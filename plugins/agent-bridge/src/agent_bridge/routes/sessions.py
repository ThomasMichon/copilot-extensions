"""Session API endpoints -- /api/v1/sessions/*."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..models import (
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
    from datetime import datetime, UTC

    return SessionInfo(
        session_id=s.session_id,
        name=s.name,
        agent_name=s.agent_name,
        target_dir=s.target.cwd,
        target_type=s.target.type,
        status=s.status,
        pid=s.pid,
        turn_count=s.turn_count,
        created_at=datetime.fromtimestamp(s.created_at, tz=UTC),
        updated_at=datetime.fromtimestamp(s.updated_at, tz=UTC),
    )


@router.post("", response_model=StartSessionResponse, status_code=201)
async def start_session(req: StartSessionRequest, request: Request):
    mgr: SessionManager = request.app.state.session_manager

    target = SpawnTarget(
        type="local",
        cwd=req.target_dir or ".",
    )

    session = await mgr.start_session(target, agent_name=req.agent)

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


@router.delete("/{session_id}", status_code=204)
async def end_session(session_id: str, request: Request):
    mgr: SessionManager = request.app.state.session_manager
    try:
        await mgr.end_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
