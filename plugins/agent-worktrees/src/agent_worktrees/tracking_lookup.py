"""Exact record lookups, re-exported by the tracking facade."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import ResourceClaim, WorktreeRecord


def load_record_by_id(
    worktree_id: str,
    *,
    tracking_path: Path | None = None,
) -> WorktreeRecord | None:
    """Load a tracked worktree record by id from a tracking directory.

    Returns ``None`` when the id is empty, no record file exists, or the file
    is unreadable/malformed. Fail-safe -- never raises.
    """
    from . import config as cfg, tracking

    if not worktree_id:
        return None
    path = (tracking_path or cfg.tracking_dir()) / f"{worktree_id}.yaml"
    if not path.exists():
        return None
    try:
        return tracking.load_record(path)
    except Exception:
        return None


def find_paired_record(record: WorktreeRecord) -> WorktreeRecord | None:
    """Resolve the SIBLING record of a paired worktree, or ``None``.

    Reads ``record.pair_ref`` (a :class:`ClaimRef` to the sibling) and loads that
    worktree's record from the referenced project's tracking directory.
    Current local reporters qualify historical reciprocal refs independently
    of their old machine spelling. A legacy record misplaced in the
    current project's directory is deliberately not accepted:
    ``state-root --pair`` must surface that broken pair until ``doctor --fix``
    copies the record to its owning project registry.
    """
    from . import config as cfg, tracking

    ref = record.pair_claim_ref
    if ref is None:
        return None
    if not ref.is_qualified or not ref.project:
        return None
    sibling = tracking.load_record_by_id(
        ref.worktree_id,
        tracking_path=cfg.project_dir(ref.project) / "worktrees",
    )
    if ref.machine == record.execution_machine:
        return sibling
    return sibling if sibling is not None and local_pair_subjects(record, sibling) else None


def local_pair_subjects(record: WorktreeRecord, sibling: WorktreeRecord) -> bool:
    """Verify reciprocal allocation subjects on the same actual reporting host."""
    if not reciprocal_pair_subjects(record, sibling):
        return False
    return local_receiving_subjects(record, sibling)


def local_receiving_subjects(record: WorktreeRecord, sibling: WorktreeRecord) -> bool:
    """Verify both current receiving subjects without following ownership again."""
    from . import execution_spaces
    from .record_reporting import require_current_subject, same_reporter

    try:
        for member in (record, sibling):
            context = getattr(member, "_reporting_context", None)
            receiving = execution_spaces.project_record_config(member)
            if context is None or getattr(context, "unavailable", False):
                return False
            require_current_subject(member, receiving)
        if not same_reporter(record, sibling):
            return False
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def original_owned_claim(record: WorktreeRecord, parent: WorktreeRecord) -> ResourceClaim | None:
    """Resolve ordinary ownership through the exact original reciprocal claim."""
    from . import record_cache, tracking

    owner = record.owner_claim_ref
    if (
        owner is None or not owner.is_qualified or record.pair_role == "knowledge"
        or (owner.project, owner.worktree_id) != (parent.repo, parent.worktree_id)
        or not local_receiving_subjects(record, parent)
    ):
        return None
    current = record_cache.cached_load(
        record.yaml_path, tracking._load_record_uncached, copy_result=False,
    )
    if current.owner_ref != record.owner_ref or current.status != record.status:
        return None
    candidates = []
    for claim in parent.resources:
        ref = tracking.parse_claim_ref(claim.ref)
        if (
            claim.kind == "worktree" and not claim.metadata_opaque and not claim.has_producer
            and ref is not None and ref.is_qualified and not ref.session
            and ref.machine == owner.machine
            and (ref.project, ref.worktree_id) == (record.repo, record.worktree_id)
        ):
            candidates.append(claim)
    return candidates[0] if len(candidates) == 1 else None


def reciprocal_pair_subjects(record: WorktreeRecord, sibling: WorktreeRecord) -> bool:
    """Compare the original reciprocal relationship, not refreshed reporter labels."""
    ref, back = record.pair_claim_ref, sibling.pair_claim_ref
    return bool(
        ref is not None and back is not None and ref.is_qualified and back.is_qualified
        and (ref.project, ref.worktree_id) == (sibling.repo, sibling.worktree_id)
        and (back.project, back.worktree_id) == (record.repo, record.worktree_id)
        and ref.machine == back.machine
        and record.pair_id and record.pair_id == sibling.pair_id
        and record.pair_kind == sibling.pair_kind == "worktree"
        and {record.pair_role, sibling.pair_role} == {"harness", "knowledge"}
    )
