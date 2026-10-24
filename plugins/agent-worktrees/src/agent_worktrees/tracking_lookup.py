"""Exact record lookups, re-exported by the tracking facade."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


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
    Only same-machine qualified refs resolve. A legacy record misplaced in the
    current project's directory is deliberately not accepted:
    ``state-root --pair`` must surface that broken pair until ``doctor --fix``
    copies the record to its owning project registry.
    """
    from . import config as cfg, tracking

    ref = record.pair_claim_ref
    if ref is None:
        return None
    if ref.is_qualified and ref.machine == record.execution_machine and ref.project:
        return tracking.load_record_by_id(
            ref.worktree_id,
            tracking_path=cfg.project_dir(ref.project) / "worktrees",
        )
    return None
