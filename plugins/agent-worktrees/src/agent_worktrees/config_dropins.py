"""Resilient per-project ``config.d`` discovery.

Direct YAML fragments are operator-owned. Managed plugins contribute an
attributed JSON pointer to a YAML file contained by their current,
identity-verified plugin root.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Callable, Mapping
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

REGISTRY_NAME = "config.d"
POINTER_SCHEMA_VERSION = 1
_POINTER_KEYS = frozenset({"schema_version", "plugin", "plugin_root", "target"})
_PLUGIN_SOURCE_RE = re.compile(r"^[^@/\\\s]+@[^@/\\\s]+$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

log = logging.getLogger("agent-worktrees")


@dataclass(frozen=True)
class ConfigDropin:
    """One validated config fragment selected for merging."""

    entry: Path
    target: Path
    entry_class: str
    raw_config: dict[str, Any]
    owner: str | None = None

    def to_dict(self) -> dict[str, str]:
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
    """The classifier result consumed by config loading and doctor."""

    snapshot: ScanSnapshot[ConfigDropin]
    active_entries: dict[str, ConfigDropin]
    entry_classes: dict[str, str] = field(default_factory=dict)

    @property
    def authority(self) -> ScanAuthority:
        return self.snapshot.authority

    @property
    def findings(self) -> tuple[Finding, ...]:
        return self.snapshot.findings

    @property
    def active_configs(self) -> list[ConfigDropin]:
        return [self.active_entries[key] for key in sorted(self.active_entries)]

    def to_dict(self) -> dict[str, Any]:
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
            "registry": REGISTRY_NAME,
            "authority": self.authority.value,
            "active_entries": [item.to_dict() for item in self.active_configs],
            "entries": entries,
            "findings": [finding.to_dict() for finding in self.findings],
        }


_LAST_KNOWN: dict[tuple[str, str], dict[str, ConfigDropin]] = {}
_WARNING_TRACKER = WarningTracker()


def _root_identity(directory: Path) -> str:
    try:
        return str(directory.expanduser().resolve(strict=False))
    except OSError:
        return os.path.abspath(os.path.expanduser(str(directory)))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _remedy(entry: Path, *, entry_class: str, owner: str | None = None) -> str:
    if entry_class == "managed-plugin" and owner:
        return (
            f"Re-enable or reinstall {owner}, then recreate {entry} from its "
            "current plugin payload; agent-worktrees will not remove it."
        )
    if entry_class == "operator":
        return (
            f"Fix the operator-owned YAML fragment at {entry}; "
            "agent-worktrees will not remove it."
        )
    return (
        f"Fix or remove the unrecognized entry {entry} if it is no longer "
        "intended; agent-worktrees will not remove it."
    )


def _finding(
    entry: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: Path | str | None = None,
    entry_class: str,
    owner: str | None = None,
    detail: str | None = None,
) -> Finding:
    return Finding(
        registry=REGISTRY_NAME,
        entry=str(entry),
        status=status,
        reason=reason,
        target=str(target) if target is not None else None,
        owner=owner,
        remedy=_remedy(entry, entry_class=entry_class, owner=owner),
        detail=detail,
    )


def _regular_file(
    entry: Path,
    target: Path,
    *,
    entry_class: str,
    owner: str | None,
) -> tuple[Path | None, EntryDecision[ConfigDropin] | None]:
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None, EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=target,
                entry_class=entry_class,
                owner=owner,
            )
        )
    except OSError as exc:
        return None, EntryDecision.indeterminate(
            _finding(
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
            _finding(
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
            _finding(
                entry,
                "missing-target",
                target=target,
                entry_class=entry_class,
                owner=owner,
            )
        )
    except OSError as exc:
        return None, EntryDecision.indeterminate(
            _finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=target,
                entry_class=entry_class,
                owner=owner,
                detail=str(exc),
            )
        )


def _validate_string_list(value: object, *, location: str) -> str | None:
    if not isinstance(value, list):
        return f"{location} must be a list"
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return f"{location} entries must be non-empty strings"
    return None


def _validate_platform_map(
    value: object,
    *,
    location: str,
    list_values: bool,
) -> str | None:
    if not isinstance(value, dict):
        return f"{location} must be a mapping"
    for platform_name, item in value.items():
        if not isinstance(platform_name, str) or not platform_name.strip():
            return f"{location} keys must be non-empty strings"
        if list_values:
            error = _validate_string_list(item, location=f"{location}.{platform_name}")
            if error:
                return error
        elif not isinstance(item, str) or not item.strip():
            return f"{location}.{platform_name} must be a non-empty string"
    return None


def _validate_repo_fragment(repo: object, *, location: str) -> str | None:
    if not isinstance(repo, dict):
        return f"{location} must be a mapping"
    for name in ("anchor", "worktree_root", "default_branch", "remote"):
        if name in repo and not isinstance(repo[name], str):
            return f"{location}.{name} must be a string"
    for name in ("base_repo", "stateless", "requires_external_state_root"):
        if name in repo and not isinstance(repo[name], bool):
            return f"{location}.{name} must be a boolean"
    for name in ("launch", "launch_recovery", "validate_hook", "post_install_hook"):
        if name in repo:
            error = _validate_platform_map(
                repo[name], location=f"{location}.{name}", list_values=True
            )
            if error:
                return error
    for name in ("copilot_path", "setup_hook", "env_script"):
        if name in repo:
            error = _validate_platform_map(
                repo[name], location=f"{location}.{name}", list_values=False
            )
            if error:
                return error
    if "session_path" in repo:
        error = _validate_platform_map(
            repo["session_path"],
            location=f"{location}.session_path",
            list_values=True,
        )
        if error:
            return error
    if "session_env" in repo:
        session_env = repo["session_env"]
        if not isinstance(session_env, dict):
            return f"{location}.session_env must be a mapping"
        if any(
            not isinstance(key, str) or not key.strip()
            for key in session_env
        ):
            return f"{location}.session_env keys must be non-empty strings"
    for name in ("validate_paths", "service_paths"):
        if name in repo:
            error = _validate_string_list(
                repo[name], location=f"{location}.{name}"
            )
            if error:
                return error
    if "pr" in repo:
        error = _validate_pr(repo["pr"], location=f"{location}.pr")
        if error:
            return error
    return None


def _validate_pr(raw: object, *, location: str) -> str | None:
    if not isinstance(raw, dict):
        return f"{location} must be a mapping"
    boolean_fields = {
        "enabled",
        "required",
        "auto_open",
        "source_attribution",
        "approval_required",
        "squash",
        "delete_source_branch",
        "bypass_policy",
        "review_blocking",
        "self_approve",
        "conflict_retriggers_review",
        "prefer_auto_merge",
    }
    string_fields = {
        "provider",
        "strategy",
        "branch_prefix",
        "head_scheme",
        "head_pattern",
        "api_base",
        "token_env",
        "token_command",
        "automerge_label",
        "bypass_reason",
        "reviewer",
        "review_latency_hint",
        "merge_actor",
        "branch_update_strategy",
        "merge_strategy",
    }
    list_or_string_fields = {
        "labels",
        "hold_labels",
        "required_body_sections",
        "wip_title_prefixes",
    }
    for name in boolean_fields:
        if name in raw and not isinstance(raw[name], bool):
            return f"{location}.{name} must be a boolean"
    for name in string_fields:
        if name in raw and not isinstance(raw[name], str):
            return f"{location}.{name} must be a string"
    for name in list_or_string_fields:
        if name not in raw:
            continue
        value = raw[name]
        if isinstance(value, str):
            continue
        error = _validate_string_list(value, location=f"{location}.{name}")
        if error:
            return error
    choices = {
        "head_scheme": {"refspec", "snapshot"},
        "branch_update_strategy": {"rebase", "merge"},
        "merge_strategy": {"squash", "merge", "rebase"},
    }
    for name, allowed in choices.items():
        if name in raw and raw[name].strip().lower() not in allowed:
            return (
                f"{location}.{name} must be one of "
                f"{', '.join(sorted(allowed))}"
            )
    return None


def _validate_config(raw: object) -> str | None:
    if not isinstance(raw, dict) or not raw:
        return "config fragment must be a non-empty YAML mapping"
    for name in (
        "srcroot",
        "machine",
        "platform",
        "repo_name",
        "knowledge_repo",
    ):
        if name in raw and not isinstance(raw[name], str):
            return f"{name} must be a string"
    repos = raw.get("repos")
    if repos is not None:
        if not isinstance(repos, dict):
            return "repos must be a mapping"
        for name, repo in repos.items():
            if not isinstance(name, str) or not name.strip():
                return "repos keys must be non-empty strings"
            error = _validate_repo_fragment(repo, location=f"repos.{name}")
            if error:
                return error
    profiles = raw.get("copilot_profiles")
    if profiles is not None:
        if not isinstance(profiles, list):
            return "copilot_profiles must be a list"
        for index, profile in enumerate(profiles):
            location = f"copilot_profiles[{index}]"
            if not isinstance(profile, dict):
                return f"{location} must be a mapping"
            name = profile.get("name")
            if not isinstance(name, str) or not name.strip():
                return f"{location}.name must be a non-empty string"
            if "label" in profile and not isinstance(profile["label"], str):
                return f"{location}.label must be a string"
            if "env" in profile:
                env = profile["env"]
                if not isinstance(env, dict):
                    return f"{location}.env must be a mapping"
                if any(not isinstance(key, str) for key in env):
                    return f"{location}.env keys must be strings"
            if "copilot_args" in profile:
                error = _validate_string_list(
                    profile["copilot_args"],
                    location=f"{location}.copilot_args",
                )
                if error:
                    return error
    session_backend = raw.get("session_backend")
    if session_backend is not None:
        if not isinstance(session_backend, dict):
            return "session_backend must be a mapping"
        allowed = {
            "kind",
            "endpoint_url",
            "github_account",
            "protocol_versions",
            "auth_resource",
            "connect_timeout_seconds",
        }
        unknown = set(session_backend) - allowed
        if unknown:
            return (
                "session_backend has unknown key(s): "
                + ", ".join(sorted(str(value) for value in unknown))
            )
        if "kind" in session_backend and session_backend["kind"] not in {
            "direct",
            "ahp",
        }:
            return "session_backend.kind must be 'direct' or 'ahp'"
        for name in ("endpoint_url", "github_account", "auth_resource"):
            if name in session_backend and not isinstance(
                session_backend[name], str
            ):
                return f"session_backend.{name} must be a string"
        if "protocol_versions" in session_backend:
            error = _validate_string_list(
                session_backend["protocol_versions"],
                location="session_backend.protocol_versions",
            )
            if error:
                return error
        if (
            "connect_timeout_seconds" in session_backend
            and (
                isinstance(
                    session_backend["connect_timeout_seconds"],
                    bool,
                )
                or not isinstance(
                    session_backend["connect_timeout_seconds"],
                    int | float,
                )
            )
        ):
            return (
                "session_backend.connect_timeout_seconds must be a number"
            )
    for name in ("headless", "auto_fast_forward", "new_picker"):
        if name in raw and not isinstance(raw[name], bool):
            return f"{name} must be a boolean"
    return None


def _validated_yaml(
    entry: Path,
    target: Path,
    *,
    entry_class: str,
    owner: str | None = None,
) -> EntryDecision[ConfigDropin]:
    canonical, verdict = _regular_file(
        entry, target, entry_class=entry_class, owner=owner
    )
    if verdict is not None:
        return verdict
    canonical = cast(Path, canonical)
    try:
        raw = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=f"config is not valid UTF-8: {exc}",
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
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
            _finding(
                entry,
                "invalid-entry",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=f"config is not valid YAML: {exc}",
            )
        )
    error = _validate_config(raw)
    if error:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=canonical,
                entry_class=entry_class,
                owner=owner,
                detail=error,
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


def _managed_decision(
    entry: Path,
    data: object,
    *,
    project_name: str,
    activation_resolver: Callable[[], ActivationReport],
) -> EntryDecision[ConfigDropin]:
    if (
        not isinstance(data, dict)
        or set(data) != _POINTER_KEYS
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != POINTER_SCHEMA_VERSION
        or not all(
            isinstance(data.get(key), str) and data[key].strip()
            for key in ("plugin", "plugin_root", "target")
        )
    ):
        return EntryDecision.inactive(
            _finding(
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
    stored_root = Path(data["plugin_root"].strip()).expanduser()
    target = Path(data["target"].strip()).expanduser()
    if not _PLUGIN_SOURCE_RE.fullmatch(source):
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin must be an exact name@marketplace identity",
            )
        )
    if not stored_root.is_absolute() or not target.is_absolute():
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin_root and target must be absolute paths",
            )
        )

    report = activation_resolver()
    source_decision = report.decisions.get(source)
    if (
        report.authority is ScanAuthority.INDETERMINATE
        or (
            source_decision is not None
            and source_decision.status is EntryStatus.INDETERMINATE
        )
    ):
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin activation or root evidence is indeterminate",
            )
        )
    if source_decision is None or source_decision.status is EntryStatus.INACTIVE:
        reason = "not-enabled"
        detail = "plugin is not enabled globally or for this project"
        if source_decision is not None and source_decision.findings:
            current = source_decision.findings[0]
            reason = current.reason
            detail = current.detail or detail
        return EntryDecision.inactive(
            _finding(
                entry,
                reason,
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=detail,
            )
        )

    active = cast(ActivePlugin, source_decision.value)
    allowed_scopes = {"global"}
    if project_name:
        allowed_scopes.add(f"project:{project_name}")
    if not allowed_scopes.intersection(active.scopes):
        return EntryDecision.inactive(
            _finding(
                entry,
                "not-enabled",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    f"plugin is enabled only outside project {project_name!r}"
                    if project_name
                    else "plugin is not globally enabled"
                ),
            )
        )

    try:
        canonical_root = stored_root.resolve(strict=True)
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=f"pointer plugin_root is unavailable: {exc}",
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=f"pointer plugin_root could not be read: {exc}",
            )
        )
    applicable_roots = {
        selected.root
        for selected in active.live_roots
        if allowed_scopes.intersection(selected.scopes)
    }
    if canonical_root not in applicable_roots:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=target,
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    "pointer root differs from active roots for this scope: "
                    + ", ".join(str(root) for root in sorted(applicable_roots))
                ),
            )
        )

    canonical_target, verdict = _regular_file(
        entry, target, entry_class="managed-plugin", owner=source
    )
    if verdict is not None:
        return verdict
    canonical_target = cast(Path, canonical_target)
    try:
        canonical_target.relative_to(canonical_root)
    except ValueError:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=canonical_target,
                entry_class="managed-plugin",
                owner=source,
                detail="target escapes the identity-verified plugin root",
            )
        )
    decision = _validated_yaml(
        entry,
        canonical_target,
        entry_class="managed-plugin",
        owner=source,
    )
    if (
        decision.status is EntryStatus.ACTIVE
        and source_decision.status is EntryStatus.ACTIVE_WITH_ADVISORY
    ):
        contribution = cast(ConfigDropin, decision.value)
        advisories = tuple(
            replace(
                finding,
                registry=REGISTRY_NAME,
                entry=str(entry),
                status="active-with-advisory",
                owner=source,
                remedy=_remedy(
                    entry, entry_class="managed-plugin", owner=source
                ),
            )
            for finding in source_decision.findings
        )
        return EntryDecision.advisory(contribution, *advisories)
    return decision


def _withdraw_confirmed_disappearances(
    snapshot: ScanSnapshot[ConfigDropin],
) -> ScanSnapshot[ConfigDropin]:
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


def _add_registry_remedies(
    snapshot: ScanSnapshot[ConfigDropin],
    directory: Path,
    entry_classes: Mapping[str, str],
) -> ScanSnapshot[ConfigDropin]:
    findings: list[Finding] = []
    for finding in snapshot.findings:
        if finding.remedy:
            findings.append(finding)
            continue
        remedy = (
            f"Restore readable access to {directory}, then run "
            "`agent-worktrees doctor` again; current fragments are retained."
            if finding.reason == "registry-indeterminate"
            else _remedy(
                Path(finding.entry),
                entry_class=entry_classes.get(finding.entry, "unknown"),
                owner=finding.owner,
            )
        )
        findings.append(replace(finding, remedy=remedy))
    return replace(snapshot, findings=tuple(findings))


def scan_config_dropin_registry(
    directory: str | os.PathLike[str],
    *,
    project_name: str = "",
    previous: Mapping[str, ConfigDropin] | None = None,
    activation_report: ActivationReport | None = None,
) -> ConfigDropinRegistryReport:
    """Scan and reconcile one project's machine-local config fragments."""
    root = Path(directory)
    entry_classes: dict[str, str] = {}
    activation = activation_report

    def current_activation() -> ActivationReport:
        nonlocal activation
        if activation is None:
            activation = resolve_active_plugins()
        return activation

    def classify(entry: Path) -> EntryDecision[ConfigDropin]:
        key = str(entry)
        if entry.suffix.lower() in {".yaml", ".yml"}:
            entry_classes[key] = "operator"
            return _validated_yaml(entry, entry, entry_class="operator")
        if entry.suffix.lower() != ".json":
            entry_classes[key] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail="entry must be operator YAML or a managed JSON pointer",
                )
            )
        entry_classes[key] = "managed-plugin"
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="managed-plugin",
                    detail=f"pointer is not valid UTF-8: {exc}",
                )
            )
        except json.JSONDecodeError as exc:
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="managed-plugin",
                    detail=f"pointer is not valid JSON: {exc}",
                )
            )
        return _managed_decision(
            entry,
            data,
            project_name=project_name,
            activation_resolver=current_activation,
        )

    snapshot = scan_directory(root, classify, registry=REGISTRY_NAME)
    snapshot = _withdraw_confirmed_disappearances(snapshot)
    snapshot = _add_registry_remedies(snapshot, root, entry_classes)

    cache_key = (_root_identity(root), project_name)
    prior = dict(previous) if previous is not None else dict(
        _LAST_KNOWN.get(cache_key, {})
    )
    reconciled = snapshot.reconcile(prior)
    active_entries = dict(reconciled)

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
        current = decisions.get(key)
        if current is None or current.status is EntryStatus.INDETERMINATE:
            continue
        duplicate = _finding(
            Path(key),
            "duplicate",
            target=contribution.target,
            entry_class=entry_classes.get(key, "unknown"),
            owner=contribution.owner,
            detail=f"same config target is already supplied by {winner}",
        )
        decisions[key] = EntryDecision.inactive(duplicate)
        findings = [finding for finding in findings if finding.entry != key]
        findings.append(duplicate)
    snapshot = replace(
        snapshot,
        decisions=decisions,
        findings=tuple(findings),
    )
    if previous is None:
        _LAST_KNOWN[cache_key] = reconciled
    return ConfigDropinRegistryReport(
        snapshot=snapshot,
        active_entries=active_entries,
        entry_classes=entry_classes,
    )


def empty_config_dropin_report() -> ConfigDropinRegistryReport:
    """Return an explicit absent report when no project can be resolved."""
    return ConfigDropinRegistryReport(
        snapshot=ScanSnapshot(
            registry=REGISTRY_NAME,
            authority=ScanAuthority.ABSENT,
        ),
        active_entries={},
    )


def warn_config_dropin_findings(
    report: ConfigDropinRegistryReport,
) -> None:
    """Emit bounded, fingerprint-deduplicated operational warnings."""
    batch = _WARNING_TRACKER.select(report.findings)
    for finding in batch.emitted:
        target = f" target={finding.target}" if finding.target else ""
        log.warning(
            "%s entry=%s reason=%s%s; run `agent-worktrees doctor`",
            REGISTRY_NAME,
            finding.entry,
            finding.reason,
            target,
        )
    if batch.suppressed:
        log.warning(
            "%s: %d additional findings suppressed; run `agent-worktrees doctor`",
            REGISTRY_NAME,
            batch.suppressed,
        )
