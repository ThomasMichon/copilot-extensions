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
            records = tracking.list_records(tracking_dir, include_anchor=True)
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


def claims_of_owner(
    worktree_id: str,
    kind: str,
    *,
    include_released: bool = False,
) -> list[str]:
    """Refs of ``kind`` that ``worktree_id`` itself holds an outbound claim on.

    The forward counterpart to :func:`find_claim_owners` (which resolves a ref
    to its owner): given an owner, list what it already claims. Used to resume
    a workstream's own previously-claimed resource (e.g. a persistent
    CodeSpace, Phase 2b / codespace-venue-pool) rather than re-deriving it from
    scratch each request. Best-effort across every registered project on this
    machine (a worktree id is looked up by exact match, not project-qualified);
    degrades to ``[]`` on any registry/record trouble.
    """
    if not worktree_id or not kind:
        return []
    refs: list[str] = []
    try:
        for _project, record in _iter_records():
            if record.worktree_id != worktree_id:
                continue
            for claim in getattr(record, "resources", []) or []:
                try:
                    if claim.kind != kind:
                        continue
                    if not include_released and not claim.is_live:
                        continue
                    refs.append(claim.ref)
                except Exception:
                    continue
    except Exception:
        return []
    return refs


def _owner_ref_parent(worktree_id: str) -> str | None:
    """The worktree id that created ``worktree_id`` (its ``owner_ref``), if any.

    Best-effort, single project-registry scan; a worktree id is looked up by
    exact match across every registered project (ids are machine-scoped, not
    globally unique, but collisions across unrelated projects are vanishingly
    rare and this degrades to "no relation found" rather than a wrong one).
    """
    for _project, record in _iter_records():
        if record.worktree_id != worktree_id:
            continue
        parsed = getattr(record, "owner_claim_ref", None)
        if parsed is None and record.owner_ref:
            from . import tracking_claims

            parsed = tracking_claims.parse_claim_ref(record.owner_ref)
        if parsed is not None and parsed.worktree_id:
            return parsed.worktree_id
    return None


def _ancestor_chain(worktree_id: str, *, max_depth: int = 32) -> set[str]:
    """``worktree_id``'s own id plus every ancestor found by walking
    ``owner_ref`` upward. Bounded depth guards against a (should-never-happen)
    reference cycle."""
    chain = {worktree_id}
    current = worktree_id
    for _ in range(max_depth):
        parent = _owner_ref_parent(current)
        if not parent or parent in chain:
            break
        chain.add(parent)
        current = parent
    return chain


def same_worktree_family(worktree_a: str, worktree_b: str) -> bool:
    """True when ``worktree_a`` and ``worktree_b`` are the same worktree, or one
    directly or transitively created the other (an ``owner_ref`` ancestor chain
    connects them either direction).

    Used to recognize a resource claim (e.g. a CodeSpace) taken by a parent
    worktree as legitimately reachable by its own child worktree (and vice
    versa) without requiring an explicit force-takeover -- they are the same
    task lineage, not a foreign contender. Best-effort + degrade-safe: any
    lookup failure (missing registry, unreadable record) yields ``False`` (the
    prior, stricter behavior), never a false family match.
    """
    if not worktree_a or not worktree_b:
        return False
    if worktree_a == worktree_b:
        return True
    try:
        chain_a = _ancestor_chain(worktree_a)
        if worktree_b in chain_a:
            return True
        chain_b = _ancestor_chain(worktree_b)
        return worktree_a in chain_b
    except Exception:
        return False


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
