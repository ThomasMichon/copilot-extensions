"""Declarative namespace-provider discovery.

agent-bridge sources external agent *providers* (codespaces, containers, ...)
from a filesystem manifest registry instead of hardcoded imports / PATH probes.
Each provider plugin drops a small JSON manifest into
``~/.agent-bridge/providers.d/`` from its own sessionStart bootstrap hook; the
daemon scans that directory and registers a namespace resolver per manifest,
driving the provider's binstub over a process boundary.

Why a filesystem registry (mirrors the agent-worktrees *pivot* registry): the
daemon runs from its own isolated versioned venv and service PATH, where a
provider package is neither importable nor on ``PATH``. A manifest carries an
**absolute** command (resolved by the provider's own bootstrap hook, which *can*
find its binstub), so the daemon never depends on importing the provider or on
its ``PATH``. Providers self-register merely by dropping a manifest -- no
imperative "register" call, no TTL, always freshly enumerated on demand.

Robustness follows the suite drop-in registry contract: every entry is an
independent fault boundary, missing commands are detected during discovery, and
only an authoritative scan withdraws a prior provider. Findings feed both
bounded daemon warnings and ``agent-bridge doctor``.

Manifest schema (``~/.agent-bridge/providers.d/<name>.json``)::

    {
      "schema_version": 1,
      "plugin": "agent-codespaces@copilot-extensions",
      "plugin_root": "/current/installed/plugin/root",
      "namespace": "codespace",          # required: the ``<prefix>:`` it serves
      "command": ["/abs/agent-codespaces"],  # required: absolute argv prefix
      "restricted": false,                # optional: venues lack cross-repo/inject
      "description": "GitHub Codespaces"  # optional: human label
    }

Schema-v1 manifests are active only while the attributed plugin is effectively
enabled globally or in an adopted project and ``plugin_root`` exactly matches
its current identity-verified root. Legacy anonymous manifests remain loadable
with an advisory during their compatibility window.

agent-bridge invokes ``<command...> namespace-list`` /
``<command...> namespace-resolve <name>`` (etc.) to source and resolve the
provider's agents on demand.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    scan_directory,
)
from plugin_activation import ActivationReport, resolve_active_plugins

REGISTRY_NAME = "providers.d"

#: Environment override for the provider-manifest directory (tests use it for
#: hermetic isolation; also an operator escape hatch).
PROVIDERS_DIR_ENV = "AGENT_BRIDGE_PROVIDERS_DIR"

#: Environment override for the agent-bridge config dir (shared with the rest of
#: the daemon; ``providers.d`` lives beneath it).
_CONFIG_DIR_ENV = "AGENT_BRIDGE_CONFIG_DIR"


def providers_dir() -> Path:
    """Resolve the ``providers.d`` directory (does not create it)."""
    override = os.environ.get(PROVIDERS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    config_dir = Path(
        os.environ.get(_CONFIG_DIR_ENV, "~/.agent-bridge")
    ).expanduser()
    return config_dir / "providers.d"


@dataclass(frozen=True)
class ProviderManifest:
    """A validated namespace-provider drop-in manifest."""

    namespace: str
    command: tuple[str, ...]
    restricted: bool = False
    description: str = ""
    source_path: str = ""
    schema_version: int = 0
    plugin: str | None = None
    plugin_root: str | None = None


class ManifestError(ValueError):
    """A provider manifest was structurally invalid."""


class TargetUnusableError(ValueError):
    """A provider target exists but cannot satisfy its contract."""


def parse_manifest(data: object, *, source_path: str = "") -> ProviderManifest:
    """Build a :class:`ProviderManifest` from parsed JSON.

    Raises :class:`ManifestError` on any structural problem so the caller can
    skip a single bad manifest without aborting discovery.
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    ns = data.get("namespace")
    if not isinstance(ns, str) or not ns.strip():
        raise ManifestError("`namespace` is required and must be a non-empty string")
    ns = ns.strip().rstrip(":")

    cmd = data.get("command")
    if (
        not isinstance(cmd, list)
        or not cmd
        or not all(isinstance(x, str) and x for x in cmd)
    ):
        raise ManifestError("`command` must be a non-empty array of strings")

    desc = data.get("description", "")
    if not isinstance(desc, str):
        raise ManifestError("`description` must be a string when present")

    restricted = data.get("restricted", False)
    if not isinstance(restricted, bool):
        raise ManifestError("`restricted` must be a JSON boolean when present")

    schema_version = data.get("schema_version", 0)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version not in (0, 1)
    ):
        raise ManifestError("`schema_version` must be 0 or 1")

    plugin = data.get("plugin")
    plugin_root = data.get("plugin_root")
    if schema_version == 1:
        if not isinstance(plugin, str) or "@" not in plugin or not plugin.strip():
            raise ManifestError("schema v1 requires canonical `plugin` name@marketplace")
        if not isinstance(plugin_root, str) or not plugin_root.strip():
            raise ManifestError("schema v1 requires non-empty `plugin_root`")
    elif plugin is not None or plugin_root is not None:
        raise ManifestError("`plugin`/`plugin_root` require schema_version 1")

    return ProviderManifest(
        namespace=ns,
        command=tuple(cmd),
        restricted=restricted,
        description=desc,
        source_path=source_path,
        schema_version=schema_version,
        plugin=plugin.strip() if isinstance(plugin, str) else None,
        plugin_root=plugin_root.strip() if isinstance(plugin_root, str) else None,
    )


@dataclass(frozen=True)
class ProviderRegistryReport:
    """Provider scan plus its reconciled entries and namespace winners."""

    snapshot: ScanSnapshot[ProviderManifest]
    entries: Mapping[str, ProviderManifest]
    manifests: Mapping[str, ProviderManifest]
    findings: tuple[Finding, ...]


def _inactive(
    path: Path,
    reason: str,
    *,
    target: str | None = None,
    detail: str | None = None,
    owner: str | None = None,
) -> EntryDecision[ProviderManifest]:
    return EntryDecision.inactive(
        Finding(
            registry=REGISTRY_NAME,
            entry=str(path),
            status="inactive",
            reason=reason,
            target=target,
            owner=owner,
            remedy=(
                f"Run `agent-bridge doctor`; remove {path} or "
                "reinstall/re-enable its provider."
            ),
            detail=detail,
        )
    )


def _indeterminate(
    path: Path,
    *,
    target: str | None = None,
    detail: str | None = None,
    owner: str | None = None,
) -> EntryDecision[ProviderManifest]:
    return EntryDecision.indeterminate(
        Finding(
            registry=REGISTRY_NAME,
            entry=str(path),
            status="indeterminate",
            reason="entry-indeterminate",
            target=target,
            owner=owner,
            remedy="Retry `agent-bridge doctor` after plugin settings are readable.",
            detail=detail,
        )
    )


def _resolve_command(command: tuple[str, ...]) -> tuple[str, ...]:
    first = command[0]
    candidate = Path(first).expanduser()
    has_path = candidate.is_absolute() or candidate.parent != Path(".")
    resolved = str(candidate) if has_path else shutil.which(first)
    if not resolved:
        raise FileNotFoundError(first)
    target = Path(resolved)
    info = target.stat()
    if not stat.S_ISREG(info.st_mode):
        raise TargetUnusableError("provider command is not a regular file")
    if os.name != "nt" and not os.access(target, os.X_OK):
        raise TargetUnusableError("provider command is not executable")
    return (str(target), *command[1:])


def _classify_attribution(
    path: Path,
    manifest: ProviderManifest,
    activation: ActivationReport,
) -> EntryDecision[ProviderManifest]:
    source = manifest.plugin or ""
    if activation.authority is ScanAuthority.INDETERMINATE:
        return _indeterminate(
            path,
            target=manifest.plugin_root,
            owner=source,
            detail="effective plugin activation evidence is indeterminate",
        )

    decision = activation.decisions.get(source)
    if decision is None:
        return _inactive(
            path,
            "not-enabled",
            target=manifest.plugin_root,
            owner=source,
            detail="plugin is not enabled globally or in any registered project",
        )
    if decision.status is EntryStatus.INDETERMINATE:
        detail = "; ".join(
            finding.detail or finding.reason for finding in decision.findings
        )
        return _indeterminate(
            path,
            target=manifest.plugin_root,
            owner=source,
            detail=detail or "plugin root eligibility is indeterminate",
        )
    if decision.status is EntryStatus.INACTIVE or decision.value is None:
        finding = decision.findings[0]
        return _inactive(
            path,
            finding.reason,
            target=finding.target or manifest.plugin_root,
            owner=source,
            detail=finding.detail,
        )

    expected_root = decision.value.root
    if Path(manifest.plugin_root or "") != expected_root:
        return _inactive(
            path,
            "identity-mismatch",
            target=manifest.plugin_root,
            owner=source,
            detail=f"provider root differs from active plugin root {expected_root}",
        )
    if decision.status is EntryStatus.ACTIVE_WITH_ADVISORY:
        advisories = tuple(
            Finding(
                registry=REGISTRY_NAME,
                entry=str(path),
                status="advisory",
                reason=finding.reason,
                target=finding.target,
                owner=source,
                remedy="Run `agent-bridge doctor` and repair the plugin source.",
                detail=finding.detail,
            )
            for finding in decision.findings
        )
        return EntryDecision.advisory(manifest, *advisories)
    return EntryDecision.active(manifest)


def _classify_manifest(
    path: Path,
    activation_report: Callable[[], ActivationReport],
) -> EntryDecision[ProviderManifest]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        manifest = parse_manifest(data, source_path=str(path))
    except (json.JSONDecodeError, UnicodeDecodeError, ManifestError) as exc:
        return _inactive(path, "invalid-entry", detail=str(exc))

    try:
        command = _resolve_command(manifest.command)
    except FileNotFoundError:
        return _inactive(
            path,
            "missing-target",
            target=manifest.command[0],
            owner=manifest.plugin,
        )
    except TargetUnusableError as exc:
        return _inactive(
            path,
            "target-unusable",
            target=manifest.command[0],
            detail=str(exc),
            owner=manifest.plugin,
        )

    if manifest.schema_version == 1:
        root = Path(manifest.plugin_root or "").expanduser()
        try:
            root_info = root.stat()
            if not stat.S_ISDIR(root_info.st_mode):
                raise TargetUnusableError("plugin_root is not a directory")
            canonical_root = str(root.resolve(strict=True))
        except FileNotFoundError as exc:
            return _inactive(
                path,
                "missing-target",
                target=str(root),
                detail=str(exc),
                owner=manifest.plugin,
            )
        except TargetUnusableError as exc:
            return _inactive(
                path,
                "target-unusable",
                target=str(root),
                detail=str(exc),
                owner=manifest.plugin,
            )
        manifest = ProviderManifest(
            namespace=manifest.namespace,
            command=command,
            restricted=manifest.restricted,
            description=manifest.description,
            source_path=manifest.source_path,
            schema_version=manifest.schema_version,
            plugin=manifest.plugin,
            plugin_root=canonical_root,
        )
        return _classify_attribution(path, manifest, activation_report())

    manifest = ProviderManifest(
        namespace=manifest.namespace,
        command=command,
        restricted=manifest.restricted,
        description=manifest.description,
        source_path=manifest.source_path,
    )
    return EntryDecision.advisory(
        manifest,
        Finding(
            registry=REGISTRY_NAME,
            entry=str(path),
            status="advisory",
            reason="legacy-unattributed",
            target=command[0],
            remedy="Re-run the provider plugin's sessionStart registration hook.",
        ),
    )


def scan_provider_registry(
    directory: str | os.PathLike[str] | None = None,
    *,
    previous: Mapping[str, ProviderManifest] | None = None,
    activation_report: ActivationReport | None = None,
) -> ProviderRegistryReport:
    """Scan, reconcile, and de-duplicate provider manifests."""
    root = Path(directory) if directory is not None else providers_dir()
    resolved_activation = activation_report

    def current_activation() -> ActivationReport:
        nonlocal resolved_activation
        if resolved_activation is None:
            resolved_activation = resolve_active_plugins()
        return resolved_activation

    snapshot = scan_directory(
        root,
        lambda path: _classify_manifest(path, current_activation),
        registry=REGISTRY_NAME,
        suffixes=(".json",),
    )
    entries = snapshot.reconcile(previous)
    manifests: dict[str, ProviderManifest] = {}
    findings = list(snapshot.findings)
    for entry, manifest in sorted(entries.items()):
        prior = manifests.get(manifest.namespace)
        if prior is None:
            manifests[manifest.namespace] = manifest
            continue
        findings.append(
            Finding(
                registry=REGISTRY_NAME,
                entry=entry,
                status="inactive",
                reason="duplicate",
                target=manifest.namespace,
                owner=manifest.plugin,
                remedy=f"Remove {entry} or the conflicting {prior.source_path}.",
                detail=f"namespace already claimed by {prior.source_path}",
            )
        )
    return ProviderRegistryReport(
        snapshot=snapshot,
        entries=entries,
        manifests=manifests,
        findings=tuple(findings),
    )


def discover_provider_manifests(
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, ProviderManifest]:
    """Compatibility wrapper returning the active namespace map."""
    return dict(scan_provider_registry(directory).manifests)
