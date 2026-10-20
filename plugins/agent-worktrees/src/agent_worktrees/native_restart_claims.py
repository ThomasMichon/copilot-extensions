"""The original native session's own claim is not new recovery authority."""

from __future__ import annotations

from . import tracking


def original_session_only(record: tracking.WorktreeRecord, native_session_id: str) -> bool:
    """Admit no obligations except one exact, active, producer-free self claim."""
    if (
        record.last_finalize_released or record.owner_ref
        or record.dispatch_attempt is not None or record.dispatch_attempt_opaque
        or record.dispatch_attempt_raw_present or len(record.resources) > 1
    ):
        return False
    if not record.resources:
        return True
    claim = record.resources[0]
    return bool(
        record.execution_machine and record.repo
        and claim.kind == "session" and claim.state == "active"
        and not claim.has_producer and not claim.metadata_opaque
        and claim.ref == tracking.format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session=native_session_id,
        )
    )
