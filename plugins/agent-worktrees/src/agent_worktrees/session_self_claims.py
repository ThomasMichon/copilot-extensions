"""Original qualified session claims belong to a proven receiving subject."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import ResourceClaim, WorktreeRecord


def self_claims(record: WorktreeRecord, session_id: str) -> list[ResourceClaim]:
    """Keep project, worktree and tracked session exact across reporter renames."""
    from . import record_reporting, tracking

    if record.session_entry(session_id) is None:
        return []
    current = []
    for claim in record.resources:
        if claim.kind != "session" or claim.metadata_opaque or claim.has_producer:
            continue
        ref = tracking.parse_claim_ref(claim.ref)
        if (
            ref is None or not ref.is_qualified or ref.canonical() != claim.ref
            or (ref.project, ref.worktree_id, ref.session)
            != (record.repo, record.worktree_id, session_id)
        ):
            continue
        if record_reporting.historical_owner_is_local(record, ref.machine):
            current.append(claim)
    return current
