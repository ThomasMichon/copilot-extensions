"""Pivot registry scanning, classification, and pruning."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    scan_directory,
)
from plugin_activation import ActivationReport, ActivePlugin

from .pivot_actions import ManifestError, parse_config_sections, parse_worktree_actions
from .pivot_manifest import (
    _KNOWN_LEGACY_PIVOTS,
    _LAST_KNOWN,
    _MANAGED_POINTER_KEYS,
    _PLUGIN_SOURCE_RE,
    _WARNING_TRACKER,
    MANAGED_SCHEMA_VERSION,
    REGISTRY_NAME,
    PivotContribution,
    PivotRegistryReport,
    RegisteredPivot,
    _compat_manifest_documents,
    _pivot_is_visible,
    _resolve_activation,
    _resolve_compat_document,
    _resolve_state_root_path,
    parse_manifest,
    pivots_dir,
)
from .pivot_targets import (
    TargetUnusableError,
    _activation_from_plugins_root,
    _managed_manifest_data,
    _materialize_active_pivots,
    _read_json,
    _rewrite_manifest_commands,
)

log = logging.getLogger("agent-worktrees")


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
) -> EntryDecision[PivotContribution]:
    source = data.get("plugin")
    raw_root = data.get("plugin_root")
    template_name = data.get("template")
    if (
        set(data) != _MANAGED_POINTER_KEYS
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
        template = _read_json(template_path)
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
    # No baked-content comparison: `data` is a pointer, `expected` is always
    # freshly re-resolved from the identity-verified template above -- there
    # is nothing on disk that can drift out of sync with it.
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
        return EntryDecision.advisory(contribution, *advisories)
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


def _withdraw_confirmed_disappearances(
    snapshot: ScanSnapshot[PivotContribution],
) -> ScanSnapshot[PivotContribution]:
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
    return replace(
        snapshot,
        decisions=decisions,
        findings=tuple(findings),
    )


def scan_pivot_registry(
    base: str | os.PathLike[str] | None = None,
    *,
    previous: Mapping[str, PivotContribution] | None = None,
    activation_report: ActivationReport | None = None,
    materialize: bool = True,
) -> PivotRegistryReport:
    """Scan, classify, reconcile, and de-duplicate Picker contributions."""
    directory = pivots_dir(base)
    activation = activation_report or _resolve_activation()
    if materialize:
        _materialize_active_pivots(directory, activation)
    entry_classes: dict[str, str] = {}

    def classify(entry: Path) -> EntryDecision[PivotContribution]:
        try:
            data = _read_json(entry)
        except UnicodeDecodeError as exc:
            entry_classes[str(entry)] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail=f"manifest is not valid UTF-8: {exc}",
                )
            )
        except json.JSONDecodeError as exc:
            entry_classes[str(entry)] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail=f"manifest is not valid JSON: {exc}",
                )
            )
        if not isinstance(data, dict):
            entry_classes[str(entry)] = "unknown"
            return EntryDecision.inactive(
                _finding(
                    entry,
                    "invalid-entry",
                    entry_class="unknown",
                    detail="manifest root must be a JSON object",
                )
            )
        schema = data.get("schema_version")
        if schema == MANAGED_SCHEMA_VERSION:
            entry_classes[str(entry)] = "managed-plugin"
            return _classify_managed(entry, data, activation=activation)
        if schema == 2:
            # Superseded fully-baked managed shape (pre-pointer redesign).
            # Route through the same unattributed/advisory path as a v1
            # legacy manifest so a pre-existing on-disk file keeps
            # contributing (and is prunable) while it decays -- the
            # materializer never republishes at this schema version again.
            entry_classes[str(entry)] = "unknown-legacy"
            return _classify_unattributed(
                entry,
                data,
                entry_class="unknown-legacy",
                advisory=True,
            )
        if schema == 1:
            source = _KNOWN_LEGACY_PIVOTS.get(entry.name)
            if source:
                entry_classes[str(entry)] = "legacy-plugin"
                return _classify_legacy(
                    entry,
                    data,
                    source=source,
                    activation=activation,
                )
            entry_classes[str(entry)] = "unknown-legacy"
            return _classify_unattributed(
                entry,
                data,
                entry_class="unknown-legacy",
                advisory=True,
            )
        if schema is None:
            entry_classes[str(entry)] = "operator"
            return _classify_unattributed(
                entry,
                data,
                entry_class="operator",
                advisory=False,
            )
        entry_classes[str(entry)] = "unknown"
        return EntryDecision.inactive(
            _finding(
                entry,
                "invalid-entry",
                entry_class="unknown",
                detail=f"unsupported schema_version {schema!r}",
            )
        )

    snapshot = scan_directory(
        directory,
        classify,
        registry=REGISTRY_NAME,
        suffixes=(".json",),
    )
    snapshot = _withdraw_confirmed_disappearances(snapshot)
    if snapshot.findings:
        snapshot = replace(
            snapshot,
            findings=tuple(
                replace(
                    finding,
                    remedy=(
                        (
                            f"Restore readable access to {directory}, then run "
                            "`agent-worktrees doctor` again; current pivots are retained."
                        )
                        if finding.reason == "registry-indeterminate"
                        else _remedy(
                            Path(finding.entry),
                            entry_class=entry_classes.get(
                                finding.entry, "unknown"
                            ),
                            owner=finding.owner,
                        )
                    ),
                )
                if not finding.remedy
                else finding
                for finding in snapshot.findings
            ),
        )

    try:
        root_key = str(directory.expanduser().resolve(strict=False))
    except OSError:
        root_key = os.path.abspath(os.path.expanduser(str(directory)))
    prior = dict(previous) if previous is not None else dict(
        _LAST_KNOWN.get(root_key, {})
    )
    reconciled = snapshot.reconcile(prior)
    active_entries = dict(reconciled)

    decisions = dict(snapshot.decisions)
    findings = list(snapshot.findings)
    owners: dict[str, str] = {}
    precedence = {
        "operator": 0,
        "managed-plugin": 1,
        "legacy-plugin": 2,
        "unknown-legacy": 3,
    }
    ordered_entries = sorted(
        active_entries,
        key=lambda key: (
            precedence.get(active_entries[key].entry_class, 4),
            key,
        ),
    )
    for key in ordered_entries:
        contribution = active_entries[key]
        duplicate_of = next(
            (owners[identity] for identity in contribution.identities if identity in owners),
            None,
        )
        if duplicate_of is None:
            for identity in contribution.identities:
                owners[identity] = key
            continue
        active_entries.pop(key)
        current = decisions.get(key)
        if current is None or current.status is EntryStatus.INDETERMINATE:
            continue
        duplicate = _finding(
            Path(key),
            "duplicate",
            target=duplicate_of,
            entry_class=entry_classes.get(key, "unknown"),
            owner=contribution.owner,
            detail="a prior active entry already claims one of this manifest's identities",
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
        _LAST_KNOWN[root_key] = reconciled
    return PivotRegistryReport(
        snapshot=snapshot,
        active_entries=active_entries,
        entry_classes=entry_classes,
    )


def warn_pivot_findings(report: PivotRegistryReport) -> None:
    """Emit bounded, fingerprint-deduplicated Picker registry warnings."""
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


#: Findings a plugin's own runtime produced (never operator-authored) whose
#: entry is provably superseded: either its content no longer matches the
#: plugin's live template ("identity-mismatch"/"invalid-entry"/
#: "missing-target"), or another entry already claims its identity
#: ("duplicate"). In every case the plugin's correct, current manifest already
#: exists elsewhere in the registry, so removing the stale file loses no
#: reachable state -- it accumulates purely because ``_materialize_active_
#: pivots`` never overwrites or deletes an existing file (see its docstring).
_PRUNABLE_REASONS = frozenset(
    {"duplicate", "identity-mismatch", "missing-target", "invalid-entry"}
)

#: Entry classes that are always plugin-generated, never hand-authored by an
#: operator. ``"operator"`` (schema_version absent) and ``"unknown"``
#: (unparseable JSON/UTF-8, which may be a hand-edit gone wrong) are
#: deliberately excluded: those files may carry content only a human can
#: judge, and their own remedy text promises "agent-worktrees will not
#: remove it" -- pruning must not silently break that promise.
_PRUNABLE_ENTRY_CLASSES = frozenset(
    {"managed-plugin", "legacy-plugin", "unknown-legacy"}
)


def prunable_findings(report: PivotRegistryReport) -> list[Finding]:
    """The subset of ``report.findings`` safe to auto-remove.

    Always a strict subset of the *inactive* findings: never an operator-owned
    or unparseable-class entry, and never an indeterminate/advisory finding
    (those need a human, not automation).
    """
    return [
        finding
        for finding in report.findings
        if finding.status == "inactive"
        and finding.reason in _PRUNABLE_REASONS
        and report.entry_classes.get(finding.entry) in _PRUNABLE_ENTRY_CLASSES
    ]


def prune_stale_entries(
    report: PivotRegistryReport,
    *,
    base: str | os.PathLike[str] | None = None,
    apply: bool = False,
) -> list[dict[str, object]]:
    """Remove pivot manifest files that :func:`prunable_findings` proved stale.

    Dry-run by default (``apply=False``): returns the plan (one dict per
    candidate, with ``removed`` always ``False``) without touching disk.
    ``doctor --fix --prune-pivots`` is the only caller that passes
    ``apply=True`` -- this is deliberately not part of plain ``--fix``, since
    every one of these findings' own remedy text otherwise promises
    "agent-worktrees will not remove it"; pruning is an explicit,
    separately-opted-into escalation of that promise, not its default.

    Each candidate is re-resolved against the live pivots directory
    immediately before deletion (defends against a manifest outside the
    registry root, e.g. a stale finding computed against a different
    ``base``) and a missing file is treated as already-removed, not an error.
    """
    directory = pivots_dir(base)
    try:
        registry_root = directory.resolve(strict=False)
    except OSError:
        registry_root = directory
    results: list[dict[str, object]] = []
    for finding in prunable_findings(report):
        entry_path = Path(finding.entry)
        outcome: dict[str, object] = {
            "entry": str(entry_path),
            "reason": finding.reason,
            "owner": finding.owner,
            "removed": False,
        }
        try:
            resolved = entry_path.resolve(strict=False)
        except OSError as exc:
            outcome["error"] = str(exc)
            results.append(outcome)
            continue
        if resolved.parent != registry_root:
            outcome["error"] = "entry is outside the pivots registry directory"
            results.append(outcome)
            continue
        if not apply:
            results.append(outcome)
            continue
        try:
            resolved.unlink()
            outcome["removed"] = True
        except FileNotFoundError:
            outcome["removed"] = True  # already gone -- not an error
        except OSError as exc:
            outcome["error"] = str(exc)
        results.append(outcome)
    return results


def ensure_pivots(
    base: str | os.PathLike[str] | None = None,
    plugins_root: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Materialize active plugin manifests as attributed runtime entries.

    Cached installed payloads are never authority by themselves. Only roots
    returned by :func:`resolve_active_plugins` may create or refresh a managed
    pivot entry. Existing operator/unknown entries are never overwritten.

    ``plugins_root`` remains a test-only compatibility input. When supplied,
    candidates are discovered from that synthetic tree and treated as active.
    """
    activation = (
        _activation_from_plugins_root(Path(plugins_root))
        if plugins_root is not None
        else _resolve_activation()
    )
    return _materialize_active_pivots(pivots_dir(base), activation)


def discover_pivots(base: str | os.PathLike[str] | None = None) -> list[RegisteredPivot]:
    """Return active pivots, with parser-only explicit-dir support."""
    candidates: list[RegisteredPivot] = []
    for path, data in _compat_manifest_documents(pivots_dir(base)):
        resolved = _resolve_compat_document(path, data)
        if resolved is None or "list" not in resolved:
            continue
        try:
            candidates.append(
                parse_manifest(resolved, name=path.stem, source_path=str(path))
            )
        except ManifestError:
            continue
    state_root = (
        _resolve_state_root_path()
        if any(pivot.visible_when_state_root_file for pivot in candidates)
        else None
    )
    return [
        pivot
        for pivot in candidates
        if _pivot_is_visible(pivot, state_root=state_root)
    ]


def order_pivots(builtins: Sequence[str], registered: Sequence[RegisteredPivot]) -> list[dict]:
    """Weave registered pivots into the builtin order via their ``after`` hint.

    Returns a list of pivot descriptors (dicts) in final display order. Each is
    ``{"label", "kind", "pivot"}``; builtins carry ``pivot=None`` and a kind of
    their lowercased label, registered pivots carry ``kind="registered"`` and
    their :class:`RegisteredPivot`. A registered pivot whose ``after`` matches
    no builtin is appended at the end (still shown, never dropped).
    """
    descriptors: list[dict] = [
        {"label": b, "kind": b.strip().lower(), "pivot": None} for b in builtins
    ]
    for reg in registered:
        entry = {"label": reg.label, "kind": "registered", "pivot": reg}
        idx = next(
            (i for i, d in enumerate(descriptors) if d["label"].lower() == reg.after.lower()),
            None,
        )
        if idx is None:
            descriptors.append(entry)
        else:
            descriptors.insert(idx + 1, entry)
    return descriptors
