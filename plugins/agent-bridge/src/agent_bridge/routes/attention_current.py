"""``GET /api/v1/sessions/{session_ref}/attention/current``: the current
attention reason of an owned or a represented session (see
:mod:`agent_bridge.attention_current`). Mounted under the sessions router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..attention_current import (
    CurrentAttention,
    current_owned_attention,
    current_represented_attention,
)

router = APIRouter()


@router.get("/{session_ref}/attention/current", response_model=CurrentAttention)
def get_current_attention(session_ref: str, request: Request) -> CurrentAttention:
    """Owned sessions first (a session id or a worktree handle whose owner
    runs), then the represented session a live registration or worktree head
    names. Never waits."""
    from ..events import EventLog
    from ..result_snapshot import build_represented_result_snapshot
    from .live_sessions import _db, _resolve_registration, _result_history, _to_info
    from .sessions import _resolve_result_session

    mgr = request.app.state.session_manager
    try:
        session = _resolve_result_session(mgr, session_ref)
    except HTTPException as exc:
        if exc.status_code != 409:  # 409: the worktree head is a represented session
            raise
        session = None
    if session is not None:
        if session.event_log is None:
            raise HTTPException(
                status_code=409,
                detail="Session history is not loaded in the active bridge generation",
            )
        return current_owned_attention(session, session_ref)
    db = _db(request)
    row = _resolve_registration(db, session_ref)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Session or worktree {session_ref} not found")
    log, _history = _result_history(request, row["session_id"])
    if log is None:
        log = EventLog(session_id=row["session_id"], worktree_id=row.get("worktree_id"),
                       telemetry_source="represented")
    registration = _to_info(row).model_dump(mode="json")
    snapshot = build_represented_result_snapshot(
        registration=registration, event_log=log, requested_ref=session_ref, position=None,
        max_items=1, retired_ids=frozenset(db.live_session_aliases_to(row["session_id"])),
    )
    return current_represented_attention(snapshot, registration, session_ref)
