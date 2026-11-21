"""Non-bypassable responsibility checks before checkout or record retirement."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import PRRecord, WorktreeRecord


@dataclass(frozen=True)
class RetirementBlocker:
    bucket: str
    reason: str


def retains_artifact_metadata(record: WorktreeRecord) -> bool:
    """Keep retained-byte custody and uncertain publication history after retirement."""
    from . import pr_creation_reconcile

    return any(pr_creation_reconcile.requires_qualification(pr) for pr in record.prs) or any(
        claim.has_producer and isinstance(claim.producer, dict)
        and (
            claim.producer.get("lifetime") == "artifact-owned"
            and claim.producer.get("phase") == "retained"
            or claim.producer.get("lifetime") == "user-deliverable"
            and claim.producer.get("phase") == "delivered"
        )
        for claim in record.resources
    )


def record_paths(record: WorktreeRecord, tracking_path: Path) -> list[Path]:
    """Lock the same complete owner set before teardown and ledger retirement."""
    from . import config as cfg

    paths = {tracking_path / f"{record.worktree_id}.yaml"}
    ref = record.pair_claim_ref if record.is_paired else None
    if ref is not None and ref.is_qualified and ref.project:
        paths.add(cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml")
    return sorted(paths, key=str)


def pending_prs(record: WorktreeRecord, *, live_only: bool = False) -> list[PRRecord]:
    """Preserve each consumer's terminal-state policy and require held receipt proof."""
    from . import pr_creation_reconcile, tracking

    pending = []
    for pr in record.prs:
        if pr_creation_reconcile.requires_qualification(pr):
            held = not pr_creation_reconcile.qualified(record, pr)
        else:
            held = pr.state in tracking._PR_NON_TERMINAL if live_only else pr.state not in ("merged", "closed")
        if held:
            pending.append(pr)
    return pending


def retirement_blocker(record: WorktreeRecord) -> RetirementBlocker | None:
    """Require terminal or freshly fenced PR disposition and released ownership."""
    pending = pending_prs(record)
    if pending:
        numbers = ", ".join(
            "?" if pr.number is None else f"#{pr.number}" for pr in pending
        )
        return RetirementBlocker(
            "open-pr", f"PR {numbers} still open/in review or state unconfirmed"
        )
    from . import session_lease

    held = [
        claim for claim in record.resources
        if (claim.is_live or claim.is_unsettled)
        and not session_lease.expired_finalized_self_claim(record, claim)
    ]
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
    from . import tracking_save_notifications as save_notifications

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
    if retains_artifact_metadata(record) or retains_artifact_metadata(sibling):
        return True
    sibling_path.unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    # Both removals must reach the resident; the save above announced only
    # ``record``'s path (delivered once the record holds release).
    save_notifications.worktree_changed(record.worktree_path)
    save_notifications.worktree_changed(sibling.worktree_path)
    return True
