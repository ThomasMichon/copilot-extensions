"""Declarative cold-store-provider discovery.

Not every session the bridge is asked for is live (see
``visions/plugins/agent-bridge`` §Concepts/*cold-store providers*). A
**cold-store provider** answers *"give me this session's content"* when the
bridge's own live session ledger has nothing registered for the requested ID.
This mirrors :mod:`agent_bridge.provider_sources` (the ``codespace:`` /
``container:`` namespace-provider registry) almost exactly, but is keyed by
**capability** rather than address **namespace**, lives in its own
``cold-store-providers.d`` manifest directory (a cold-store provider and a
namespace provider are distinct registrations, even when the same plugin
supplies both), and drives a different verb over the process boundary
(``session-fetch <session-id>`` instead of ``namespace-list`` /
``namespace-resolve``).

Each provider plugin drops a small JSON manifest into
``~/.agent-bridge/cold-store-providers.d/`` from its own ``sessionStart``
bootstrap hook; the daemon scans that directory and, for each distinct
``capability``, drives the winning provider's binstub over a process boundary
-- never by importing the provider package (the daemon's own venv/PATH cannot
see it, exactly as for namespace providers).

Manifest schema (``~/.agent-bridge/cold-store-providers.d/<name>.json``)::

    {
      "schema_version": 1,
      "plugin": "agent-logger@copilot-extensions",
      "plugin_root": "/current/installed/plugin/root",
      "capability": "session-fetch",     # required: the retrieval capability
      "command": ["/abs/agent-logger"],  # required: absolute argv prefix
      "description": "agent-logger cold-store retrieval"  # optional
    }

Schema-v1 manifests are active only while the attributed plugin is effectively
enabled globally or in an adopted project and ``plugin_root`` exactly matches
its current identity-verified root -- the same attribution rule
:mod:`agent_bridge.provider_sources` enforces. Legacy anonymous (schema 0)
manifests remain loadable with an advisory during their compatibility window.

agent-bridge invokes ``<command...> session-fetch <session-id> --json`` to
resolve a single session's metadata + event content on demand. See
:mod:`agent_bridge.cold_store` for the process-boundary client that drives
this verb and maps its exit-code contract.
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

from .install_paths import effective_config_dir, normalized_path

REGISTRY_NAME = "cold-store-providers.d"

#: Environment override for the cold-store-provider manifest directory (tests
#: use it for hermetic isolation; also an operator escape hatch).
COLD_STORE_PROVIDERS_DIR_ENV = "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR"


def cold_store_providers_dir() -> Path:
    """Resolve the ``cold-store-providers.d`` directory (does not create it)."""
    override = os.environ.get(COLD_STORE_PROVIDERS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return effective_config_dir() / "cold-store-providers.d"


def _registry_targets_current_install(root: Path) -> bool:
    if root.name != REGISTRY_NAME:
        return True
    return normalized_path(root.parent) == normalized_path(effective_config_dir())


@dataclass(frozen=True)
class ColdStoreManifest:
    """A validated cold-store-provider drop-in manifest."""

    capability: str
    command: tuple[str, ...]
    description: str = ""
    source_path: str = ""
    schema_version: int = 0
    plugin: str | None = None
    plugin_root: str | None = None


class ManifestError(ValueError):
    """A cold-store-provider manifest was structurally invalid."""


class TargetUnusableError(ValueError):
    """A cold-store-provider target exists but cannot satisfy its contract."""


def parse_manifest(data: object, *, source_path: str = "") -> ColdStoreManifest:
    """Build a :class:`ColdStoreManifest` from parsed JSON.

    Raises :class:`ManifestError` on any structural problem so the caller can
    skip a single bad manifest without aborting discovery.
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest root must be a JSON object")

    capability = data.get("capability")
    if not isinstance(capability, str) or not capability.strip():
        raise ManifestError("`capability` is required and must be a non-empty string")
    capability = capability.strip()

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

    return ColdStoreManifest(
        capability=capability,
        command=tuple(cmd),
        description=desc,
        source_path=source_path,
        schema_version=schema_version,
        plugin=plugin.strip() if isinstance(plugin, str) else None,
        plugin_root=plugin_root.strip() if isinstance(plugin_root, str) else None,
    )


@dataclass(frozen=True)
class ColdStoreRegistryReport:
    """Cold-store-provider scan plus its reconciled entries and winners."""

    snapshot: ScanSnapshot[ColdStoreManifest]
    entries: Mapping[str, ColdStoreManifest]
    manifests: Mapping[str, ColdStoreManifest]
    findings: tuple[Finding, ...]


def _inactive(
    path: Path,
    reason: str,
    *,
    target: str | None = None,
    detail: str | None = None,
    owner: str | None = None,
) -> EntryDecision[ColdStoreManifest]:
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
) -> EntryDecision[ColdStoreManifest]:
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
    manifest: ColdStoreManifest,
    activation: ActivationReport,
) -> EntryDecision[ColdStoreManifest]:
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

    expected_roots = {selected.root for selected in decision.value.live_roots}
    if Path(manifest.plugin_root or "") not in expected_roots:
        return _inactive(
            path,
            "identity-mismatch",
            target=manifest.plugin_root,
            owner=source,
            detail=(
                "provider root differs from authoritative live plugin roots "
                + ", ".join(str(root) for root in sorted(expected_roots))
            ),
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
) -> EntryDecision[ColdStoreManifest]:
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
        manifest = ColdStoreManifest(
            capability=manifest.capability,
            command=command,
            description=manifest.description,
            source_path=manifest.source_path,
            schema_version=manifest.schema_version,
            plugin=manifest.plugin,
            plugin_root=canonical_root,
        )
        return _classify_attribution(path, manifest, activation_report())

    manifest = ColdStoreManifest(
        capability=manifest.capability,
        command=command,
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


def scan_cold_store_registry(
    directory: str | os.PathLike[str] | None = None,
    *,
    previous: Mapping[str, ColdStoreManifest] | None = None,
    activation_report: ActivationReport | None = None,
) -> ColdStoreRegistryReport:
    """Scan, reconcile, and de-duplicate cold-store-provider manifests."""
    root = Path(directory) if directory is not None else cold_store_providers_dir()
    if not _registry_targets_current_install(root):
        detail = (
            "cold-store-provider registry targets a different agent-bridge "
            f"installation: expected {effective_config_dir() / REGISTRY_NAME}"
        )
        return ColdStoreRegistryReport(
            snapshot=ScanSnapshot(
                registry=REGISTRY_NAME,
                authority=ScanAuthority.COMPLETE,
                decisions={},
                findings=(),
            ),
            entries={},
            manifests={},
            findings=(
                Finding(
                    registry=REGISTRY_NAME,
                    entry=str(root),
                    status="inactive",
                    reason="bridge-install-mismatch",
                    target=str(root),
                    owner=None,
                    remedy="Run `agent-bridge doctor` from the intended installation.",
                    detail=detail,
                ),
            ),
        )
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
    manifests: dict[str, ColdStoreManifest] = {}
    findings = list(snapshot.findings)
    for entry, manifest in sorted(entries.items()):
        prior = manifests.get(manifest.capability)
        if prior is None:
            manifests[manifest.capability] = manifest
            continue
        findings.append(
            Finding(
                registry=REGISTRY_NAME,
                entry=entry,
                status="inactive",
                reason="duplicate",
                target=manifest.capability,
                owner=manifest.plugin,
                remedy=f"Remove {entry} or the conflicting {prior.source_path}.",
                detail=f"capability already claimed by {prior.source_path}",
            )
        )
    return ColdStoreRegistryReport(
        snapshot=snapshot,
        entries=entries,
        manifests=manifests,
        findings=tuple(findings),
    )


def discover_cold_store_manifests(
    directory: str | os.PathLike[str] | None = None,
) -> dict[str, ColdStoreManifest]:
    """Compatibility wrapper returning the active capability map."""
    return dict(scan_cold_store_registry(directory).manifests)
