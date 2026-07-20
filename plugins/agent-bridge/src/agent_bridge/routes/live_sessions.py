"""Live interactive-session registry endpoints -- /api/v1/live-sessions/*.

A live *interactive* Copilot CLI session is not owned by the bridge: the
bundled agent-bridge extension registers the session here so the bridge can
represent and (later) message it. Distinct from ``/api/v1/sessions`` (which
holds bridge-spawned ACP sessions). Liveness is heartbeat-based -- the
extension re-POSTs periodically to refresh ``updated_at``; an ungraceful exit
is reaped by staleness rather than relying on a clean deregister.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..models import (
    AckMessagesRequest,
    AckMessagesResult,
    IngestLiveEventsRequest,
    IngestLiveEventsResult,
    LiveMessage,
    LiveMessageListResponse,
    LiveProgressRequest,
    LiveSessionInfo,
    LiveSessionListResponse,
    RegisterLiveSessionRequest,
    SendMessageRequest,
    SendMessageResult,
)
from ..live_representation import (
    await_turn_reply,
    build_progress_snapshot,
    derive_turn_state,
)
from .sessions import _sse_event_stream

#: A running turn with no activity for longer than this reads as "stalled".
_TURN_STALL_SECONDS = 90.0

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


def _live_liveness(row: dict[str, Any], *, now: float | None = None) -> str | None:
    """Compute a friendly liveness label from turn-state + activity recency.

    ``active`` (running, recent), ``stalled`` (running but silent past the
    threshold -- the mid-turn-stall signal), ``idle`` (last turn ended), or None
    when the session has pushed no turn signal yet.
    """
    turn_state = row.get("turn_state")
    if not turn_state:
        return None
    if turn_state == "idle":
        return "idle"
    if turn_state == "running":
        last = row.get("last_activity_at")
        ts = time.time() if now is None else now
        if isinstance(last, (int, float)) and ts - last > _TURN_STALL_SECONDS:
            return "stalled"
        return "active"
    return turn_state


def _parse_progress(raw: Any) -> dict[str, Any] | None:
    """Parse the stored ``latest_progress`` JSON string into an object, or None."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


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
        driven_by=row.get("driven_by"),
        status=row.get("status") or "live",
        turn_state=row.get("turn_state"),
        last_activity_at=row.get("last_activity_at"),
        liveness=_live_liveness(row),
        latest_progress=_parse_progress(row.get("latest_progress")),
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
    status = db.register_live_session(
        body.session_id,
        machine=body.machine,
        cwd=body.cwd,
        worktree_id=body.worktree_id,
        repo=body.repo,
        branch=body.branch,
        pid=body.pid,
        role=body.role,
        driven_by=body.driven_by,
        now=now,
    )
    if status != "live":
        # Registration refused by an ownership primitive (#2912): either an
        # active owned-ACP reservation holds the worktree (``reserved``) or this
        # session id was taken over (``taken-over``). Surfaced as 409 so the
        # extension knows it must not act as this worktree's live controller.
        raise HTTPException(
            status_code=409,
            detail={
                "reason": status,
                "session_id": body.session_id,
                "worktree_id": body.worktree_id,
            },
        )
    row = db.get_live_session(body.session_id)
    if row is None:  # pragma: no cover -- write-then-read on the same connection
        raise HTTPException(status_code=500, detail="registration not persisted")
    return _to_info(row)


@router.get("", response_model=LiveSessionListResponse)
async def list_live_sessions(
    request: Request,
    worktree_id: str | None = None,
    include_dead: bool = False,
) -> LiveSessionListResponse:
    """List registered live interactive CLI sessions (optionally by worktree).

    Hides terminal ``expired`` / ``taken-over`` rows by default (they self-clean
    via the reaper's purge, #3144); pass ``?include_dead=true`` to see them.
    ``wedged`` sessions (process alive, heartbeat stalled, #3145) are shown.
    """
    db = _db(request)
    rows = db.list_live_sessions(worktree_id=worktree_id, include_dead=include_dead)
    return LiveSessionListResponse(live_sessions=[_to_info(r) for r in rows])


@router.get("/resolve", response_model=LiveSessionInfo)
async def resolve_live_session(handle: str, request: Request) -> LiveSessionInfo:
    """Resolve a handle (session id OR **worktree handle**) -> its live session.

    This is D3's addressing endpoint: an agent is a series of sessions in one
    worktree, so a peer addresses it by worktree handle and the bridge resolves
    that to whichever session is live *now* -- letting ``reply-to`` survive a
    handoff. An exact ``session_id`` still resolves to itself. 404 when the
    handle names neither a known session nor a currently-live worktree.

    Declared before ``/{session_id}`` so the literal ``/resolve`` path wins over
    the path-param route.
    """
    db = _db(request)
    row = db.resolve_live_session(handle, now=time.time())
    if row is None:
        raise HTTPException(
            status_code=404, detail="no live session for handle"
        )
    return _to_info(row)


@router.get("/{session_id}", response_model=LiveSessionInfo)
async def get_live_session(session_id: str, request: Request) -> LiveSessionInfo:
    """Fetch a single registered live interactive CLI session."""
    db = _db(request)
    row = db.get_live_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="live session not found")
    return _to_info(row)


@router.post("/{session_id}/progress", response_model=LiveSessionInfo)
async def record_live_progress(
    session_id: str, body: LiveProgressRequest, request: Request
) -> LiveSessionInfo:
    """Record an operator-driven session's progress beat (Phase 7 Slice 7c).

    The live-session analogue of the dispatched-task progress beat: a bounded,
    latest-only status line the agent emits (via a tool call) when the extension
    nudges it. ``session_id`` may be an exact id or a **worktree handle**, so the
    agent can address itself the same way peers do. 404 if it resolves to no live
    session. Every field is hard-capped so the beat stays a status line.
    """
    db = _db(request)
    now = time.time()
    row = db.resolve_live_session(session_id, now=now)
    if row is None:
        row = db.get_live_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="live session not found")
    snapshot = build_progress_snapshot(
        body.summary, phase=body.phase, blocker=body.blocker, pr=body.pr, ts=now
    )
    db.update_live_progress(
        row["session_id"],
        latest_progress=json.dumps(snapshot, separators=(",", ":")),
        now=now,
    )
    updated = db.get_live_session(row["session_id"])
    return _to_info(updated if updated is not None else row)


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
    # Phase 7 Channel A: fold the raw batch into a coarse turn_state so the
    # tracker sees running/idle/stalled -- objective and token-free.
    prior = (db.get_live_session(session_id) or {}).get("turn_state")
    new_state, saw_activity = derive_turn_state(raw, prior_state=prior)
    if new_state != prior or saw_activity:
        db.update_live_turn_state(
            session_id, turn_state=new_state, last_activity_at=time.time()
        )
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


@router.post("/{session_id}/messages", response_model=SendMessageResult)
async def post_live_message(
    session_id: str, body: SendMessageRequest, request: Request
) -> SendMessageResult:
    """Post a message INTO a live interactive session (Phase 2 write path).

    Enqueues an attributed envelope the target session's extension polls and
    injects via ``session.send`` (as an attributed user turn). 404 if the id is
    not a registered live session -- the vision's "clear refusal when the target
    is not serviceable". Delivery is durable: the message waits in the queue
    until the extension drains it.

    When ``wait`` is set (D1), the bridge also watches the target's *represented*
    event stream and returns the reply turn's assistant text once the next
    ``turn_complete`` lands (or ``replied=False`` on timeout). The represented
    head is captured **before** enqueue so the reply window starts at the moment
    of sending.
    """
    db = _db(request)
    now = time.time()

    # Freshness lease (#2906): validate the target registration's heartbeat
    # lease and enqueue *atomically* -- the check + insert run under one write
    # lock, so a concurrent reaper / take-over invalidation / session roll can't
    # strand a message on a just-expired registration. Rejects a stale, expired,
    # or superseded target (409) rather than durably queuing a write that a
    # later, unrelated incarnation could receive. The race-free enforcement NF's
    # client-side pre-send freshness guard (#2905) can only approximate.
    after = 0
    if body.wait:
        # Capture the represented head WITHOUT creating a store entry -- a
        # rejected send must not leak a permanent LiveEventStore log for a
        # stale/absent id. get_or_create is deferred until after the enqueue
        # succeeds below.
        existing_log = _store(request).get(session_id)
        after = existing_log.latest_id if existing_log is not None else 0

    message_id, reason = db.enqueue_live_message_if_fresh(
        session_id,
        sender=body.sender,
        body=body.body,
        now=now,
        reply_to=body.reply_to,
        kind=body.kind,
        expected_session_id=body.expected_session_id,
    )
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="live session not found")
    if reason == "stale":
        raise HTTPException(
            status_code=409,
            detail=(
                f"live session {session_id} is no longer fresh (it ended, was "
                "reaped, or was taken over); refusing delivery"
            ),
        )
    if reason is not None and reason.startswith("superseded:"):
        current = reason.split(":", 1)[1]
        raise HTTPException(
            status_code=409,
            detail=(
                f"live session {session_id} was superseded by {current}; "
                "refusing delivery"
            ),
        )
    if reason is not None and reason.startswith("expected_mismatch:"):
        current = reason.split(":", 1)[1]
        raise HTTPException(
            status_code=409,
            detail=(
                f"expected live session {body.expected_session_id} is not the "
                f"current live registration (current is {current or 'none'}); "
                "refusing delivery"
            ),
        )
    if message_id is None:  # defensive: unreachable when reason is None
        raise HTTPException(status_code=500, detail="enqueue produced no id")

    if not body.wait:
        return SendMessageResult(session_id=session_id, message_id=message_id)

    store = _store(request)
    log = store.get_or_create(session_id)
    reply = await await_turn_reply(log, after=after, timeout=body.wait_timeout)
    return SendMessageResult(
        session_id=session_id,
        message_id=message_id,
        replied=bool(reply["replied"]),
        reply=reply["reply"],
        stop_reason=reply["stop_reason"],
    )


@router.get("/{session_id}/messages", response_model=LiveMessageListResponse)
async def list_live_messages(
    session_id: str, request: Request
) -> LiveMessageListResponse:
    """Pending (undelivered) messages for a live session, oldest-first.

    This is the extension's inbox **poll**: it drains these, injects each via
    ``session.send``, then acks. 404 if the session is not registered.
    """
    db = _db(request)
    if db.get_live_session(session_id) is None:
        raise HTTPException(status_code=404, detail="live session not found")
    rows = db.list_pending_live_messages(session_id)
    return LiveMessageListResponse(
        messages=[
            LiveMessage(
                id=r["id"],
                sender=r["sender"],
                body=r["body"],
                reply_to=r.get("reply_to"),
                kind=r.get("kind") or "prompt",
                created_at=r["created_at"],
            )
            for r in rows
        ]
    )


@router.post("/{session_id}/messages/ack", response_model=AckMessagesResult)
async def ack_live_messages(
    session_id: str, body: AckMessagesRequest, request: Request
) -> AckMessagesResult:
    """Mark delivered messages acked (the extension acks after ``session.send``).

    Idempotent and scoped to ``session_id``: re-acking an already-delivered id
    is a no-op, so a redelivered ack never errors or double-counts.
    """
    db = _db(request)
    if db.get_live_session(session_id) is None:
        raise HTTPException(status_code=404, detail="live session not found")
    acked = db.ack_live_messages(session_id, body.ids, now=time.time())
    return AckMessagesResult(acked=acked)
