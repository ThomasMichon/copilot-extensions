"""Configuration loading and validation for agent-codespaces.

Most repos need no config: agent-codespaces derives machine/location defaults,
the ``/workspaces/<basename>`` checkout, and the git-credential relay by
convention. Supplementary, CodeSpace-specific config can live **in the adopting
repo** at the canonical ``.agent-codespaces/config.yaml`` or be exposed by an
active plugin's ``codespaceConfig`` manifest declaration. The legacy repo-root
``codespaces.yaml`` and user-level ``config.d`` providers remain compatibility
inputs. Provider declarations merge below adopted-repo and current-repo config.
On every start/reload the service reads each source live and merges in memory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import yaml
from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    WarningTracker,
    scan_directory,
)
from plugin_activation import ActivationReport, ActivePlugin, resolve_active_plugins

log = logging.getLogger("agent-codespaces")


def _home() -> Path:
    """Root under which agent-codespaces state lives, with a sandbox override.

    ``AGENT_HOME`` (when set) replaces ``~/`` as the state root -- the same
    fabric-wide override agent-worktrees honors -- so an isolated test deployment
    relocates ``~/.agent-codespaces`` (leases, sockets, logs) without touching the
    real home (``gh``/``ssh``/git auth still resolve from the actual ``~/``).
    Read at import so a freshly-spawned ``agent-codespaces`` subprocess inside a
    sandbox picks it up.
    """
    import os

    override = os.environ.get("AGENT_HOME", "").strip()
    return Path(override) if override else Path.home()


# Canonical paths
RUNTIME_DIR = _home() / ".agent-codespaces"
ADOPTED_REPOS_FILE = RUNTIME_DIR / "adopted-repos.yaml"
SOCKET_DIR = RUNTIME_DIR / "sockets"
LOG_FILE = RUNTIME_DIR / "agent-codespaces.log"

# In-repo config, aligned with the sibling agent-* plugins' ``.agent-<name>/``
# convention (e.g. ``.agent-worktrees/config.yaml``). This is the **canonical**
# home for a repo's CodeSpace config; it carries only the *supplementary*,
# CodeSpace-specific bits that convention can't derive (workspace_repo/split-repo
# mapping, devcontainer pin, ado_host, provision hooks). A repo that matches
# convention (machine defaults, ``/workspaces/<basename>`` checkout, git-credential
# relay) needs no file at all.
CONFIG_DIR_NAME = ".agent-codespaces"
CONFIG_FILE_IN_DIR = "config.yaml"
CANONICAL_CONFIG_REL = f"{CONFIG_DIR_NAME}/{CONFIG_FILE_IN_DIR}"

# Legacy repo-root config filename, still read as a back-compat fallback.
# ``agent-codespaces config migrate`` relocates it to CANONICAL_CONFIG_REL.
CONFIG_FILENAME = "codespaces.yaml"
NO_SUPPLEMENTAL_CONFIG_ADVISORY = (
    "No CodeSpace config found (no .agent-codespaces/config.yaml in the "
    "current repo and no adopted repos). Standard repos need none; add "
    "one only for supplementary CodeSpace-specific config."
)

# ── Supplementary config providers ──────────────────────────────────────────
# An active plugin can expose its shipped CodeSpace target config directly from
# plugin.json. Legacy/operator config.d entries remain supported, but are not
# required authority for active plugin payloads.
PLUGIN_CONFIG_MANIFEST_FIELD = "codespaceConfig"
PLUGIN_CONFIG_REGISTRY_NAME = "plugin-manifests"
CONFIG_D_DIR_NAME = "config.d"
CONFIG_D_REGISTRY_NAME = "config.d"
CONFIG_D_POINTER_SCHEMA_VERSION = 1
_MANAGED_POINTER_KEYS = frozenset(
    {"schema_version", "plugin", "plugin_root", "target"}
)
_PLUGIN_SOURCE_RE = re.compile(r"^[^@/\\\s]+@[^@/\\\s]+$")
_LEGACY_PROVIDER_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*-harness\.conf$"
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass(frozen=True)
class ConfigDropin:
    """One validated config.d contribution selected for config merging."""

    entry: Path
    target: Path
    entry_class: str
    raw_config: dict[str, Any]
    owner: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Return the active-contribution shape used by doctor JSON."""
        result = {
            "entry": str(self.entry),
            "target": str(self.target),
            "class": self.entry_class,
        }
        if self.owner:
            result["owner"] = self.owner
        return result


@dataclass(frozen=True)
class ConfigDropinRegistryReport:
    """The one config.d scan/classification result used by runtime and doctor."""

    snapshot: ScanSnapshot[ConfigDropin]
    active_entries: dict[str, ConfigDropin]
    entry_classes: dict[str, str] = field(default_factory=dict)

    @property
    def authority(self) -> ScanAuthority:
        """Whether this scan was authoritative for reconciliation."""
        return self.snapshot.authority

    @property
    def findings(self) -> tuple[Finding, ...]:
        """All current, exhaustive findings."""
        return self.snapshot.findings

    @property
    def active_configs(self) -> list[ConfigDropin]:
        """Validated target configs in deterministic registry-entry order."""
        return [
            self.active_entries[key]
            for key in sorted(self.active_entries)
        ]

    def to_dict(self) -> dict[str, Any]:
        """Render exhaustive, machine-readable registry diagnostics."""
        entries: list[dict[str, str]] = []
        for entry, decision in sorted(self.snapshot.decisions.items()):
            item = {
                "entry": entry,
                "status": decision.status.value,
                "class": self.entry_classes.get(entry, "unknown"),
            }
            if decision.value is not None:
                item["target"] = str(decision.value.target)
                if decision.value.owner:
                    item["owner"] = decision.value.owner
            entries.append(item)
        return {
            "registry": self.snapshot.registry,
            "authority": self.authority.value,
            "active_entries": [
                contribution.to_dict() for contribution in self.active_configs
            ],
            "entries": entries,
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class ConfigProviderReports:
    """Active-plugin and compatibility config-provider diagnostics."""

    active_plugins: ConfigDropinRegistryReport
    config_d: ConfigDropinRegistryReport

    @property
    def active_configs(self) -> list[ConfigDropin]:
        """Return provider configs in precedence order."""
        return [
            *self.active_plugins.active_configs,
            *self.config_d.active_configs,
        ]

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Return exhaustive findings from both provider surfaces."""
        return (
            *self.active_plugins.findings,
            *self.config_d.findings,
        )


# Config loading is normally one-shot, but a daemon can reload it while a
# registry entry is transiently unreadable. Keep only the last selected value
# per entry, and clear it on an authoritative absent scan.
_CONFIG_D_LAST_KNOWN: dict[str, ConfigDropin] = {}
_CONFIG_D_LAST_KNOWN_ROOT: Path | None = None
_CONFIG_D_WARNING_TRACKER = WarningTracker()
_PLUGIN_CONFIG_LAST_KNOWN: dict[str, ConfigDropin] = {}
_PLUGIN_CONFIG_WARNING_TRACKER = WarningTracker()


def config_d_dir() -> Path:
    """The user-level drop-in config directory (~/.agent-codespaces/config.d/)."""
    return RUNTIME_DIR / CONFIG_D_DIR_NAME


def _config_d_root_identity(directory: Path) -> Path:
    """Return the canonical identity used to scope implicit retained state."""
    try:
        return directory.expanduser().resolve(strict=False)
    except OSError:
        return Path(os.path.abspath(os.path.expanduser(str(directory))))


def _is_reparse(info: os.stat_result) -> bool:
    """Whether a Windows stat result names a reparse point."""
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _config_d_remedy(
    entry: Path,
    *,
    entry_class: str,
    owner: str | None = None,
) -> str:
    """Give report-only remediation without claiming ownership of an entry."""
    if entry_class == "managed-plugin" and owner:
        return (
            f"Re-enable or reinstall {owner}, then run its config-provider hook "
            f"to recreate {entry}."
        )
    if entry_class == "legacy-plugin":
        return (
            f"Update or re-enable the legacy provider that wrote {entry} so it "
            "rewrites this entry as schema v1; remove it only if no longer intended."
        )
    if entry_class == "operator":
        return f"Fix the operator-owned YAML at {entry}; agent-codespaces will not remove it."
    return f"Fix or remove the unrecognized entry {entry} if it is no longer intended."


def _config_d_finding(
    entry: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: Path | str | None = None,
    entry_class: str,
    owner: str | None = None,
    detail: str | None = None,
) -> Finding:
    """Create a config.d finding with a precise, report-only remedy."""
    return Finding(
        registry=CONFIG_D_REGISTRY_NAME,
        entry=str(entry),
        status=status,
        reason=reason,
        target=str(target) if target is not None else None,
        owner=owner,
        remedy=_config_d_remedy(entry, entry_class=entry_class, owner=owner),
        detail=detail,
    )


def _regular_target(
    entry: Path,
    target: Path,
    *,
    entry_class: str,
    owner: str | None,
) -> tuple[Path | None, EntryDecision[ConfigDropin] | None]:
    """Resolve a regular, non-reparse config target or return its verdict."""
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None, EntryDecision.inactive(
            _config_d_finding(
                entry,
                "missing-target",
                target=target,
                entry_class=entry_class,
                owner=owner,
            )
        )
    except OSError as exc:
        return None, EntryDecision.indeterminate(
            _config_d_finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=target,
                entry_class=entry_class,
                owner=owner,
                detail=str(exc),
            )
        )
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return None, EntryDecision.inactive(
            _config_d_finding(
                entry,
                "target-unusable",
                target=target,
                entry_class=entry_class,
                owner=owner,
                detail="target must be a regular non-reparse file",
            )
        )
    try:
        return target.resolve(strict=True), None
    except FileNotFoundError:
        return None, EntryDecision.inactive(
            _config_d_finding(
                entry,
                "missing-target",
                target=target,
                entry_class=entry_class,
                owner=owner,
            )
        )
    except OSError as exc:
        return None, EntryDecision.indeterminate(
            _config_d_finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=target,
                entry_class=entry_class,
                owner=owner,
                detail=str(exc),
            )
        )


def _validated_config_target(
    entry: Path,
    target: Path,
    *,
    entry_class: str,
    owner: str | None = None,
) -> EntryDecision[ConfigDropin]:
    """Validate a target config's file identity and non-empty YAML mapping."""
    canonical, verdict = _regular_target(
        entry, target, entry_class=entry_class, owner=owner
    )
    if verdict is not None:
        return verdict
    canonical = cast(Path, canonical)
    try:
        raw = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "invalid-entry",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=f"target config is not valid UTF-8: {exc}",
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _config_d_finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=str(exc),
            )
        )
    except yaml.YAMLError as exc:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "invalid-entry",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=f"target config is not valid YAML: {exc}",
            )
        )
    validation_error = _validate_dropin_config(raw)
    if validation_error is not None:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "invalid-entry",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=validation_error,
            )
        )
    return EntryDecision.active(
        ConfigDropin(
            entry=entry,
            target=canonical,
            entry_class=entry_class,
            raw_config=raw,
            owner=owner,
        )
    )


def _validate_dropin_provision(
    raw: object,
    *,
    location: str,
) -> str | None:
    """Validate the provision shape consumed by ``_parse_provision``."""
    if not isinstance(raw, dict):
        return f"{location} must be a mapping"
    for name in ("files", "on_connect", "on_create"):
        value = raw.get(name)
        if name in raw and not isinstance(value, list):
            return f"{location}.{name} must be a list"
    for index, file_spec in enumerate(raw.get("files", [])):
        if not isinstance(file_spec, dict):
            return f"{location}.files[{index}] must be a mapping"
        for name in ("src", "dest"):
            value = file_spec.get(name)
            if not isinstance(value, str) or not value.strip():
                return (
                    f"{location}.files[{index}].{name} must be a non-empty string"
                )
    return None


def _validate_dropin_string_list(raw: list[object], *, location: str) -> str | None:
    """Require the string lists that later merge and launch code relies on."""
    if any(not isinstance(value, str) or not value.strip() for value in raw):
        return f"{location} entries must be non-empty strings"
    return None


def _validate_dropin_config(raw: object) -> str | None:
    """Validate the mapping shapes assumed by ``load_merged_config``.

    The normal repo loader predates provider drop-ins and can surface its own
    configuration errors. A drop-in is an independently fault-isolated
    contributor, so every shape the merge path calls ``.get()``, ``.items()``,
    or iterates must be proven before it is allowed to become active.
    """
    if not isinstance(raw, dict) or not raw:
        return "target config must be a non-empty YAML mapping"

    for name in ("defaults", "credentials", "connection_owner", "repos", "provision"):
        if name in raw and not isinstance(raw[name], dict):
            return f"{name} must be a mapping"
    if "codespace_plugins" in raw and not isinstance(raw["codespace_plugins"], list):
        return "codespace_plugins must be a list"
    for index, plugin in enumerate(raw.get("codespace_plugins", [])):
        if not isinstance(plugin, dict):
            return f"codespace_plugins[{index}] must be a mapping"

    credentials = raw.get("credentials", {})
    if "feed_token_env" in credentials and not isinstance(
        credentials["feed_token_env"], list
    ):
        return "credentials.feed_token_env must be a list"
    if "feed_token_env" in credentials:
        error = _validate_dropin_string_list(
            credentials["feed_token_env"],
            location="credentials.feed_token_env",
        )
        if error is not None:
            return error
    sources = credentials.get("sources", {})
    if not isinstance(sources, dict):
        return "credentials.sources must be a mapping"
    for source_name, source in sources.items():
        if not isinstance(source_name, str) or not isinstance(source, dict):
            return "credentials.sources entries must have string names and mapping values"
        for name in ("allowed_hosts", "allowed_resources"):
            if name in source and not isinstance(source[name], list):
                return f"credentials.sources.{source_name}.{name} must be a list"
            if name in source:
                error = _validate_dropin_string_list(
                    source[name],
                    location=f"credentials.sources.{source_name}.{name}",
                )
                if error is not None:
                    return error

    connection_owner = raw.get("connection_owner", {})
    if "reconcile_interval" in connection_owner and (
        isinstance(connection_owner["reconcile_interval"], bool)
        or not isinstance(connection_owner["reconcile_interval"], (int, float))
    ):
        return "connection_owner.reconcile_interval must be a number"

    repos = raw.get("repos", {})
    for repo_name, repo in repos.items():
        if not isinstance(repo_name, str) or not isinstance(repo, dict):
            return "repos entries must have string names and mapping values"
        if "bootstrap" in repo and not isinstance(repo["bootstrap"], dict):
            return f"repos.{repo_name}.bootstrap must be a mapping"
        if "provision" in repo:
            error = _validate_dropin_provision(
                repo["provision"], location=f"repos.{repo_name}.provision"
            )
            if error is not None:
                return error

    if "provision" in raw:
        return _validate_dropin_provision(raw["provision"], location="provision")
    return None


def _plugin_config_remedy(source: str, manifest: Path) -> str:
    """Return the report-only remedy for one manifest declaration."""
    return (
        f"Fix or remove {PLUGIN_CONFIG_MANIFEST_FIELD} in {manifest} for "
        f"{source}, then update or re-enable that plugin."
    )


def _plugin_config_finding(
    source: str,
    manifest: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: Path | str | None = None,
    detail: str | None = None,
) -> Finding:
    """Create an exact active-plugin declaration finding."""
    return Finding(
        registry=PLUGIN_CONFIG_REGISTRY_NAME,
        entry=str(manifest),
        status=status,
        reason=reason,
        target=str(target) if target is not None else None,
        owner=source,
        remedy=_plugin_config_remedy(source, manifest),
        detail=detail,
    )


def _active_plugin_config_decision(
    active: ActivePlugin,
) -> EntryDecision[ConfigDropin] | None:
    """Read and validate one active plugin's optional config declaration."""
    manifest = active.root / "plugin.json"
    try:
        info = manifest.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return EntryDecision.indeterminate(
            _plugin_config_finding(
                active.source,
                manifest,
                "entry-indeterminate",
                status="indeterminate",
                detail=f"plugin manifest could not be inspected: {exc}",
            )
        )
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
    ):
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "invalid-entry",
                detail="plugin.json must be a regular non-reparse file",
            )
        )
    try:
        raw_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "invalid-entry",
                detail=f"plugin.json is not valid UTF-8: {exc}",
            )
        )
    except json.JSONDecodeError as exc:
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "invalid-entry",
                detail=f"plugin.json is not valid JSON: {exc}",
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _plugin_config_finding(
                active.source,
                manifest,
                "entry-indeterminate",
                status="indeterminate",
                detail=f"plugin.json could not be read: {exc}",
            )
        )
    if not isinstance(raw_manifest, dict):
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "invalid-entry",
                detail="plugin.json must contain a JSON object",
            )
        )
    if PLUGIN_CONFIG_MANIFEST_FIELD not in raw_manifest:
        return None

    declared = raw_manifest.get(PLUGIN_CONFIG_MANIFEST_FIELD)
    if not isinstance(declared, str) or not declared.strip():
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "invalid-entry",
                target=str(declared),
                detail=f"{PLUGIN_CONFIG_MANIFEST_FIELD} must be a non-empty relative path",
            )
        )
    relative = Path(declared.strip())
    if relative.is_absolute():
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "identity-mismatch",
                target=relative,
                detail=f"{PLUGIN_CONFIG_MANIFEST_FIELD} must be relative to the plugin root",
            )
        )
    target = active.root / relative
    canonical_target, verdict = _regular_target(
        manifest,
        target,
        entry_class="active-plugin",
        owner=active.source,
    )
    if verdict is not None:
        finding = verdict.findings[0]
        replacement = _plugin_config_finding(
            active.source,
            manifest,
            finding.reason,
            status=finding.status,
            target=target,
            detail=finding.detail,
        )
        if verdict.status is EntryStatus.INDETERMINATE:
            return EntryDecision.indeterminate(replacement)
        return EntryDecision.inactive(replacement)
    canonical_target = cast(Path, canonical_target)
    try:
        canonical_target.relative_to(active.root)
    except ValueError:
        return EntryDecision.inactive(
            _plugin_config_finding(
                active.source,
                manifest,
                "identity-mismatch",
                target=canonical_target,
                detail="declared config escapes the identity-verified plugin root",
            )
        )

    validated = _validated_config_target(
        manifest,
        canonical_target,
        entry_class="active-plugin",
        owner=active.source,
    )
    if validated.status in (EntryStatus.ACTIVE, EntryStatus.ACTIVE_WITH_ADVISORY):
        contribution = cast(ConfigDropin, validated.value)
        return EntryDecision.active(replace(contribution, entry=manifest))
    finding = validated.findings[0]
    replacement = _plugin_config_finding(
        active.source,
        manifest,
        finding.reason,
        status=finding.status,
        target=canonical_target,
        detail=finding.detail,
    )
    if validated.status is EntryStatus.INDETERMINATE:
        return EntryDecision.indeterminate(replacement)
    return EntryDecision.inactive(replacement)


def scan_active_plugin_config_registry(
    *,
    previous: dict[str, ConfigDropin] | None = None,
) -> ConfigDropinRegistryReport:
    """Resolve supplementary configs declared by currently active plugins."""
    global _PLUGIN_CONFIG_LAST_KNOWN

    activation = resolve_active_plugins()
    decisions: dict[str, EntryDecision[ConfigDropin]] = {}
    entry_classes: dict[str, str] = {}
    findings: list[Finding] = []

    for source, activation_decision in sorted(activation.decisions.items()):
        if activation_decision.status is EntryStatus.INDETERMINATE:
            manifest = (
                activation_decision.value.root / "plugin.json"
                if activation_decision.value is not None
                else Path(f"<active-plugin:{source}>")
            )
            decision = EntryDecision.indeterminate(
                _plugin_config_finding(
                    source,
                    manifest,
                    "entry-indeterminate",
                    status="indeterminate",
                    detail="plugin activation could not be determined authoritatively",
                )
            )
        elif activation_decision.status is EntryStatus.INACTIVE:
            continue
        else:
            active = cast(ActivePlugin, activation_decision.value)
            decision = _active_plugin_config_decision(active)
            if decision is None:
                continue
        decisions[source] = decision
        entry_classes[source] = "active-plugin"
        findings.extend(decision.findings)

    if activation.authority is ScanAuthority.INDETERMINATE:
        findings.append(Finding(
            registry=PLUGIN_CONFIG_REGISTRY_NAME,
            entry=PLUGIN_CONFIG_REGISTRY_NAME,
            status="indeterminate",
            reason="registry-indeterminate",
            remedy=(
                "Restore readable plugin activation settings and payload roots, "
                "then run `agent-codespaces doctor` again; current declarations "
                "are retained."
            ),
            detail="active plugins could not be enumerated authoritatively",
        ))

    snapshot = ScanSnapshot(
        registry=PLUGIN_CONFIG_REGISTRY_NAME,
        authority=activation.authority,
        decisions=decisions,
        findings=tuple(findings),
    )
    prior = dict(_PLUGIN_CONFIG_LAST_KNOWN if previous is None else previous)
    active_entries = snapshot.reconcile(prior)
    if previous is None:
        _PLUGIN_CONFIG_LAST_KNOWN = dict(active_entries)
    return ConfigDropinRegistryReport(
        snapshot=snapshot,
        active_entries=active_entries,
        entry_classes=entry_classes,
    )


def _legacy_target(entry: Path, text: str) -> Path | None:
    """Recognize only the documented pre-v1 harness pointer shape."""
    if not _LEGACY_PROVIDER_RE.fullmatch(entry.name):
        return None
    candidates = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(candidates) != 1:
        return None
    target = Path(candidates[0]).expanduser()
    if not target.is_absolute():
        return None
    if (
        target.name != CONFIG_FILE_IN_DIR
        or target.parent.name != CONFIG_DIR_NAME.removeprefix(".")
        or target.parent.parent.name != "references"
    ):
        return None
    return target


def _activation_indeterminate_finding(
    entry: Path,
    *,
    source: str,
    target: Path,
    report: ActivationReport,
) -> Finding:
    """Translate shared activation uncertainty to this registry's entry."""
    details = [
        finding.detail or finding.reason
        for finding in report.findings
        if finding.owner in {None, source} or finding.target == source
    ]
    detail = "plugin eligibility could not be determined"
    if details:
        detail = f"{detail}: {'; '.join(details[:2])}"
    return _config_d_finding(
        entry,
        "entry-indeterminate",
        status="indeterminate",
        target=target,
        entry_class="managed-plugin",
        owner=source,
        detail=detail,
    )


def _managed_pointer_decision(
    entry: Path,
    data: object,
    *,
    activation_resolver: Callable[[], ActivationReport],
) -> EntryDecision[ConfigDropin]:
    """Classify an attributed v1 pointer against effective plugin activation."""
    if (
        not isinstance(data, dict)
        or set(data) != _MANAGED_POINTER_KEYS
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != CONFIG_D_POINTER_SCHEMA_VERSION
        or not all(
            isinstance(data.get(key), str) and data[key].strip()
            for key in ("plugin", "plugin_root", "target")
        )
    ):
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "invalid-entry",
                entry_class="managed-plugin",
                detail=(
                    "managed pointers require exactly schema_version, plugin, "
                    "plugin_root, and target (schema_version=1)"
                ),
            )
        )

    source = data["plugin"].strip()
    raw_root = data["plugin_root"].strip()
    raw_target = data["target"].strip()
    if not _PLUGIN_SOURCE_RE.fullmatch(source):
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "invalid-entry",
                target=raw_target,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin must be an exact name@marketplace identity",
            )
        )
    stored_root = Path(raw_root).expanduser()
    target = Path(raw_target).expanduser()
    if not stored_root.is_absolute() or not target.is_absolute():
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "invalid-entry",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin_root and target must be absolute paths",
            )
        )

    activation_report = activation_resolver()
    source_decision = activation_report.decisions.get(source)
    if (
        activation_report.authority is ScanAuthority.INDETERMINATE
        or (
            source_decision is not None
            and source_decision.status is EntryStatus.INDETERMINATE
        )
    ):
        return EntryDecision.indeterminate(
            _activation_indeterminate_finding(
                entry,
                source=source,
                target=target,
                report=activation_report,
            )
        )
    if source_decision is None:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "not-enabled",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin is not effectively enabled in global or adopted-project scope",
            )
        )
    if source_decision.status is EntryStatus.INACTIVE:
        finding = next(
            (f for f in source_decision.findings if f.status != "indeterminate"),
            None,
        )
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                finding.reason if finding else "identity-mismatch",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    finding.detail
                    if finding and finding.detail
                    else "plugin source is no longer available at its verified root"
                ),
            )
        )

    active = cast(ActivePlugin, source_decision.value)
    try:
        canonical_stored_root = stored_root.resolve(strict=True)
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "identity-mismatch",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=f"pointer plugin_root is not the current verified root: {exc}",
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _config_d_finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=f"pointer plugin_root could not be read: {exc}",
            )
        )
    if canonical_stored_root != active.root:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "identity-mismatch",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    "pointer plugin_root differs from the current identity-verified "
                    f"root ({active.root})"
                ),
            )
        )

    canonical_target, verdict = _regular_target(
        entry, target, entry_class="managed-plugin", owner=source
    )
    if verdict is not None:
        return verdict
    canonical_target = cast(Path, canonical_target)
    try:
        canonical_target.relative_to(active.root)
    except ValueError:
        return EntryDecision.inactive(
            _config_d_finding(
                entry,
                "identity-mismatch",
                target=canonical_target,
                entry_class="managed-plugin",
                owner=source,
                detail="target escapes the identity-verified plugin root",
            )
        )
    return _validated_config_target(
        entry,
        canonical_target,
        entry_class="managed-plugin",
        owner=source,
    )


def _withdraw_confirmed_disappearances(
    snapshot: ScanSnapshot[ConfigDropin],
) -> ScanSnapshot[ConfigDropin]:
    """Omit entries confirmed absent after a complete directory enumeration."""
    if snapshot.authority is not ScanAuthority.COMPLETE:
        return snapshot
    decisions = dict(snapshot.decisions)
    findings = list(snapshot.findings)
    for key, decision in tuple(decisions.items()):
        if decision.status is not EntryStatus.INDETERMINATE:
            continue
        try:
            Path(key).lstat()
        except FileNotFoundError:
            del decisions[key]
            findings = [finding for finding in findings if finding.entry != key]
        except OSError:
            continue
    return ScanSnapshot(
        registry=snapshot.registry,
        authority=snapshot.authority,
        decisions=decisions,
        findings=tuple(findings),
    )


def scan_config_dropin_registry(
    *,
    previous: dict[str, ConfigDropin] | None = None,
) -> ConfigDropinRegistryReport:
    """Scan, classify, and reconcile config.d without silent stale authority."""
    global _CONFIG_D_LAST_KNOWN, _CONFIG_D_LAST_KNOWN_ROOT

    entry_classes: dict[str, str] = {}
    activation: ActivationReport | None = None
    directory = config_d_dir()

    def activation_report() -> ActivationReport:
        nonlocal activation
        if activation is None:
            activation = resolve_active_plugins()
        return activation

    def classify(entry: Path) -> EntryDecision[ConfigDropin]:
        entry_key = str(entry)
        if entry.suffix.lower() in {".yaml", ".yml"}:
            entry_classes[entry_key] = "operator"
            return _validated_config_target(
                entry, entry, entry_class="operator"
            )
        try:
            text = entry.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            entry_classes[entry_key] = "unknown"
            return EntryDecision.inactive(
                _config_d_finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail=f"pointer is not valid UTF-8: {exc}",
                )
            )
        except OSError as exc:
            entry_classes[entry_key] = "unknown"
            return EntryDecision.indeterminate(
                _config_d_finding(
                    entry,
                    "entry-indeterminate",
                    status="indeterminate",
                    entry_class="unknown",
                    detail=str(exc),
                )
            )

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            entry_classes[entry_key] = "managed-plugin"
            return _managed_pointer_decision(
                entry, parsed, activation_resolver=activation_report
            )

        target = _legacy_target(entry, text)
        if target is None:
            entry_classes[entry_key] = "unknown"
            return EntryDecision.inactive(
                _config_d_finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail=(
                        "entry is neither an operator YAML fragment nor a schema v1 "
                        "managed pointer nor the documented legacy harness pointer"
                    ),
                )
            )
        entry_classes[entry_key] = "legacy-plugin"
        decision = _validated_config_target(
            entry,
            target,
            entry_class="legacy-plugin",
            owner=entry.stem,
        )
        if decision.status is EntryStatus.ACTIVE:
            contribution = cast(ConfigDropin, decision.value)
            return EntryDecision.advisory(
                contribution,
                _config_d_finding(
                    entry,
                    "legacy-unattributed",
                    status="active-with-advisory",
                    target=contribution.target,
                    entry_class="legacy-plugin",
                    owner=entry.stem,
                    detail="legacy provider pointer remains active during compatibility migration",
                ),
            )
        return decision

    snapshot = scan_directory(
        directory,
        classify,
        registry=CONFIG_D_REGISTRY_NAME,
    )
    snapshot = _withdraw_confirmed_disappearances(snapshot)
    snapshot = _add_config_d_remedies(snapshot, entry_classes, directory)

    if previous is None:
        root_identity = _config_d_root_identity(directory)
        prior = dict(
            _CONFIG_D_LAST_KNOWN
            if _CONFIG_D_LAST_KNOWN_ROOT == root_identity
            else {}
        )
    else:
        root_identity = None
        prior = dict(previous)
    reconciled_entries = snapshot.reconcile(prior)
    active_entries = dict(reconciled_entries)

    # Config fragments are target-exclusive. Reconcile first so a retained
    # indeterminate entry competes with newly active entries, then choose one
    # stable winner per canonical target for runtime and doctor alike.
    decisions = dict(snapshot.decisions)
    findings = list(snapshot.findings)
    seen_targets: dict[Path, str] = {}
    for key in sorted(active_entries):
        contribution = active_entries[key]
        winner = seen_targets.get(contribution.target)
        if winner is None:
            seen_targets[contribution.target] = key
            continue
        active_entries.pop(key)
        decision = decisions.get(key)
        if decision is None or decision.status is EntryStatus.INDETERMINATE:
            continue
        duplicate = _config_d_finding(
            Path(key),
            "duplicate",
            target=contribution.target,
            entry_class=entry_classes.get(key, "unknown"),
            owner=contribution.owner,
            detail=f"same target is already supplied by {winner}",
        )
        decisions[key] = EntryDecision.inactive(duplicate)
        findings = [finding for finding in findings if finding.entry != key]
        findings.append(duplicate)
    snapshot = ScanSnapshot(
        registry=snapshot.registry,
        authority=snapshot.authority,
        decisions=decisions,
        findings=tuple(findings),
    )

    if previous is None:
        _CONFIG_D_LAST_KNOWN = reconciled_entries
        _CONFIG_D_LAST_KNOWN_ROOT = root_identity
    return ConfigDropinRegistryReport(
        snapshot=snapshot,
        active_entries=active_entries,
        entry_classes=entry_classes,
    )


def _shadow_config_d_with_active_plugins(
    report: ConfigDropinRegistryReport,
    active_plugins: ConfigDropinRegistryReport,
) -> ConfigDropinRegistryReport:
    """Make valid active-plugin declarations authoritative over old pointers."""
    authoritative = {
        contribution.owner
        for contribution in active_plugins.active_configs
        if contribution.owner
    }
    if not authoritative:
        return report

    active_entries = dict(report.active_entries)
    decisions = dict(report.snapshot.decisions)
    findings = list(report.findings)
    for key, contribution in tuple(active_entries.items()):
        if contribution.owner not in authoritative:
            continue
        active_entries.pop(key)
        superseded = _config_d_finding(
            contribution.entry,
            "superseded",
            status="active-with-advisory",
            target=contribution.target,
            entry_class=contribution.entry_class,
            owner=contribution.owner,
            detail=(
                f"{contribution.owner} now declares "
                f"{PLUGIN_CONFIG_MANIFEST_FIELD} in its active plugin.json; "
                "this compatibility pointer is ignored"
            ),
        )
        decisions[key] = EntryDecision.advisory(contribution, superseded)
        findings = [finding for finding in findings if finding.entry != key]
        findings.append(superseded)
    snapshot = ScanSnapshot(
        registry=report.snapshot.registry,
        authority=report.snapshot.authority,
        decisions=decisions,
        findings=tuple(findings),
    )
    return ConfigDropinRegistryReport(
        snapshot=snapshot,
        active_entries=active_entries,
        entry_classes=report.entry_classes,
    )


def scan_config_providers() -> ConfigProviderReports:
    """Scan active plugin declarations and compatibility config.d entries."""
    active_plugins = scan_active_plugin_config_registry()
    config_d = _shadow_config_d_with_active_plugins(
        scan_config_dropin_registry(),
        active_plugins,
    )
    return ConfigProviderReports(
        active_plugins=active_plugins,
        config_d=config_d,
    )


def _warn_active_plugin_config_findings(
    report: ConfigDropinRegistryReport,
) -> None:
    """Emit bounded declaration findings during config loading."""
    batch = _PLUGIN_CONFIG_WARNING_TRACKER.select(report.findings)
    for finding in batch.emitted:
        target = f" target={finding.target}" if finding.target else ""
        log.warning(
            "%s plugin=%s manifest=%s reason=%s%s; "
            "run `agent-codespaces doctor`",
            PLUGIN_CONFIG_REGISTRY_NAME,
            finding.owner or "unknown",
            finding.entry,
            finding.reason,
            target,
        )
    if batch.suppressed:
        log.warning(
            "%s: %d additional findings suppressed; "
            "run `agent-codespaces doctor`",
            PLUGIN_CONFIG_REGISTRY_NAME,
            batch.suppressed,
        )


def _warn_config_dropin_findings(report: ConfigDropinRegistryReport) -> None:
    """Emit bounded, deduplicated operational findings for config loading."""
    batch = _CONFIG_D_WARNING_TRACKER.select(report.findings)
    for finding in batch.emitted:
        target = f" target={finding.target}" if finding.target else ""
        log.warning(
            "%s entry=%s reason=%s%s; run `agent-codespaces doctor`",
            CONFIG_D_REGISTRY_NAME,
            finding.entry,
            finding.reason,
            target,
        )
    if batch.suppressed:
        log.warning(
            "%s: %d additional findings suppressed; run `agent-codespaces doctor`",
            CONFIG_D_REGISTRY_NAME,
            batch.suppressed,
        )


def _add_config_d_remedies(
    snapshot: ScanSnapshot[ConfigDropin],
    entry_classes: dict[str, str],
    directory: Path,
) -> ScanSnapshot[ConfigDropin]:
    """Attach consumer-specific report-only remedies to scanner findings."""
    findings: list[Finding] = []
    for finding in snapshot.findings:
        if finding.remedy:
            findings.append(finding)
            continue
        if finding.reason == "registry-indeterminate":
            remedy = (
                f"Restore readable access to {directory}, then run "
                "`agent-codespaces doctor` again; current entries are retained."
            )
        else:
            remedy = _config_d_remedy(
                Path(finding.entry),
                entry_class=entry_classes.get(finding.entry, "unknown"),
                owner=finding.owner,
            )
        findings.append(replace(finding, remedy=remedy))
    return ScanSnapshot(
        registry=snapshot.registry,
        authority=snapshot.authority,
        decisions=snapshot.decisions,
        findings=tuple(findings),
    )


def discover_dropin_configs() -> list[Path]:
    """Compatibility wrapper returning current reconciled config.d targets."""
    return [contribution.target for contribution in scan_config_dropin_registry().active_configs]


def repo_config_path(repo_path: Path) -> Path | None:
    """Return a repo's CodeSpace config file, or ``None`` if it has none.

    Prefers the canonical ``.agent-codespaces/config.yaml``; falls back to the
    legacy repo-root ``codespaces.yaml`` (back-compat). The returned path is the
    *file*; the repo root (used to resolve provision ``src`` paths) stays
    ``repo_path`` regardless of which location the file lives in.
    """
    canonical = repo_path / CONFIG_DIR_NAME / CONFIG_FILE_IN_DIR
    if canonical.exists():
        return canonical
    legacy = repo_path / CONFIG_FILENAME
    if legacy.exists():
        return legacy
    return None


def repo_has_config(repo_path: Path) -> bool:
    """Whether ``repo_path`` carries a CodeSpace config (canonical or legacy)."""
    return repo_config_path(repo_path) is not None


def cwd_repo_root() -> Path | None:
    """The git repo root for the current directory, or ``None`` when not in one.

    Backs config **auto-discovery**: a CLI run inside a repo that carries a
    ``.agent-codespaces/config.yaml`` picks it up without a manual ``config
    adopt`` (the adoption manifest remains for extra/multi repos and for the
    detached daemon paths, which pass ``include_cwd=False``).
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path.cwd(), capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    return Path(result.stdout.strip()).resolve()

# Standard location GitHub Codespaces clones the account dotfiles repo into.
# Canonical here (config is the layer both provision.py and the request-folder
# resolver share); ``provision`` re-exports it for back-compat.
DOTFILES_DIR = "/workspaces/.codespaces/.persistedshare/dotfiles"


def ensure_runtime_dir() -> None:
    """Create the runtime directory (~/.agent-codespaces) if it is absent."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _norm_repo(value: str) -> str:
    """Normalize a repo name for cross-repo matching.

    Strips any ``owner/`` prefix, lower-cases, and drops a trailing
    ``-codespaces`` so a logical repo (``example-web``) matches its CodeSpaces
    host repo (``example-org/example-web-codespaces``).
    """
    s = value.strip().lower().split("/")[-1]
    suffix = "-codespaces"
    return s[: -len(suffix)] if s.endswith(suffix) else s


def _repo_matches_codespace(repo: str, cs_repository: str | None) -> bool:
    """Whether ``repo`` addresses the CodeSpace's own hosted repository."""
    if not cs_repository:
        return False
    return _norm_repo(repo) == _norm_repo(cs_repository)


def _conventional_workspace_folder(repo: str | None) -> str | None:
    """The ``/workspaces/<basename>`` a CodeSpace checks a repo out at, by convention.

    GitHub Codespaces materializes a repo at ``/workspaces/<basename>``; a
    ``<product>-codespaces`` host repo bootstraps its product at
    ``/workspaces/<product>``. Mirrors :func:`_norm_repo` (strip ``owner/`` +
    drop a trailing ``-codespaces``) but **preserves case**, since a filesystem
    path is case-sensitive (unlike the lower-cased matching key). Returns
    ``None`` for an empty repo.

    This is the deterministic, probe-free concrete folder used to populate the
    **structured** provider ``workspace_folder`` (the ACP ``session/new`` cwd)
    when nothing is explicitly configured -- so a dispatched agent's tools run
    from the repo checkout instead of ``/home/<user>`` (dotfiles#1274). It does
    **not** change the process ``cd`` in ``effective_acp_command_for``, which
    keeps its robust runtime env-expansion for unmapped repos.
    """
    if not repo:
        return None
    base = repo.strip().rstrip("/").split("/")[-1]
    suffix = "-codespaces"
    if base.endswith(suffix):
        base = base[: -len(suffix)]
    return f"/workspaces/{base}" if base else None

# Remote-resolved `cd` for a CodeSpace session when no explicit
# ``workspace_folder`` is configured (#33). Expanded by the remote
# ``bash -l -c`` at launch, so the agent lands in the repo checkout rather than
# ``/home/vscode``. Order: ``$CODESPACE_VSCODE_FOLDER`` (the VS Code/Codespaces
# convention, when exported to the shell) -> ``$WORKING_DIRECTORY`` (set by
# Codespaces to the devcontainer workspace folder; reliably present in the
# ``bash -l`` login shell the --stdio launch uses -- this is what rescues a
# dispatched agent from landing in ``/home/vscode`` when the other vars aren't
# exported, #134) -> ``$VM_REPO_PATH`` (set by many devcontainers) -> ``.``
# (no-op last resort: keep the SSH default cwd -- never forces $HOME).
_WORKSPACE_CD = (
    'cd "${CODESPACE_VSCODE_FOLDER:-${WORKING_DIRECTORY:-${VM_REPO_PATH:-.}}}"'
)


@dataclass
class CredentialSourceConfig:
    """Configuration for a single credential source type."""

    enabled: bool = False
    allowed_hosts: list[str] = field(default_factory=list)
    allowed_resources: list[str] = field(default_factory=list)


@dataclass
class CredentialsConfig:
    """Credential relay configuration."""

    sources: dict[str, CredentialSourceConfig] = field(default_factory=dict)
    # Relay TCP port. 0 (the default) means "dynamic": the host relay binds an
    # OS-assigned ephemeral port and publishes it via relay_state, and the SSH
    # reverse-forward + LC_GIT_CREDENTIAL_RELAY env are sourced from that live
    # port -- so nothing well-known (9857) is reserved (dotfiles #694). A
    # positive value pins a fixed port (must match what the tunnel forwards).
    relay_port: int = 0
    # Default ADO host for bare `get-access-token` requests that carry no host.
    # Set this to your Azure DevOps host (e.g. ``your-org.visualstudio.com`` or
    # ``dev.azure.com``). Left unset, such requests are rejected rather than
    # assuming an organization.
    ado_host: str | None = None
    # #77: enforce host `az login` at connect time when the host cannot mint an
    # ADO REST bearer (the relay's get-azure-token path needs a signed-in host
    # az identity). Default True: a connect to an ADO workspace runs `az login`
    # on the host when needed and ABORTS the connect (with a clear message) if it
    # can't complete -- failing fast is better than a silent ADO-REST failure
    # that only surfaces later mid-dispatch. Set False to downgrade to a loud
    # warning that never aborts an otherwise-healthy SSH+relay session. The relay
    # itself always logs a loud, actionable not-logged-in error regardless.
    enforce_ado_rest_login: bool = True
    # Env var names to populate at launch from the CodeSpace-side
    # ``ado-auth-helper get-access-token`` (a relay-minted ADO bearer), so
    # tooling that authenticates to an Azure Artifacts feed via a static env
    # token works over the relay. The relay itself only wires *git* auth; a Rush
    # ``.npmrc`` line like ``//<feed>/:_authToken=${EXAMPLE_NPM_AUTH_TOKEN}`` reads
    # such a var and, when it is undefined, Rush drops the line -> anonymous ->
    # E401. Set this to your feed's token var(s), e.g. ``[EXAMPLE_NPM_AUTH_TOKEN]``.
    # Left empty (default), nothing is exported and behavior is unchanged.
    # dotfiles#1221.
    feed_token_env: list[str] = field(default_factory=list)


@dataclass
class ConnectionOwnerConfig:
    """Persistent Connection Owner relay daemon (dotfiles#1320 / #1333).

    Default OFF. When ``enabled``, a single per-machine daemon owns + self-heals
    each CodeSpace's credential-relay independent of any one agent-bridge
    dispatch, so a caller disconnect / bridge restart no longer drops the relay
    mid-task. Nothing starts it by default; enabling is "flip the config, run
    install/update" (the cutover contract). ``reconcile_interval`` bounds how
    quickly the live relay set tracks the hold registry.
    """

    enabled: bool = False
    reconcile_interval: float = 15.0


@dataclass
class RepoConfig:
    """Per-target-repo CodeSpace settings.

    Keyed by the CodeSpace repository (e.g.
    ``my-org/my-codespaces-repo``). ``provision`` hooks declared
    here apply only to CodeSpaces of this repo.

    A CodeSpaces repo frequently differs from the product checkout it hosts
    (e.g. ``my-org/example-web-codespaces`` serves a ``/workspaces/example-web``
    checkout). The directional "consume-from" relationship -- *we consume
    CodeSpaces from this repo for product repo X* -- is recorded with
    ``workspace_repo``, mirroring agent-worktrees' "related repos" concept.
    The remote workspace folder then derives from it
    (``/workspaces/<basename(workspace_repo)>``) unless an explicit
    ``workspace_folder`` overrides it. This is what makes an agent launched
    for ``example-web-codespaces`` land in ``/workspaces/example-web`` rather than
    the (wrong) ``/workspaces/example-web-codespaces``.
    """

    workspace_repo: str | None = None
    workspace_folder: str | None = None
    machine_type: str | None = None
    location: str | None = None
    # Which devcontainer config ``gh codespace create`` should use for this
    # repo. Only consulted when the repo exposes MORE THAN ONE discoverable
    # ``devcontainer.json`` -- in which case ``gh`` would otherwise prompt and
    # hard-fail headless (``failed to prompt: no terminal``). Set this to the
    # config a CodeSpace should be built from (e.g.
    # ``.devcontainer/devcontainer.json``) when the repo also ships alternate
    # devcontainers not meant for CodeSpaces (e.g. a local-Docker one). See
    # ``lifecycle.resolve_devcontainer_path``.
    devcontainer_path: str | None = None
    bootstrap_post_create: str | None = None
    provision: ProvisionConfig | None = None


@dataclass
class ProvisionFile:
    """A file an adopting repo deploys into the CodeSpace on connect.

    ``src`` is resolved relative to the repo that declares it (the repo root,
    regardless of whether the config lives at ``.agent-codespaces/config.yaml``
    or the legacy ``codespaces.yaml``). ``dest`` is the remote path and
    may start with ``~``.
    """

    src: str
    dest: str
    mode: str = "0644"
    repo_dir: Path | None = None  # set during merge, for resolving src


@dataclass
class ProvisionConfig:
    """By-convention provisioning hook declared in the repo config.

    Lets an adopting repo deploy its own files (e.g. shell env snippets)
    and run setup commands on every ``agent-codespaces ssh`` connect,
    without bespoke per-repo SSH tooling. Generic relay setup is handled
    separately by the plugin; this is purely repo-specific extras.

    Can be declared globally (applies to all CodeSpaces) or under
    ``repos.<repo>.provision`` (applies only to that repo's CodeSpaces).
    """

    files: list[ProvisionFile] = field(default_factory=list)
    on_connect: list[str] = field(default_factory=list)
    # Commands run once, right after creation (post-create injection).
    # Use for one-time setup such as running an install script.
    on_create: list[str] = field(default_factory=list)


@dataclass
class CodespacesConfig:
    """Merged configuration from all adopted repos."""

    # Defaults for CodeSpace creation
    default_machine_type: str = "largePremiumLinux"
    default_location: str = "EastUs"
    dotfiles_repo: str | None = None
    ssh_user: str = "vscode"

    # Control-plane *harness* repo (the repo that carries effort / vision
    # state), kept SEPARATE from ``dotfiles_repo`` (the GitHub-dotfiles
    # housekeeping shim). When set, the harness is cloned/synced onto the venue
    # on connect (see ``_provision_harness``) at ``/workspaces/<basename>`` --
    # the **standard repo-layout convention** (#174), same as any other named
    # repo; there is no bespoke harness path. Unset by default -> the harness is
    # NOT put on the venue; the local control-plane agent manages effort
    # updates, and the skills tell an on-venue agent where the harness lives and
    # how to interop. This decouples "the harness" from "the dotfiles shim":
    # where the two were once the same repo (so the dotfiles clone doubled as
    # the harness), they are now independent.
    harness_repo: str | None = None

    # Fallback devcontainer config path used when a repo exposes more than one
    # discoverable ``devcontainer.json`` and no per-repo ``devcontainer_path``
    # (nor an explicit CLI override) is set. The GitHub Codespaces default
    # location, so single-devcontainer repos are unaffected (the path is only
    # PASSED to ``gh`` when the repo actually has multiple configs). See
    # ``lifecycle.resolve_devcontainer_path``.
    default_devcontainer_path: str = ".devcontainer/devcontainer.json"

    # Workspace folder on the CodeSpace.  When set, the remote agent
    # command ``cd``s into this directory before launching Copilot CLI,
    # ensuring a cold-started CodeSpace lands in the repo root even if
    # the workspace volume is still mounting when the SSH session
    # connects.  Typical value: ``/workspaces/<your-repo>``.
    workspace_folder: str | None = None

    # Remote agent command -- what to run on the CodeSpace when
    # connecting via agent-bridge.  Built dynamically from
    # ``workspace_folder`` if not explicitly overridden.  Only set
    # this if you need a completely custom launch command.
    acp_command: str | None = None

    # Credential relay
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)

    # Persistent Connection Owner relay daemon (default off; dotfiles#1320)
    connection_owner: ConnectionOwnerConfig = field(
        default_factory=ConnectionOwnerConfig
    )

    # Per-target-repo settings
    repos: dict[str, RepoConfig] = field(default_factory=dict)

    # Global provisioning hooks (apply to every CodeSpace)
    provision: ProvisionConfig = field(default_factory=lambda: ProvisionConfig())

    # Operator-declared CodeSpace-scoped plugins (the control-plane's own
    # `codespace_plugins:` list in .agent-codespaces/config.yaml). Same entry
    # shape as a harness plugin's `codespacePlugins` manifest array
    # (``{source, enable?, forWorkspaceRepo?}``) -- resolved by
    # ``codespace_plugins.resolve_codespace_plugins`` alongside the ones swept
    # from installed harness plugins. This is where an operator declares the
    # generic plugins every CodeSpace should get (e.g. agent-worktrees, efforts)
    # WITHOUT baking that choice into a shared or repo-specific plugin.json.
    codespace_plugins: list[dict] = field(default_factory=list)

    # Source tracking
    source_paths: list[Path] = field(default_factory=list)

    @property
    def effective_acp_command(self) -> str:
        """Return the resolved remote agent command (global / no repo context).

        Equivalent to ``effective_acp_command_for(None)`` -- see that method
        for the full resolution order. Retained for callers with no CodeSpace
        repository in hand.
        """
        return self.effective_acp_command_for(None)

    def workspace_folder_for(self, repo: str | None) -> str | None:
        """Resolve the remote workspace folder for a CodeSpace repository.

        Resolution order (most specific wins):

        1. ``repos.<repo>.workspace_folder`` -- explicit per-repo override.
        2. ``repos.<repo>.workspace_repo`` -- the product repo this CodeSpace
           hosts; the folder derives as ``/workspaces/<basename>`` (the
           GitHub Codespaces checkout convention). This is the "related
           repo" link: it lets ``example-web-codespaces`` map to
           ``/workspaces/example-web`` without restating the path.
        3. ``defaults.workspace_folder`` -- the global fallback.

        Returns ``None`` when nothing is configured, so the caller falls back
        to the remote-resolved workspace (see ``_WORKSPACE_CD``).
        """
        repo_cfg = self.repos.get(repo) if repo else None
        if repo_cfg is not None:
            if repo_cfg.workspace_folder:
                return repo_cfg.workspace_folder
            if repo_cfg.workspace_repo:
                basename = repo_cfg.workspace_repo.rstrip("/").split("/")[-1]
                if basename:
                    return f"/workspaces/{basename}"
        return self.workspace_folder

    def resolved_workspace_folder_for(self, repo: str | None) -> str | None:
        """Concrete workspace folder for a CodeSpace repo, config-or-convention.

        Like :meth:`workspace_folder_for` but, when nothing is explicitly
        configured, falls back to the ``/workspaces/<basename>`` CodeSpaces
        layout convention (:func:`_conventional_workspace_folder`) instead of
        returning ``None``. This is what the bridge provider publishes as the
        structured ``codespace.workspace_folder`` -- the value agent-bridge uses
        as the ACP ``session/new`` cwd (it prefers structured metadata over
        parsing the launch command). A concrete folder here is what keeps a
        dispatched agent's tools rooted in the repo checkout rather than
        ``/home/<user>`` (dotfiles#1274), even for a CodeSpace with no per-repo
        config. Returns ``None`` only when *repo* is empty and no global default
        is set. Intentionally does **not** feed ``effective_acp_command_for``,
        whose process ``cd`` keeps the robust runtime env-expansion for unmapped
        repos.
        """
        return self.workspace_folder_for(repo) or _conventional_workspace_folder(
            repo
        )

    def workspace_folder_for_request(
        self, cs_repository: str | None, requested_repo: str,
    ) -> tuple[str | None, bool]:
        """Resolve the workspace folder for a ``<requested_repo>@<codespace>``.

        Implements the CodeSpace repo-layout **convention** (#174): a repo
        ``<r>`` lives at ``/workspaces/<basename(r)>`` on the CodeSpace, with two
        pre-populated special cases the CodeSpace bootstrap already owns.

        Returns ``(folder, prepopulated)``:

        - ``requested_repo`` is the **account dotfiles repo** -> ``DOTFILES_DIR``
          (``/workspaces/.codespaces/.persistedshare/dotfiles``), ``prepopulated``
          True -- the universal bootstrap clones/keeps it current.
        - ``requested_repo`` is the **CodeSpace's own product** (e.g. ``example-web``
          on an ``example-web-codespaces`` CodeSpace) -> the bare default folder
          (``workspace_folder_for(cs_repository)``), ``prepopulated`` True -- the
          devcontainer already checked it out.
        - **any other repo** -> ``/workspaces/<basename(requested_repo)>``,
          ``prepopulated`` False -- caller clones-if-missing.

        ``prepopulated`` tells the command builder whether a clone-if-missing is
        appropriate (never for a folder the bootstrap owns).
        """
        if self.dotfiles_repo and _norm_repo(requested_repo) == _norm_repo(
            self.dotfiles_repo
        ):
            return DOTFILES_DIR, True
        is_own = _repo_matches_codespace(requested_repo, cs_repository)
        if is_own:
            # Honor an explicit bare-default override (per-repo workspace_folder
            # or workspace_repo) for the CodeSpace's own product; otherwise the
            # convention basename below yields the same /workspaces/<basename>.
            configured = self.workspace_folder_for(cs_repository)
            if configured:
                return configured, True
        basename = requested_repo.rstrip("/").split("/")[-1]
        folder = f"/workspaces/{basename}" if basename else None
        return folder, is_own

    def effective_acp_command_for(
        self, repo: str | None, *,
        requested_repo: str | None = None,
        repo_remote: str | None = None,
    ) -> str:
        """Return the resolved remote agent command for a CodeSpace repo.

        ``repo`` is the CodeSpace's own hosted repository (used for per-repo
        config + the bare-default workspace folder). ``requested_repo`` (with an
        optional ``repo_remote`` URL) is the ``<repo>`` half of a
        ``<repo>@<codespace>`` cross-repo address (#174).

        **Bare** (``requested_repo`` is ``None``) -- unchanged:
        1. Explicit ``acp_command`` if set (a complete custom override).
        2. ``cd <workspace_folder> && copilot ...`` when a workspace folder
           resolves for ``repo`` (see ``workspace_folder_for``).
        3. ``cd "<remote-resolved workspace>" && copilot ...`` otherwise -- the
           directory is resolved *on the CodeSpace* at launch (see
           ``_WORKSPACE_CD``) so a session lands in the repo checkout rather
           than ``/home/vscode`` (#33).

        **Cross-repo** (``requested_repo`` set) -- apply the repo-layout
        convention (``workspace_folder_for_request``):
        - a **pre-populated** folder (own product / dotfiles) -> plain
          ``cd <folder> && copilot ...`` (no clone; the bootstrap owns it).
        - **any other** folder with a known ``repo_remote`` ->
          ``[ -d <folder>/.git ] || git clone <remote> <folder>; cd <folder> &&
          copilot ...`` (clone-if-missing over the credential relay the
          ``--stdio`` login shell already set up).
        - any other folder with **no** remote -> plain ``cd <folder> && ...``;
          the ``cd`` fails loudly if the checkout is absent, surfacing the
          missing-remote misconfiguration rather than silently launching in the
          wrong place.

        ``--allow-all-tools`` is required for headless dispatch: there is no
        human to answer interactive tool-permission prompts.
        """
        copilot = "copilot --acp --stdio --allow-all-tools"

        if requested_repo is not None:
            folder, prepopulated = self.workspace_folder_for_request(
                repo, requested_repo
            )
            if folder is None:
                return f"{_WORKSPACE_CD} && {copilot}"
            if prepopulated or not repo_remote:
                return f"cd {folder} && {copilot}"
            clone = f"[ -d {folder}/.git ] || git clone {repo_remote} {folder}"
            return f"{clone}; cd {folder} && {copilot}"

        if self.acp_command:
            return self.acp_command
        workspace_folder = self.workspace_folder_for(repo)
        if workspace_folder:
            return f"cd {workspace_folder} && {copilot}"
        return f"{_WORKSPACE_CD} && {copilot}"

    def provision_for_repo(self, repo: str | None) -> ProvisionConfig:
        """Collect provisioning hooks that apply to a CodeSpace.

        Returns the union of the global ``provision`` hooks and any
        declared under ``repos.<repo>.provision`` for the CodeSpace's
        repository. Global hooks run first.
        """
        files = list(self.provision.files)
        on_connect = list(self.provision.on_connect)
        on_create = list(self.provision.on_create)
        if repo and repo in self.repos:
            repo_prov = self.repos[repo].provision
            if repo_prov:
                files.extend(repo_prov.files)
                on_connect.extend(repo_prov.on_connect)
                on_create.extend(repo_prov.on_create)
        return ProvisionConfig(
            files=files, on_connect=on_connect, on_create=on_create,
        )


@dataclass
class AdoptedRepo:
    """A repo registered in the adoption manifest."""

    path: Path
    adopted_at: str | None = None


def load_adopted_repos() -> list[AdoptedRepo]:
    """Load the adoption manifest from the runtime directory."""
    if not ADOPTED_REPOS_FILE.exists():
        return []

    with open(ADOPTED_REPOS_FILE) as f:
        data = yaml.safe_load(f) or {}

    # Lazy schema migration (in memory, never persists / never raises) so a
    # still-old manifest loads at the current shape before install/update
    # rewrites it.
    from . import config_migrations

    data = config_migrations.migrate_loaded(data)

    repos = []
    for entry in data.get("repos", []):
        repos.append(AdoptedRepo(
            path=Path(entry["path"]),
            adopted_at=entry.get("adopted_at"),
        ))
    return repos


def save_adopted_repos(repos: list[AdoptedRepo]) -> None:
    """Write the adoption manifest to the runtime directory."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    from . import config_migrations

    # Stamp the current schema version so the marker round-trips through a
    # normal save (rather than being dropped on the next reserialize).
    data = {
        "schema_version": config_migrations.current_version(),
        "repos": [
            {"path": str(r.path), "adopted_at": r.adopted_at}
            for r in repos
        ],
    }
    with open(ADOPTED_REPOS_FILE, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def load_repo_config(repo_path: Path) -> dict[str, Any] | None:
    """Load a repo's CodeSpace config. Returns None if missing.

    Reads the canonical ``.agent-codespaces/config.yaml`` or the legacy
    repo-root ``codespaces.yaml`` (see :func:`repo_config_path`).
    """
    config_file = repo_config_path(repo_path)
    if config_file is None:
        log.warning("No %s found in %s", CANONICAL_CONFIG_REL, repo_path)
        return None

    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def _state_root_config_dir(repo_path: Path) -> Path | None:
    """Resolve the bound knowledge repo's dir when it carries a CodeSpace config.

    The citadel E1e **knowledge overlay** (config-graft, #947): a stateless
    harness carries no CodeSpace config of its own -- personal CodeSpace
    topology is reference config that lives in the bound knowledge repo. This asks
    ``agent-worktrees state-root`` (run with cwd=repo_path) only to LOCATE the
    knowledge checkout -- the config-READ axis, distinct from where personal state
    is written -- returning it only when it actually declares a CodeSpace config
    (canonical ``.agent-codespaces/config.yaml`` or legacy ``codespaces.yaml``).

    Best-effort + fail-open: a missing ``agent-worktrees`` binstub, a
    non-stateless / unbound repo, or any error yields ``None``. Never raises.
    Only the config *content* + its ``src``/provision ``repo_dir`` graft here;
    plugin-settings sourcing (``source_paths``) stays the harness's own, so
    generic CodeSpace plugins remain harness-sourced.
    """
    import json
    import shutil
    import subprocess

    exe = shutil.which("agent-worktrees")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "state-root", "--json"], cwd=str(repo_path),
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    # Only graft when the launch repo actually requires an external state root
    # (a stateless harness); a self-hosted repo resolves to itself.
    if not data.get("requires_external") or not data.get("bound"):
        return None
    root = data.get("state_root")
    if not root:
        return None
    kroot = Path(root)
    return kroot if repo_has_config(kroot) else None


def repo_copilot_settings(source_paths: Iterable[Path]) -> dict[str, Any]:
    """Merge repo-scoped Copilot settings across the adopted control-plane repos.

    Reads each repo's plugin config from the **Copilot-native**
    ``.github/copilot/settings.json`` (+ ``settings.local.json``) **and**, as a
    fallback, the **Claude** ``.claude/settings.json`` (+ ``.claude/settings.local.json``),
    returning the merged ``extraKnownMarketplaces`` and ``enabledPlugins`` maps.
    This is the **repo-scoped** replacement for reading the harness *user*
    ``~/.copilot/settings.json`` -- the marketplace registry and plugin enablement
    are versioned, in-repo config, not per-machine user state.

    Per-repo native/Claude precedence is resolved by the shared ``plugin_resolve``
    lib (**Copilot-native wins over Claude** on a key conflict; within a
    convention, ``settings.local.json`` overrides ``settings.json``). Across repos,
    ``extraKnownMarketplaces`` is first-wins and ``enabledPlugins`` is last-wins.
    Missing / malformed files are skipped.

    Returns ``{"extraKnownMarketplaces": {...}, "enabledPlugins": {...}}`` (both
    always present, possibly empty).
    """
    from plugin_resolve import read_repo_settings

    ekm: dict[str, Any] = {}
    ep: dict[str, Any] = {}
    for base in source_paths:
        repo = read_repo_settings(base)
        for k, v in repo.marketplaces.items():
            ekm.setdefault(k, v)  # first-wins across repos
        for k, v in repo.enabled.items():
            ep[k] = v  # last-wins across repos
    return {"extraKnownMarketplaces": ekm, "enabledPlugins": ep}


def _parse_credential_source(raw: dict[str, Any]) -> CredentialSourceConfig:
    """Parse a credential source config block."""
    return CredentialSourceConfig(
        enabled=raw.get("enabled", False),
        allowed_hosts=raw.get("allowed_hosts", []),
        allowed_resources=raw.get("allowed_resources", []),
    )


def _parse_provision(raw: dict[str, Any], repo_dir: Path | None) -> ProvisionConfig:
    """Parse a ``provision`` block, tagging files with their repo dir."""
    files: list[ProvisionFile] = []
    for f in raw.get("files", []) or []:
        if not isinstance(f, dict) or "src" not in f or "dest" not in f:
            log.warning("Skipping invalid provision file entry: %r", f)
            continue
        files.append(ProvisionFile(
            src=f["src"],
            dest=f["dest"],
            mode=str(f.get("mode", "0644")),
            repo_dir=repo_dir,
        ))
    on_connect = [str(c) for c in (raw.get("on_connect", []) or [])]
    on_create = [str(c) for c in (raw.get("on_create", []) or [])]
    return ProvisionConfig(files=files, on_connect=on_connect, on_create=on_create)


def _parse_repo_config(raw: dict[str, Any], repo_dir: Path | None = None) -> RepoConfig:
    """Parse a per-target-repo config block."""
    bootstrap = raw.get("bootstrap", {})
    provision_raw = raw.get("provision")
    return RepoConfig(
        workspace_repo=raw.get("workspace_repo"),
        workspace_folder=raw.get("workspace_folder"),
        machine_type=raw.get("machine_type"),
        location=raw.get("location"),
        devcontainer_path=raw.get("devcontainer_path"),
        bootstrap_post_create=bootstrap.get("post_create"),
        provision=(
            _parse_provision(provision_raw, repo_dir) if provision_raw else None
        ),
    )


def load_merged_config(
    include_cwd: bool = True,
    *,
    provider_reports: ConfigProviderReports | None = None,
) -> CodespacesConfig:
    """Load and merge CodeSpace config from all adopted repos.

    Reads each repo's config (``.agent-codespaces/config.yaml``, or legacy
    ``codespaces.yaml``) live. First repo's values win on conflicts (except
    credential sources, which are unioned).

    ``include_cwd`` (default True) also **auto-discovers** the current git repo:
    if the cwd's repo carries a config and isn't already adopted, it is merged
    last -- so a CLI run inside a repo picks up its ``.agent-codespaces/config.yaml``
    with no manual ``config adopt``. Detached daemon paths (relay/resolver) pass
    ``include_cwd=False`` so they stay driven purely by the adoption manifest.
    """
    roots: list[Path] = [entry.path for entry in load_adopted_repos()]
    if include_cwd:
        cwd_root = cwd_repo_root()
        if (
            cwd_root is not None
            and cwd_root not in roots
            and repo_has_config(cwd_root)
        ):
            roots.append(cwd_root)

    merged = CodespacesConfig()
    defaults_set = False
    connection_owner_set = False

    # Ordered list of (raw_config, config_dir, source_path) to merge; order =
    # precedence. Adopted repos + cwd first, then active plugin declarations,
    # then compatibility config.d providers. All provider config is a default
    # below repository-owned config; active payload declarations outrank stale
    # user-level pointers for the same provider.
    sources: list[tuple[dict[str, Any], Path, Path]] = []
    for repo_root in roots:
        # E1e knowledge overlay (config-graft, #947): read the config from the
        # repo itself, or -- when it has none and is a stateless harness -- from
        # the bound knowledge repo (its src/provision paths resolve relative to
        # THAT dir). source_paths stays the repo root so generic CodeSpace plugin
        # settings remain harness-sourced; only the config content + its repo_dir
        # graft to the knowledge overlay.
        config_dir = repo_root
        if not repo_has_config(repo_root):
            grafted = _state_root_config_dir(repo_root)
            if grafted is not None:
                config_dir = grafted
        raw = load_repo_config(config_dir)
        if raw is None:
            continue
        sources.append((raw, config_dir, repo_root))
    provider_reports = provider_reports or scan_config_providers()
    _warn_active_plugin_config_findings(provider_reports.active_plugins)
    _warn_config_dropin_findings(provider_reports.config_d)
    for contribution in provider_reports.active_configs:
        # The provider classifiers already read and structurally validated each
        # target. Re-use their exact results so runtime and doctor cannot diverge.
        sources.append((
            contribution.raw_config,
            contribution.target.parent,
            contribution.target.parent,
        ))

    for raw, config_dir, source_path in sources:
        merged.source_paths.append(source_path)

        # Defaults (first wins)
        defaults = raw.get("defaults", {})
        if not defaults_set and defaults:
            merged.default_machine_type = defaults.get(
                "machine_type", merged.default_machine_type
            )
            merged.default_location = defaults.get(
                "location", merged.default_location
            )
            merged.default_devcontainer_path = defaults.get(
                "devcontainer_path", merged.default_devcontainer_path
            )
            merged.dotfiles_repo = defaults.get(
                "dotfiles_repo", merged.dotfiles_repo
            )
            merged.harness_repo = defaults.get(
                "harness_repo", merged.harness_repo
            )
            merged.ssh_user = defaults.get(
                "ssh_user", merged.ssh_user
            )
            merged.acp_command = defaults.get(
                "acp_command", merged.acp_command
            )
            merged.workspace_folder = defaults.get(
                "workspace_folder", merged.workspace_folder
            )
            defaults_set = True

        # Credentials (union sources across repos)
        creds_raw = raw.get("credentials", {})
        if creds_raw:
            merged.credentials.relay_port = creds_raw.get(
                "relay_port", merged.credentials.relay_port
            )
            merged.credentials.ado_host = creds_raw.get(
                "ado_host", merged.credentials.ado_host
            )
            merged.credentials.enforce_ado_rest_login = bool(creds_raw.get(
                "enforce_ado_rest_login",
                merged.credentials.enforce_ado_rest_login,
            ))
            # Union feed-token env var names across adopted repos (dotfiles#1221).
            for _var in creds_raw.get("feed_token_env", []) or []:
                if _var and _var not in merged.credentials.feed_token_env:
                    merged.credentials.feed_token_env.append(_var)
            for source_name, source_raw in creds_raw.get("sources", {}).items():
                if source_name not in merged.credentials.sources:
                    merged.credentials.sources[source_name] = _parse_credential_source(
                        source_raw
                    )
                else:
                    # Union allowlists across adopted repos.
                    existing = merged.credentials.sources[source_name]
                    new_hosts = set(existing.allowed_hosts) | set(
                        source_raw.get("allowed_hosts", [])
                    )
                    existing.allowed_hosts = sorted(new_hosts)
                    new_resources = set(existing.allowed_resources) | set(
                        source_raw.get("allowed_resources", [])
                    )
                    existing.allowed_resources = sorted(new_resources)

        # Connection Owner (first repo with a block wins; default off). An
        # explicit block claims the slot even when empty ({} -> defaults), so a
        # later repo cannot override a deliberate empty declaration.
        if "connection_owner" in raw and not connection_owner_set:
            co_raw = raw.get("connection_owner") or {}
            merged.connection_owner.enabled = bool(
                co_raw.get("enabled", merged.connection_owner.enabled)
            )
            merged.connection_owner.reconcile_interval = float(
                co_raw.get(
                    "reconcile_interval",
                    merged.connection_owner.reconcile_interval,
                )
            )
            connection_owner_set = True

        # Repos (first wins on conflicts)
        for repo_key, repo_raw in raw.get("repos", {}).items():
            if repo_key not in merged.repos:
                merged.repos[repo_key] = _parse_repo_config(repo_raw, config_dir)

        # Global provisioning hooks (union across all adopted repos)
        provision_raw = raw.get("provision")
        if provision_raw:
            parsed = _parse_provision(provision_raw, config_dir)
            merged.provision.files.extend(parsed.files)
            merged.provision.on_connect.extend(parsed.on_connect)
            merged.provision.on_create.extend(parsed.on_create)

        # Operator-declared CodeSpace-scoped plugins (union across adopted repos).
        cs_plugins_raw = raw.get("codespace_plugins")
        if isinstance(cs_plugins_raw, list):
            merged.codespace_plugins.extend(
                e for e in cs_plugins_raw if isinstance(e, dict)
            )

    return merged


def validate_config(config: CodespacesConfig) -> list[str]:
    """Validate a merged config. Returns a list of warnings/errors."""
    issues: list[str] = []

    if not config.source_paths:
        issues.append(NO_SUPPLEMENTAL_CONFIG_ADVISORY)

    for source_name, source_cfg in config.credentials.sources.items():
        if (
            source_cfg.enabled
            and source_name == "az-login"
            and not source_cfg.allowed_resources
        ):
            issues.append(
                f"Credential source '{source_name}' is enabled but has no "
                "allowed_resources"
            )
        elif (
            source_cfg.enabled
            and source_name != "az-login"
            and not source_cfg.allowed_hosts
        ):
            issues.append(
                f"Credential source '{source_name}' is enabled but has no allowed_hosts"
            )

    return issues
