"""Session API endpoints -- /api/v1/sessions/*."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..models import (
    CursorAckRequest,
    CursorInfo,
    ResumeSessionRequest,
    SessionInfo,
    SessionListResponse,
    SessionStatus,
    StartSessionRequest,
    StartSessionResponse,
    SubmitPromptRequest,
    SubmitPromptResponse,
)
from ..session_manager import SessionConflictError
from ..transport import SpawnTarget

if TYPE_CHECKING:
    from ..session_manager import SessionManager

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

# Sentinel cursor key for callers that supply no caller_id. Keeps the
# delivery_cursors primary key non-null while still giving anonymous
# callers a single shared resume point per session.
_CURSOR_DEFAULT_KEY = "__default__"


def _cursor_key(caller_id: str | None) -> str:
    """Normalize a caller_id into a non-null delivery_cursors key."""
    return caller_id if caller_id else _CURSOR_DEFAULT_KEY


def _tool_progress_sse(active: dict, now: float) -> str:
    """Frame an in-flight tool call as a cursor-neutral SSE *comment*.

    ``active`` is :meth:`EventLog.active_tool_call`'s return value. The line is
    an SSE comment (``: tool_progress <json>``), not an ``event:``/``data:``
    block -- so it is invisible to spec-compliant ``EventSource`` consumers
    (which ignore ``:`` lines, like the existing ``: heartbeat``) and
    structurally cannot carry an ``id:``. It is pure transport liveness: it
    tells a watcher what the remote is working on (and that it is still alive)
    during a quiet, output-buffered tool call, without injecting a synthetic,
    non-relay event into the durable, replayable event stream or moving any
    delivery cursor. Only the agent-bridge CLI renderer opts in to parsing it;
    HTTP API consumers (e.g. Neuron Forge) ignore the comment for free.
    """
    progress = dict(active)
    started = progress.pop("started_at", None)
    if started is not None:
        progress["elapsed_s"] = max(0.0, now - started)
    # JSON is single-line (newlines escaped), so the comment stays one line.
    payload = json.dumps(progress)
    return f": tool_progress {payload}\n\n"


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

    try:
        session = await mgr.start_session(
            target, agent_name=req.agent, caller_id=req.caller_id,
        )
    except SessionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "session_conflict",
                "message": str(exc),
                "existing_session_id": exc.existing_session_id,
                "agent_name": exc.agent_name,
            },
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


@router.get("/{session_id}/status")
async def get_session_status(
    session_id: str, request: Request, caller_id: str | None = None
):
    """Compact, single-screen status for a dispatch.

    Returns session state, turn count, the caller's delivery-cursor position
    vs the head (so a watcher knows how far behind it is), and -- crucially --
    the *in-flight tool call with elapsed time*. That liveness is otherwise
    only emitted as a cursor-neutral SSE ``: tool_progress`` comment (invisible
    to ``read``), so a watcher could not previously tell a busy agent from a
    hung one without dumping the whole feed. This endpoint surfaces it cheaply
    (#46.1).
    """
    import time as _time
    from datetime import datetime, timezone

    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    active = session.event_log.active_tool_call() if session.event_log else None
    if active and active.get("started_at") is not None:
        active = {**active, "elapsed_s": max(0.0, _time.time() - active["started_at"])}

    head_id = mgr.db.get_max_event_id(session_id)
    last_acked = mgr.db.get_cursor(_cursor_key(caller_id), session_id)

    return {
        "session_id": session.session_id,
        "name": session.name,
        "agent_name": session.agent_name,
        "caller_id": session.caller_id,
        "status": session.status.value,
        "turn_count": session.turn_count,
        "context_pct": session.context_pct,
        "head_id": head_id,
        "last_acked_id": last_acked,
        "behind": max(0, head_id - last_acked),
        "active_tool": active,
        "updated_at": datetime.fromtimestamp(
            session.updated_at, tz=timezone.utc
        ).isoformat(),
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
async def get_events(
    session_id: str,
    request: Request,
    after: int | None = None,
    caller_id: str | None = None,
):
    """SSE event stream with durable event IDs.

    Resume semantics:

    - ``?after=<id>`` -- explicit start point (back-compat). Streams events
      with id > after.
    - omitted ``after`` + ``caller_id`` -- resume from the caller's last
      *acked* delivery cursor, so a reconnect picks up exactly where the
      host left off (nothing skipped on ungraceful death).
    - omitted ``after`` + no caller_id -- start from the beginning (0).

    The stream never advances the delivery cursor itself; the client acks
    delivered events via ``POST /{id}/cursor`` after flushing them, which
    is what makes delivery confirmation (not server-side production) drive
    the cursor.
    """
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not session.event_log:
        raise HTTPException(status_code=500, detail="No event log for session")

    if after is None:
        start = mgr.db.get_cursor(_cursor_key(caller_id), session_id)
    else:
        start = after

    async def event_stream():
        cursor = start
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
                # Quiet period. If a tool call is in flight, surface a
                # cursor-neutral liveness event carrying what the remote is
                # working on (title + command + elapsed) so a watcher can tell
                # a busy agent from a hung one. Otherwise, a bare heartbeat.
                active = session.event_log.active_tool_call()
                if active:
                    yield _tool_progress_sse(active, time.time())
                else:
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


@router.get("/{session_id}/events/range")
async def get_events_range(
    session_id: str, request: Request, start: int = 0, end: int | None = None
):
    """Random-access historical read of events by id range (inclusive).

    Returns events with ``start <= id <= end``. Does NOT touch any
    delivery cursor -- this is the only way to re-read already-consumed
    content without disturbing the live resume point.
    """
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    rows = mgr.db.get_events_range(session_id, start, end)
    return {
        "session_id": session_id,
        "events": [
            {
                "id": r["event_id"],
                "event": r["event_type"],
                "data": r["data"],
                "timestamp": r["timestamp"],
            }
            for r in rows
        ],
    }


@router.get("/{session_id}/cursor", response_model=CursorInfo)
async def get_cursor(session_id: str, request: Request, caller_id: str | None = None):
    """Return a caller's current delivery-cursor position for a session."""
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    last = mgr.db.get_cursor(_cursor_key(caller_id), session_id)
    return CursorInfo(
        session_id=session_id, caller_id=caller_id, last_acked_id=last,
        head_id=mgr.db.get_max_event_id(session_id),
    )


@router.post("/{session_id}/cursor", response_model=CursorInfo)
async def ack_cursor(
    session_id: str, req: CursorAckRequest, request: Request
):
    """Acknowledge delivery up to ``last_id`` for a caller (monotonic).

    The stored cursor never regresses, so duplicate/out-of-order acks are
    safe. The effective cursor after the ack is returned.
    """
    mgr: SessionManager = request.app.state.session_manager
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    effective = mgr.db.set_cursor(
        _cursor_key(req.caller_id), session_id, req.last_id, time.time()
    )
    return CursorInfo(
        session_id=session_id, caller_id=req.caller_id, last_acked_id=effective
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
