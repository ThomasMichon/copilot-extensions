"""Reverse lookup from an outbound claim to its owning worktree."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

from . import config as cfg
from . import installer, output, tracking


def _project_names() -> list[str]:
    try:
        projects = installer.read_projects_registry().get("projects", {})
    except Exception:
        return []
    if not isinstance(projects, dict):
        return []
    return [str(name) for name in projects if str(name)]


def _tracking_dir(project: str) -> Path | None:
    try:
        return cfg.project_dir(project) / "worktrees"
    except Exception:
        return None


def _iter_records() -> Iterable[tuple[str, tracking.WorktreeRecord]]:
    for project in _project_names():
        tracking_dir = _tracking_dir(project)
        if tracking_dir is None:
            continue
        try:
            records = tracking.list_records(tracking_dir)
        except Exception:
            continue
        for record in records:
            yield project, record


def _owner_dict(
    project: str,
    record: tracking.WorktreeRecord,
    claim: tracking.ResourceClaim,
) -> dict:
    qualified_ref = tracking.format_claim_ref(
        record.machine,
        project,
        record.worktree_id,
    )
    return {
        "project": project,
        "worktree_id": record.worktree_id,
        "worktree_path": record.worktree_path,
        "path": record.worktree_path,
        "status": record.status,
        "qualified_ref": qualified_ref,
        "owner_ref": qualified_ref,
        "state": claim.state,
        "note": claim.note,
    }


def find_claim_owners(
    kind: str,
    ref: str,
    *,
    include_released: bool = False,
) -> list[dict]:
    """Find local worktree records that hold ``kind``/``ref``.

    Searches every registered project on this machine. Bad registry entries,
    unreadable project state, and malformed records are skipped: reverse
    resolution is a diagnostic helper and must not wedge its callers.
    """
    if not kind or not ref:
        return []
    return find_claim_owners_for_refs(
        kind,
        [ref],
        include_released=include_released,
    ).get(ref, [])


def find_claim_owners_for_refs(
    kind: str,
    refs: Iterable[str],
    *,
    include_released: bool = False,
) -> dict[str, list[dict]]:
    """Find local claim owners for many refs with one project-registry scan."""
    wanted = {str(ref) for ref in refs if str(ref)}
    owners = {ref: [] for ref in wanted}
    if not kind or not wanted:
        return owners
    for project, record in _iter_records():
        for claim in getattr(record, "resources", []) or []:
            try:
                claim_kind = claim.kind
                claim_ref = claim.ref
                live = claim.is_live
            except Exception:
                continue
            if claim_kind != kind or claim_ref not in wanted:
                continue
            if not include_released and not live:
                continue
            try:
                owners[claim_ref].append(_owner_dict(project, record, claim))
            except Exception:
                continue
    return owners


def _emit_human(kind: str, ref: str, owners: list[dict]) -> None:
    if not owners:
        output.err(f"No owner found for {kind} {ref}")
        return
    output.header(f"Claim owners for {kind} {ref}")
    for owner in owners:
        output.info(
            f"{owner['project']} / {owner['worktree_id']} "
            f"({owner['status']})"
        )
        output.info(f"ref: {owner['qualified_ref']}")
        if owner.get("worktree_path"):
            output.info(f"path: {owner['worktree_path']}")
        state = owner.get("state") or "active"
        note = owner.get("note") or ""
        output.info(f"claim: state={state}" + (f" note={note}" if note else ""))


def cmd_claims_owner(args: argparse.Namespace, target: list[str]) -> int:
    if len(target) < 2:
        msg = "claims owner: usage 'owner <kind> <ref>'"
        if args.json:
            output._json_output({"error": msg})
        else:
            output.err(msg)
        return 2
    kind, ref = target[0], target[1]
    owners = find_claim_owners(
        kind,
        ref,
        include_released=bool(getattr(args, "all_states", False)),
    )
    if args.json:
        output._json_output({"kind": kind, "ref": ref, "owners": owners})
    else:
        _emit_human(kind, ref, owners)
    return 0 if owners else 1
