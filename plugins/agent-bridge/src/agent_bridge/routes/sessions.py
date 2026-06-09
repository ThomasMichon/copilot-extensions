"""Session API endpoints -- /api/v1/sessions/*."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..models import (
    ResumeSessionRequest,
    SessionInfo,
    SessionListResponse,
    SessionStatus,
    StartSessionRequest,
    StartSessionResponse,
    SubmitPromptRequest,
    SubmitPromptResponse,
)
from ..transport import SpawnTarget

if TYPE_CHECKING:
    from ..session_manager import SessionManager

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _session_info(s) -> SessionInfo:  # noqa: ANN001
    """Convert an internal Session to the public SessionInfo model."""
    from datetime import datetime, timezone

    return SessionInfo(
        session_id=s.session_id,
        name=s.name,
        agent_name=s.agent_name,
        caller_id=s.caller_id,
        acp_session_id=s.acp_session_id,
        target_dir=s.target.cwd,
        target_type=s.target.type,
        target_host=s.target.host,
        worktree_id=s.target.worktree_id,
        status=s.status,
        pid=s.pid,
        turn_count=s.turn_count,
        context_size=s.context_size,
        context_used=s.context_used,
        context_pct=s.context_pct,
        usage_model=s.usage_model,
        last_usage_at=(
            datetime.fromtimestamp(s.last_usage_at, tz=timezone.utc).isoformat()
            if s.last_usage_at else None
        ),
        created_at=datetime.fromtimestamp(s.created_at, tz=timezone.utc),
        updated_at=datetime.fromtimestamp(s.updated_at, tz=timezone.utc),
    )


# Session states considered "alive" and therefore reusable for caller affinity.
# Terminal/stopped states are excluded -- reusing them would hand back a session
# with no running process, so the caller should get a fresh spawn instead.
_REUSABLE_STATES = frozenset({
    SessionStatus.CREATED,
    SessionStatus.STARTING,
    SessionStatus.RUNNING,
    SessionStatus.IDLE,
})


def _find_reusable_session(mgr, agent_name, caller_id):
    """Return an alive session matching (agent_name, caller_id), or None.

    Picks the most recently updated match so a reload reattaches to the
    freshest session for that caller.
    """
    for session in mgr.list_sessions():  # already sorted newest-first
        if (
            session.caller_id == caller_id
            and session.agent_name == agent_name
            and session.status in _REUSABLE_STATES
        ):
            return session
    return None


@router.post("", response_model=StartSessionResponse, status_code=201)
async def start_session(req: StartSessionRequest, request: Request):
    mgr: SessionManager = request.app.state.session_manager

    # Caller-affinity reuse: if the caller supplies a caller_id (e.g. a
    # Neuron-Forge worktree GUID) and an alive session already exists for
    # that (agent, caller_id) pair, return it instead of spawning a new one.
    # This makes create idempotent for HTTP consumers -- a duplicate POST
    # from a reload or double-click resolves to the same session/worktree
    # rather than creating a second one.  Pass force_new to opt out.
    if req.caller_id and not req.force_new:
        existing = _find_reusable_session(mgr, req.agent, req.caller_id)
        if existing is not None:
            return StartSessionResponse(
                session_id=existing.session_id,
                name=existing.name,
                status=existing.status,
            )

    if req.agent:
        # Resolve agent via registry
        resolver = getattr(request.app.state, "resolver", None)
        if not resolver:
            raise HTTPException(
                status_code=500,
                detail="No agent resolver configured -- topology not loaded",
            )
        try:
            target = await resolver.resolve_async(req.agent)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Session roll: reuse existing worktree instead of creating a new one
        if req.worktree_id:
            target.worktree_id = req.worktree_id
        if req.target_dir:
            target.cwd = req.target_dir
    else:
        target = SpawnTarget(
            type="local",
            cwd=req.target_dir or ".",
        )

    session = await mgr.start_session(
        target, agent_name=req.agent, caller_id=req.caller_id,
    )

    return StartSessionResponse(
        session_id=session.session_id,
        name=session.name,
        status=session.status,
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(request: Request, status: str | None = None):
    mgr: SessionManager = request.app.state.session_manager
    sessions = mgr.list_sessions(status=status)
    return SessionListResponse(
        sessions=[_session_info(s) for s in sessions]
    )


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str, request: Request):
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return _session_info(session)


@router.get("/{session_id}/usage")
async def get_session_usage(session_id: str, request: Request):
    """Return the full context window usage snapshot for a session."""
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    from datetime import datetime, timezone

    return {
        "session_id": session.session_id,
        "context_size": session.context_size,
        "context_used": session.context_used,
        "context_pct": session.context_pct,
        "usage_model": session.usage_model,
        "last_usage_at": (
            datetime.fromtimestamp(session.last_usage_at, tz=timezone.utc).isoformat()
            if session.last_usage_at else None
        ),
        "turn_count": session.turn_count,
        "status": session.status.value,
    }


@router.post("/{session_id}/turns", response_model=SubmitPromptResponse)
async def submit_prompt(
    session_id: str, req: SubmitPromptRequest, request: Request
):
    mgr: SessionManager = request.app.state.session_manager
    try:
        turn_index = await mgr.submit_prompt(session_id, req.prompt)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    session = mgr.get_session(session_id)
    return SubmitPromptResponse(
        turn_index=turn_index,
        status=session.status if session else SessionStatus.IDLE,
    )


@router.get("/{session_id}/events")
async def get_events(session_id: str, request: Request, after: int = 0):
    """SSE event stream with durable event IDs.

    Clients reconnect with ?after=<last_seen_id> to resume without
    missing events.
    """
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not session.event_log:
        raise HTTPException(status_code=500, detail="No event log for session")

    async def event_stream():
        cursor = after
        while True:
            events = await session.event_log.wait_for_events(cursor, timeout=30.0)
            if events:
                for evt in events:
                    data = json.dumps({
                        "event": evt.event,
                        "data": evt.data,
                        "timestamp": evt.timestamp,
                    })
                    yield f"id: {evt.id}\nevent: {evt.event}\ndata: {data}\n\n"
                    cursor = evt.id
            else:
                # Heartbeat to keep connection alive
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{session_id}/stop", status_code=204)
async def stop_session(session_id: str, request: Request):
    mgr: SessionManager = request.app.state.session_manager
    try:
        await mgr.stop_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@router.post("/{session_id}/resume", response_model=SessionInfo)
async def resume_session(session_id: str, request: Request):
    """Resume a stopped session by spawning a new agent process."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        session = await mgr.resume_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return _session_info(session)


@router.delete("/{session_id}", status_code=204)
async def end_session(session_id: str, request: Request):
    mgr: SessionManager = request.app.state.session_manager
    try:
        await mgr.end_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
