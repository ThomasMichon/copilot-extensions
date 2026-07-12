"""Live interactive-session registry endpoints -- /api/v1/live-sessions/*.

A live *interactive* Copilot CLI session is not owned by the bridge: the
bundled agent-bridge extension registers the session here so the bridge can
represent and (later) message it. Distinct from ``/api/v1/sessions`` (which
holds bridge-spawned ACP sessions). Liveness is heartbeat-based -- the
extension re-POSTs periodically to refresh ``updated_at``; an ungraceful exit
is reaped by staleness rather than relying on a clean deregister.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..models import (
    IngestLiveEventsRequest,
    IngestLiveEventsResult,
    LiveSessionInfo,
    LiveSessionListResponse,
    RegisterLiveSessionRequest,
)
from .sessions import _sse_event_stream

if TYPE_CHECKING:
    from ..db import Database
    from ..events import EventLog
    from ..live_representation import LiveEventStore

router = APIRouter(prefix="/api/v1/live-sessions", tags=["live-sessions"])


@dataclass
class _RepresentedSession:
    """Minimal object satisfying ``_sse_event_stream``'s duck-typed access.

    The SSE helper only reads ``.session_id`` and ``.event_log`` (subscriber
    tracking is skipped when ``mgr=None``), so a represented live session needs
    no bridge ``Session`` -- keeping it off the ACP-owned ``SessionManager``.
    """

    session_id: str
    event_log: EventLog


def _db(request: Request) -> Database:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="database not ready")
    return db


def _store(request: Request) -> LiveEventStore:
    store = getattr(request.app.state, "live_event_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="live event store not ready")
    return store


def _to_info(row: dict[str, Any]) -> LiveSessionInfo:
    return LiveSessionInfo(
        session_id=row["session_id"],
        machine=row.get("machine"),
        cwd=row.get("cwd"),
        worktree_id=row.get("worktree_id"),
        repo=row.get("repo"),
        branch=row.get("branch"),
        pid=row.get("pid"),
        role=row.get("role"),
        status=row.get("status") or "live",
        registered_at=row["registered_at"],
        updated_at=row["updated_at"],
    )


@router.post("", response_model=LiveSessionInfo)
async def register_live_session(
    body: RegisterLiveSessionRequest, request: Request
) -> LiveSessionInfo:
    """Register (or heartbeat-refresh) a live interactive CLI session.

    Idempotent: a re-POST for the same ``session_id`` upserts the row and
    refreshes ``updated_at``, which is how the extension heartbeats liveness.
    """
    db = _db(request)
    now = time.time()
    db.register_live_session(
        body.session_id,
        machine=body.machine,
        cwd=body.cwd,
        worktree_id=body.worktree_id,
        repo=body.repo,
        branch=body.branch,
        pid=body.pid,
        role=body.role,
        now=now,
    )
    row = db.get_live_session(body.session_id)
    if row is None:  # pragma: no cover -- write-then-read on the same connection
        raise HTTPException(status_code=500, detail="registration not persisted")
    return _to_info(row)


@router.get("", response_model=LiveSessionListResponse)
async def list_live_sessions(
    request: Request, worktree_id: str | None = None
) -> LiveSessionListResponse:
    """List registered live interactive CLI sessions (optionally by worktree)."""
    db = _db(request)
    rows = db.list_live_sessions(worktree_id=worktree_id)
    return LiveSessionListResponse(live_sessions=[_to_info(r) for r in rows])


@router.get("/{session_id}", response_model=LiveSessionInfo)
async def get_live_session(session_id: str, request: Request) -> LiveSessionInfo:
    """Fetch a single registered live interactive CLI session."""
    db = _db(request)
    row = db.get_live_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="live session not found")
    return _to_info(row)


@router.delete("/{session_id}")
async def deregister_live_session(
    session_id: str, request: Request
) -> dict[str, Any]:
    """Deregister a live interactive CLI session (best-effort on session exit).

    Deleting an unknown session_id is a no-op (idempotent), so a duplicate or
    late deregister never errors. Also drops any represented event log so the
    live tail's memory is reclaimed when the session goes away.
    """
    db = _db(request)
    db.deregister_live_session(session_id)
    store = getattr(request.app.state, "live_event_store", None)
    if store is not None:
        store.drop(session_id)
    return {"ok": True, "session_id": session_id}


@router.post("/{session_id}/events", response_model=IngestLiveEventsResult)
async def ingest_live_events(
    session_id: str, body: IngestLiveEventsRequest, request: Request
) -> IngestLiveEventsResult:
    """Ingest a batch of raw SDK events from a represented session's extension.

    The bridge translates each SDK event into its existing event vocabulary
    (see ``live_representation.translate_sdk_event``) and appends the result to
    the session's in-memory represented event log, which the SSE read endpoint
    below streams to viewers (e.g. Neuron Forge). 404 if ``session_id`` is not a
    registered live session -- representation follows registration.
    """
    db = _db(request)
    if db.get_live_session(session_id) is None:
        raise HTTPException(status_code=404, detail="live session not found")
    store = _store(request)
    raw = [e.model_dump() for e in body.events]
    ingested = store.ingest(session_id, raw)
    log = store.get(session_id)
    last_id = log.latest_id if log is not None else 0
    return IngestLiveEventsResult(
        session_id=session_id, ingested=ingested, last_id=last_id
    )


@router.get("/{session_id}/events")
async def stream_live_events(
    session_id: str, request: Request, after: int | None = None
) -> StreamingResponse:
    """SSE stream of a represented live session's translated events.

    Reuses the exact same ``_sse_event_stream`` helper the ACP sessions use, so
    NF's existing ``EventSource`` consumer reads a represented session
    identically to a bridge-owned one -- read-only: there is no turn/stop/cursor
    surface here, and permission events arrive unanswerable. Starts from
    ``?after=<id>`` (default 0 = the whole in-memory tail).
    """
    db = _db(request)
    if db.get_live_session(session_id) is None:
        raise HTTPException(status_code=404, detail="live session not found")
    store = _store(request)
    log = store.get_or_create(session_id)
    shim = _RepresentedSession(session_id=session_id, event_log=log)
    server = getattr(request.app.state, "uvicorn_server", None)
    return StreamingResponse(
        _sse_event_stream(
            shim,
            after or 0,
            server=server,
            is_disconnected=getattr(request, "is_disconnected", None),
            mgr=None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
