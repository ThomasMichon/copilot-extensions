"""Report an already-committed handoff without duplicating retirement stages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from . import activity, handoff_diagnostics

if TYPE_CHECKING:
    from .tracking import SessionHandoff


def emit_claim_stages(
    wt_id: str, session_id: str, linked_handoff: SessionHandoff | None, *,
    launch_id: str | None, emit_stage_13: Callable[..., Any],
) -> None:
    if linked_handoff is None:
        return
    activity.log_event(
        "handoff_successor_claimed",
        worktree_id=wt_id,
        session_id=session_id,
        handoff_token=linked_handoff.token,
        predecessor_session_id=linked_handoff.predecessor,
        successor_session_id=session_id,
        launch_id=launch_id,
    )
    already_retired = any(
        str(e.get("handoff_token") or "").strip() == linked_handoff.token
        and e.get("outcome") == "gone"
        for e in activity.read_events(worktree_id=wt_id, event="handoff_predecessor_retire")
    )
    if not already_retired:
        activity.log_event(
            "handoff_pickup_confirmed_predecessor_closing",
            worktree_id=wt_id,
            session_id=linked_handoff.predecessor,
            successor_session_id=session_id,
            handoff_token=linked_handoff.token,
            launch_id=launch_id,
        )
    handoff_diagnostics.stamp_session_state_handoff(
        linked_handoff.predecessor,
        handoff_token=linked_handoff.token,
        successor_session_id=session_id,
    )
    emit_stage_13(wt_id, linked_handoff.token, launch_id=launch_id)
