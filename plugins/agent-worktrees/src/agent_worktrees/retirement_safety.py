"""Non-bypassable responsibility checks before checkout or record retirement."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


@dataclass(frozen=True)
class RetirementBlocker:
    bucket: str
    reason: str


def retirement_blocker(record: WorktreeRecord) -> RetirementBlocker | None:
    """Require explicit terminal PR state and released outbound ownership."""
    pending = [pr for pr in record.prs if pr.state not in ("merged", "closed")]
    if pending:
        numbers = ", ".join(
            "?" if pr.number is None else f"#{pr.number}" for pr in pending
        )
        return RetirementBlocker(
            "open-pr", f"PR {numbers} still open/in review or state unconfirmed"
        )
    held = [claim for claim in record.resources if claim.is_live or claim.is_unsettled]
    if held:
        return RetirementBlocker(
            "held-claims", f"{len(held)} held resource claim(s) pending; inspect claims"
        )
    return None


def require_retirable(record: WorktreeRecord) -> None:
    blocker = retirement_blocker(record)
    if blocker is not None:
        raise ValueError(f"Cannot retire {record.worktree_id}: {blocker.reason}")


def same_pair(before: WorktreeRecord, after: WorktreeRecord) -> bool:
    return (
        before.is_paired,
        before.pair_id,
        before.pair_kind,
        before.pair_role,
        before.pair_ref,
    ) == (
        after.is_paired,
        after.pair_id,
        after.pair_kind,
        after.pair_role,
        after.pair_ref,
    )


def delete_retired_pair(
    record: WorktreeRecord, sibling: WorktreeRecord, path: Path, sibling_path: Path | None,
) -> bool:
    """Keep both records until receiving conclusions survive their hard deletion."""
    from . import paired_claims, receiving_allocation, tracking

    try:
        if sibling_path is None:
            raise ValueError("paired retirement has no exact sibling record path")
        receiving_allocation.recover_missing(record, path.parent)
        receiving_allocation.recover_missing(sibling, sibling_path.parent)
        if ref := paired_claims.paired_ref(record):
            paired_claims.require_clearable(record, ref)
        record.status = "archived" if record.status == "archived" else "finalized"
        now = tracking._now_iso()
        if record.completed_at is None:
            record.completed_at = now
        record.reaped_at = now
        tracking.save_record(record, path=path)
        receiving_allocation.persist_conclusion(sibling)
    except Exception as exc:
        logging.getLogger(__name__).warning("Paired retirement deferred; records retained: %s", exc)
        return False
    sibling_path.unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    return True
