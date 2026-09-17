"""Pivot registry entry classification helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from dropin_registry import EntryDecision, EntryStatus, Finding, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from .pivot_actions import ManifestError, parse_config_sections, parse_worktree_actions
from .pivot_manifest import (
    _MANAGED_POINTER_KEYS,
    _PLUGIN_SOURCE_RE,
    MANAGED_SCHEMA_VERSION,
    REGISTRY_NAME,
    PivotContribution,
    parse_manifest,
)
from .pivot_targets import (
    TargetUnusableError,
    _managed_manifest_data,
    _read_json,
    _read_verified_template,
    _rewrite_manifest_commands,
)


_PRUNABLE_ENTRY_CLASSES = frozenset(
    {"managed-plugin", "legacy-plugin", "unknown-legacy"}
)


def _remedy(
    entry: Path,
    *,
    entry_class: str,
    owner: str | None = None,
) -> str:
    prunable = entry_class in _PRUNABLE_ENTRY_CLASSES
    prune_hint = (
        " (or run `agent-worktrees doctor --fix --prune-pivots` to remove "
        "this stale copy once the plugin's current manifest is confirmed "
        "active elsewhere)"
        if prunable
        else ""
    )
    if entry_class == "managed-plugin" and owner:
        return (
            f"Re-enable or reinstall {owner}, then reopen the Picker to refresh "
            f"{entry}{prune_hint}."
        )
    if entry_class == "legacy-plugin":
        return (
            f"Re-enable or update {owner or 'the contributing plugin'} and reopen "
            f"the Picker so {entry} is rewritten with current attribution"
            f"{prune_hint}."
        )
    if entry_class == "operator":
        return (
            f"Fix the operator-owned manifest at {entry}; "
            "agent-worktrees will not remove it."
        )
    if entry_class == "unknown-legacy":
        return (
            f"Fix or remove the unrecognized legacy manifest at {entry} if no "
            f"longer intended{prune_hint}."
        )
    return (
        f"Fix or remove the unrecognized manifest at {entry} if no longer "
        "intended; agent-worktrees will not remove it (it may be hand-edited)."
    )


def _finding(
    entry: Path,
    reason: str,
    *,
    status: str = "inactive",
    target: str | Path | None = None,
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


def _parse_contribution(
    data: Mapping[str, object],
    *,
    path: Path,
    entry_class: str,
    owner: str | None,
) -> PivotContribution:
    pivot = (
        parse_manifest(data, name=path.stem, source_path=str(path))
        if "list" in data
        else None
    )
    worktree_actions = parse_worktree_actions(data, name=path.stem)
    config_sections = parse_config_sections(data, name=path.stem)
    if pivot is None and not worktree_actions and not config_sections:
        raise ManifestError(
            "manifest must contribute a list pivot, worktree action, or config section"
        )
    return PivotContribution(
        entry=path,
        entry_class=entry_class,
        owner=owner,
        pivot=pivot,
        worktree_actions=worktree_actions,
        config_sections=config_sections,
    )


def _activation_decision(
    entry: Path,
    *,
    source: str,
    stored_root: Path | None,
    entry_class: str,
    activation: ActivationReport,
) -> tuple[ActivePlugin | None, EntryDecision[PivotContribution] | None]:
    source_decision = activation.decisions.get(source)
    if (
        activation.authority is ScanAuthority.INDETERMINATE
        or (
            source_decision is not None
            and source_decision.status is EntryStatus.INDETERMINATE
        )
    ):
        return None, EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=stored_root,
                entry_class=entry_class,
                owner=source,
                detail="plugin activation or root evidence is indeterminate",
            )
        )
    if source_decision is None or source_decision.status is EntryStatus.INACTIVE:
        reason = "not-enabled"
        detail = "plugin is not enabled globally or in an adopted project"
        if source_decision is not None and source_decision.findings:
            finding = source_decision.findings[0]
            reason = finding.reason
            detail = finding.detail or detail
        return None, EntryDecision.inactive(
            _finding(
                entry,
                reason,
                target=stored_root,
                entry_class=entry_class,
                owner=source,
                detail=detail,
            )
        )
    return cast(ActivePlugin, source_decision.value), None


def _classify_managed(
    entry: Path,
    data: dict[str, object],
    *,
    activation: ActivationReport,
    legacy: bool = False,
) -> EntryDecision[PivotContribution]:
    """Classify a managed-plugin (pointer) entry.

    ``legacy=True`` handles a superseded schema-v2 fully-baked entry through
    the exact same activation/root/template identity verification as the
    current pointer shape below -- it only relaxes the strict pointer
    key-set check (a v2 payload still carries its old baked ``list``/
    ``actions``/etc. alongside the attribution fields) and marks the result
    advisory instead of plainly active, so a stale or tampered v2 entry is
    deactivated exactly like a current one whenever the plugin is disabled or
    its root no longer matches -- it is never a bypass of those checks.
    """
    source = data.get("plugin")
    raw_root = data.get("plugin_root")
    template_name = data.get("template")
    if (
        (not legacy and set(data) != _MANAGED_POINTER_KEYS)
        or not isinstance(source, str)
        or not _PLUGIN_SOURCE_RE.fullmatch(source)
        or not isinstance(raw_root, str)
        or not raw_root.strip()
        or not isinstance(template_name, str)
        or Path(template_name).name != template_name
        or not template_name.endswith(".json")
    ):
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class="managed-plugin",
                detail=(
                    "managed pointer requires exactly schema_version, plugin, "
                    "plugin_root, and template (schema_version="
                    f"{MANAGED_SCHEMA_VERSION})"
                ),
            )
        )
    stored_root = Path(raw_root).expanduser()
    if not stored_root.is_absolute():
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=stored_root,
                entry_class="managed-plugin",
                owner=source,
                detail="plugin_root must be absolute",
            )
        )
    active, verdict = _activation_decision(
        entry,
        source=source,
        stored_root=stored_root,
        entry_class="managed-plugin",
        activation=activation,
    )
    if verdict is not None:
        return verdict
    active = cast(ActivePlugin, active)
    try:
        canonical_root = stored_root.resolve(strict=True)
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=stored_root,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                target=stored_root,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    live_roots = {selected.root for selected in active.live_roots}
    if canonical_root not in live_roots:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=canonical_root,
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    "manifest root differs from authoritative live plugin roots "
                    + ", ".join(str(root) for root in sorted(live_roots))
                ),
            )
        )

    template_path = canonical_root / "pivots" / template_name
    try:
        template = _read_verified_template(canonical_root, template_name)
        if not isinstance(template, dict):
            raise ManifestError("plugin pivot template must be a JSON object")
        expected = _managed_manifest_data(
            template,
            source=source,
            root=canonical_root,
            template_name=template_name,
            require_targets=True,
        )
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=str(exc.filename or exc),
                entry_class="managed-plugin",
                owner=source,
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "target-unusable",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=template_path,
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    try:
        contribution = _parse_contribution(
            expected,
            path=entry,
            entry_class="managed-plugin",
            owner=source,
        )
    except ManifestError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class="managed-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    if activation.decisions[source].status is EntryStatus.ACTIVE_WITH_ADVISORY:
        advisories = tuple(
            replace(
                finding,
                registry=REGISTRY_NAME,
                entry=str(entry),
                status="active-with-advisory",
                owner=source,
                remedy=_remedy(
                    entry,
                    entry_class="managed-plugin",
                    owner=source,
                ),
            )
            for finding in activation.decisions[source].findings
        )
        if legacy:
            advisories = (
                _finding(
                    entry,
                    "legacy-unattributed",
                    status="active-with-advisory",
                    entry_class="managed-plugin",
                    owner=source,
                    detail=(
                        "superseded schema-v2 (fully-baked) manifest remains "
                        "active for compatibility; the materializer will not "
                        "republish at this schema version again"
                    ),
                ),
                *advisories,
            )
        return EntryDecision.advisory(contribution, *advisories)
    if legacy:
        return EntryDecision.advisory(
            contribution,
            _finding(
                entry,
                "legacy-unattributed",
                status="active-with-advisory",
                entry_class="managed-plugin",
                owner=source,
                detail=(
                    "superseded schema-v2 (fully-baked) manifest remains "
                    "active for compatibility; the materializer will not "
                    "republish at this schema version again"
                ),
            ),
        )
    return EntryDecision.active(contribution)


def _classify_legacy(
    entry: Path,
    data: dict[str, object],
    *,
    source: str,
    activation: ActivationReport,
) -> EntryDecision[PivotContribution]:
    active, verdict = _activation_decision(
        entry,
        source=source,
        stored_root=None,
        entry_class="legacy-plugin",
        activation=activation,
    )
    if verdict is not None:
        return verdict
    active = cast(ActivePlugin, active)
    template_path = active.root / "pivots" / entry.name
    matched_root: Path | None = None
    for selected in active.live_roots:
        candidate = selected.root / "pivots" / entry.name
        try:
            if _read_json(candidate) == data:
                matched_root = selected.root
                template_path = candidate
                break
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        except OSError as exc:
            return EntryDecision.indeterminate(
                _finding(
                    entry,
                    "target-unusable",
                    status="indeterminate",
                    target=candidate,
                    entry_class="legacy-plugin",
                    owner=source,
                    detail=str(exc),
                )
            )
    if matched_root is None:
        return EntryDecision.inactive(
            _finding(
                entry,
                "identity-mismatch",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail="legacy manifest differs from every active plugin template",
            )
        )
    try:
        resolved = _rewrite_manifest_commands(
            data,
            root=matched_root,
            require_targets=True,
        )
        contribution = _parse_contribution(
            resolved,
            path=entry,
            entry_class="legacy-plugin",
            owner=source,
        )
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=str(exc.filename or exc),
                entry_class="legacy-plugin",
                owner=source,
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "target-unusable",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "target-unusable",
                status="indeterminate",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                target=template_path,
                entry_class="legacy-plugin",
                owner=source,
                detail=str(exc),
            )
        )
    return EntryDecision.advisory(
        contribution,
        _finding(
            entry,
            "legacy-unattributed",
            status="active-with-advisory",
            target=template_path,
            entry_class="legacy-plugin",
            owner=source,
            detail="legacy manifest remains active during attribution migration",
        ),
    )


def _classify_unattributed(
    entry: Path,
    data: dict[str, object],
    *,
    entry_class: str,
    advisory: bool,
) -> EntryDecision[PivotContribution]:
    try:
        resolved = _rewrite_manifest_commands(
            data,
            root=None,
            require_targets=True,
        )
        contribution = _parse_contribution(
            resolved,
            path=entry,
            entry_class=entry_class,
            owner=None,
        )
    except FileNotFoundError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "missing-target",
                target=str(exc.filename or exc),
                entry_class=entry_class,
            )
        )
    except TargetUnusableError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "target-unusable",
                entry_class=entry_class,
                detail=str(exc),
            )
        )
    except OSError as exc:
        return EntryDecision.indeterminate(
            _finding(
                entry,
                "entry-indeterminate",
                status="indeterminate",
                entry_class=entry_class,
                detail=str(exc),
            )
        )
    except ManifestError as exc:
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class=entry_class,
                detail=str(exc),
            )
        )
    if not advisory:
        return EntryDecision.active(contribution)
    return EntryDecision.advisory(
        contribution,
        _finding(
            entry,
            "legacy-unattributed",
            status="active-with-advisory",
            entry_class=entry_class,
            detail="unattributed schema-v1 manifest remains active for compatibility",
        ),
    )
