"""Session API endpoints -- /api/v1/sessions/*."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from .. import elevated
from ..attention_wait import (
    AttentionHistoryChangedError,
    AttentionTokenError,
    attention_position_session_id,
    evaluate_owned_attention,
)
from ..models import (
    AnswerAskUserRequest,
    AnswerPermissionRequest,
    AttentionReason,
    AttentionWaitResponse,
    CursorAckRequest,
    CursorInfo,
    DelegatedResultSnapshot,
    PendingPrompt,
    PendingQueueResponse,
    ResyncSessionResponse,
    SessionInfo,
    SessionListResponse,
    SessionStatus,
    StartSessionRequest,
    StartSessionResponse,
    SubmitPromptRequest,
    SubmitPromptResponse,
)
from ..result_snapshot import (
    DEFAULT_MAX_ITEMS,
    DEFAULT_MAX_TEXT_CHARS,
    MAX_MAX_ITEMS,
    MAX_MAX_TEXT_CHARS,
    ResultHistoryChangedError,
    ResultTokenError,
    build_owned_result_snapshot,
    expand_owned_result_ref,
)
from ..session_manager import (
    DaemonDrainingError,
    ProviderTargetRefreshError,
    SessionBusyError,
    SessionConflictError,
)
from ..transport import SpawnTarget
from ..worktree_head import resolve_head

if TYPE_CHECKING:
    from ..session_manager import Session, SessionManager

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

# Sentinel cursor key for callers that supply no caller_id. Keeps the
# delivery_cursors primary key non-null while still giving anonymous
# callers a single shared resume point per session.
_CURSOR_DEFAULT_KEY = "__default__"


def _cursor_key(caller_id: str | None) -> str:
    """Normalize a caller_id into a non-null delivery_cursors key."""
    return caller_id if caller_id else _CURSOR_DEFAULT_KEY


def _resolve_result_session(mgr: SessionManager, ref: str) -> Session | None:
    """Resolve an owned session or authoritative worktree handle."""
    session = mgr.get_session(ref)
    if session is not None:
        return session
    ownership = mgr.db.get_worktree_ownership(ref)
    candidates = [
        item for item in mgr.list_sessions()
        if item.target.worktree_id == ref
    ]
    if ownership:
        owned = mgr.get_session(ownership.get("session_id") or "")
        if owned is not None and owned.status in {
            SessionStatus.RUNNING,
            SessionStatus.IDLE,
        }:
            return owned
    live_session_id = mgr.db.current_represented_session_for_worktree(
        ref, now=time.time()
    )
    if live_session_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "The authoritative worktree head is a represented session; "
                "use the represented result surface or the result CLI's "
                "automatic target selection"
            ),
        )
    if ownership is None and not candidates:
        return None

    candidate_rows = {
        item.session_id: mgr.db.get_session(item.session_id) or {}
        for item in candidates
    }
    lineage_heads = [
        item
        for item in candidates
        if not candidate_rows[item.session_id].get("successor_id")
    ]
    if len(lineage_heads) == 1:
        return lineage_heads[0]
    if len(candidates) == 1:
        return candidates[0]

    # Multiple unlinked owned candidates are genuinely ambiguous. Consult the
    # ground-layer authority only for that exceptional case; ordinary result
    # reads stay local and cannot inherit the subprocess timeout.
    head = resolve_head(ref)
    if head.tracked:
        if not head.head_session:
            raise HTTPException(
                status_code=409,
                detail="The worktree has no current authoritative session head",
            )
        session = mgr.get_session(head.head_session)
        if session is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The authoritative worktree head is a represented session; "
                    "use the represented result surface or the result CLI's "
                    "automatic target selection"
                ),
            )
        return session
    raise HTTPException(
        status_code=409,
        detail=(
            "The authoritative owned session head could not be resolved "
            "without guessing"
        ),
    )


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


def _control_sse(code: str, message: str, **details: Any) -> str:
    """Frame a cursor-neutral subscription control signal."""
    payload = {
        "code": code,
        "message": message,
        "action": "full_reconcile",
        **details,
    }
    return (
        "event: bridge_control\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    )


async def _sse_event_stream(  # noqa: ANN001
    session,
    start,
    *,
    server,
    is_disconnected,
    mgr=None,
    signal_gaps: bool = False,
    expected_continuity_id: str | None = None,
    heartbeat_interval: float = 30.0,
):
    """The SSE event generator for ``GET /{id}/events`` (extracted for testing).

    Streams durable events past ``start``; on each quiet ``wait_for_events``
    return it emits a liveness beat (tool-progress or heartbeat). Crucially it
    **closes promptly on daemon shutdown or client disconnect**: it races the
    (up to 30s) event wait against a fine poll of uvicorn's ``server.should_exit``
    (set on SIGTERM *before* uvicorn waits on in-flight requests). Without this a
    long-lived stream pins the daemon's graceful shutdown open until systemd's
    TimeoutStopSec SIGKILL (#1789) -- which also starves the lifespan
    graceful-cancel on a bare ``systemctl restart``. The per-cycle beat cadence
    is unchanged.

    While a stream is live it counts as an active **subscriber** (#1826) via
    ``mgr.add_subscriber``/``remove_subscriber`` so the idle reaper never reaps
    a session someone is watching. The decrement is in a ``finally`` so it runs
    on shutdown, client disconnect, or generator close.
    """
    cursor = start
    continuity_id = (
        getattr(session.event_log, "continuity_id", None)
        if signal_gaps
        else None
    )
    if (
        signal_gaps
        and expected_continuity_id is not None
        and expected_continuity_id != continuity_id
    ):
        yield _control_sse(
            "cursor_invalidated",
            "the authoritative event log continuity changed",
            prior_continuity_id=expected_continuity_id,
            continuity_id=continuity_id,
        )
        return

    if mgr is not None:
        mgr.add_subscriber(session.session_id)

    def _shutting_down() -> bool:
        return bool(server is not None and getattr(server, "should_exit", False))

    async def _closing() -> bool:
        if _shutting_down():
            return True
        if is_disconnected is not None:
            with contextlib.suppress(Exception):
                if await is_disconnected():
                    return True
        return False

    try:
        while True:
            if await _closing():
                return
            wait_snapshot = getattr(
                session.event_log, "wait_for_events_snapshot", None
            )
            if callable(wait_snapshot):
                wait_task = asyncio.ensure_future(
                    wait_snapshot(cursor, timeout=heartbeat_interval)
                )
            else:
                wait_task = asyncio.ensure_future(
                    session.event_log.wait_for_events(
                        cursor, timeout=heartbeat_interval
                    )
                )
            while True:
                done, _pending = await asyncio.wait({wait_task}, timeout=0.5)
                if done:
                    break
                if await _closing():
                    wait_task.cancel()
                    with contextlib.suppress(BaseException):
                        await wait_task
                    return
            wait_result = wait_task.result()
            if (
                isinstance(wait_result, tuple)
                and len(wait_result) == 2
            ):
                current_continuity_id, events = wait_result
            else:
                events = wait_result
                current_continuity_id = (
                    getattr(session.event_log, "continuity_id", None)
                    if signal_gaps
                    else None
                )
            if signal_gaps and current_continuity_id != continuity_id:
                if continuity_id is None and cursor == 0 and events:
                    continuity_id = current_continuity_id
                else:
                    yield _control_sse(
                        "cursor_invalidated",
                        "the authoritative event log was rebuilt",
                        prior_continuity_id=continuity_id,
                        continuity_id=current_continuity_id,
                    )
                    return
            if events:
                if signal_gaps and events[0].id != cursor + 1:
                    yield _control_sse(
                        "replay_gap",
                        "the authoritative event stream is not contiguous",
                        after=cursor,
                        next_event_id=events[0].id,
                        continuity_id=continuity_id,
                    )
                    return
                for evt in events:
                    if signal_gaps:
                        current_continuity_id = getattr(
                            session.event_log, "continuity_id", None
                        )
                        if current_continuity_id != continuity_id:
                            yield _control_sse(
                                "cursor_invalidated",
                                "the authoritative event log was rebuilt",
                                prior_continuity_id=continuity_id,
                                continuity_id=current_continuity_id,
                            )
                            return
                        if evt.id != cursor + 1:
                            yield _control_sse(
                                "replay_gap",
                                "the authoritative event stream is not contiguous",
                                after=cursor,
                                next_event_id=evt.id,
                                continuity_id=continuity_id,
                            )
                            return
                    event_payload = {
                        "event": evt.event,
                        "data": evt.data,
                        "timestamp": evt.timestamp,
                    }
                    if signal_gaps:
                        event_payload["continuity_id"] = continuity_id
                    data = json.dumps(event_payload)
                    yield f"id: {evt.id}\nevent: {evt.event}\ndata: {data}\n\n"
                    cursor = evt.id
                continue
            # Quiet period -- cursor-neutral liveness beat.
            active = session.event_log.active_tool_call()
            if active:
                yield _tool_progress_sse(active, time.time())
            else:
                yield ": heartbeat\n\n"
    finally:
        if mgr is not None:
            mgr.remove_subscriber(session.session_id)


def _session_info(s) -> SessionInfo:  # noqa: ANN001
    """Convert an internal Session to the public SessionInfo model."""
    from datetime import datetime, timezone

    status, at_rest, liveness = s.public_state()
    return SessionInfo(
        session_id=s.session_id,
        name=s.name,
        agent_name=s.agent_name,
        caller_id=s.caller_id,
        acp_session_id=s.acp_session_id,
        target_dir=s.target.cwd,
        target_type=s.target.type,
        target_host=s.target.host,
        project=getattr(s.target, "project", None),
        worktree_id=s.target.worktree_id,
        elevated=s.target.elevated,
        read_only=False,
        status=status,
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
        last_output_at=(
            datetime.fromtimestamp(s.last_output_at, tz=timezone.utc).isoformat()
            if s.last_output_at else None
        ),
        last_heartbeat_at=(
            datetime.fromtimestamp(s.last_heartbeat_at, tz=timezone.utc).isoformat()
            if s.last_heartbeat_at else None
        ),
        liveness=liveness,
        at_rest=at_rest,
    )


def _persisted_session_info(
    row: dict[str, Any], *, daemon_running: bool
) -> SessionInfo:
    """Convert an elevated database row to its read-only public view."""
    from datetime import datetime, timezone

    target_json = row.get("target_json")
    target = (
        SpawnTarget.from_json(target_json)
        if target_json
        else SpawnTarget(
            type=row.get("target_type", "local"),
            cwd=row.get("target_dir", "."),
        )
    )
    session_status = SessionStatus(row["status"])
    if not daemon_running and session_status in {
        SessionStatus.CREATED,
        SessionStatus.STARTING,
        SessionStatus.RUNNING,
        SessionStatus.IDLE,
        SessionStatus.STOPPING,
    }:
        session_status = SessionStatus.STOPPED

    context_size = row.get("context_size")
    context_used = row.get("context_used")
    context_pct = (
        round(context_used / context_size * 100, 1)
        if context_size and context_used is not None
        else None
    )
    last_usage_at = row.get("last_usage_at")
    return SessionInfo(
        session_id=row["id"],
        name=row["name"],
        agent_name=row.get("agent_name"),
        caller_id=row.get("caller_id"),
        acp_session_id=row.get("acp_session_id"),
        target_dir=target.cwd,
        target_type=target.type,
        target_host=target.host,
        project=getattr(target, "project", None),
        worktree_id=target.worktree_id,
        elevated=True,
        read_only=True,
        status=session_status,
        pid=row.get("pid") if daemon_running else None,
        turn_count=row.get("turn_count", 0),
        context_size=context_size,
        context_used=context_used,
        context_pct=context_pct,
        usage_model=row.get("usage_model"),
        last_usage_at=(
            datetime.fromtimestamp(last_usage_at, tz=timezone.utc).isoformat()
            if last_usage_at else None
        ),
        created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
        updated_at=datetime.fromtimestamp(row["updated_at"], tz=timezone.utc),
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


def _enforce_worktree_head_guard(worktree_id: str) -> None:
    """Refuse a create into a worktree with an active head or pending handoff.

    Derives the head from agent-worktrees (see :mod:`..worktree_head`). When the
    worktree is occupied by a current session or handoff, raises an ``HTTPException``
    409 whose structured detail enumerates the three deliberate resolutions
    (reuse / handoff / sunset) plus the ``reclaim`` break-glass. Fails **open**:
    an untracked worktree or an unreadable ground layer yields ``occupied=False``
    and this returns without raising, so create proceeds exactly as before.
    """
    from ..worktree_head import resolve_head

    head = resolve_head(worktree_id)
    if not head.occupied:
        return
    pending = head.occupied and not head.active
    raise HTTPException(
        status_code=409,
        detail={
            "reason": (
                "worktree_head_pending" if pending
                else "worktree_head_active"
            ),
            "worktree_id": worktree_id,
            "head_session": head.head_session,
            "head_state": head.state,
            "message": (
                (
                    f"Worktree {worktree_id} has a pending handoff; starting "
                    "another session could race the intended successor. "
                    "Consume or explicitly supersede the handoff, or pass "
                    "reclaim=true to take over."
                )
                if pending else
                (
                    f"Worktree {worktree_id} already has a current session "
                    f"({head.head_session}); starting a new one would run in "
                    "parallel with it. Resolve the incumbent first (reuse / "
                    "handoff / sunset), or pass reclaim=true to take over."
                )
            ),
            "choices": [
                {
                    "action": "reuse",
                    "preferred": True,
                    "description": (
                        "Continue the existing session in-context (resume it) "
                        "rather than creating a new one -- the worktree is "
                        "already yours to take responsibility for."
                    ),
                },
                {
                    "action": "handoff",
                    "description": (
                        "If context is high, have the current session produce a "
                        "handoff, conclude it, then create a fresh session in "
                        "this worktree seeded with that handoff."
                    ),
                },
                {
                    "action": "sunset",
                    "description": (
                        "If the current session is finished/irrelevant, drive it "
                        "to conclusion (finalize); once concluded, a fresh "
                        "create is permitted."
                    ),
                },
            ],
            "override": "reclaim=true",
        },
    )


@router.post("", response_model=StartSessionResponse, status_code=201)
async def start_session(req: StartSessionRequest, request: Request):
    mgr: SessionManager = request.app.state.session_manager
    resolver = getattr(request.app.state, "resolver", None)
    agent_name = req.agent
    if agent_name and not getattr(request.app.state, "ready", True):
        raise HTTPException(
            status_code=503,
            detail="agent-bridge is initializing; retry shortly",
        )
    if agent_name and resolver:
        canonicalize = getattr(resolver, "canonical_agent_name", None)
        if callable(canonicalize):
            canonical = canonicalize(agent_name)
            if isinstance(canonical, str) and canonical:
                agent_name = canonical

    # Refuse new sessions fast while draining -- before any agent resolution or
    # spawn work -- so a zero-downtime redeploy stops growing the daemon it is
    # about to retire. (The manager enforces the same gate as a backstop.)
    if mgr.is_draining:
        raise HTTPException(
            status_code=503,
            detail="agent-bridge is draining for a redeploy and is not "
                   "accepting a new session; retry shortly.",
        )

    if req.parity_fault:
        from ..protocol import FAILED_ACP_HANDSHAKE_FAULT

        if req.parity_fault != FAILED_ACP_HANDSHAKE_FAULT:
            raise HTTPException(status_code=400, detail="unsupported parity fault")
        if (
            not req.force_new
            or not req.agent
            or not (req.caller_id or "").startswith("venue-parity:")
        ):
            raise HTTPException(
                status_code=403,
                detail="parity start faults require an explicit force-new "
                "venue-parity caller and a named remote target",
            )
        active = [
            session.session_id
            for session in mgr.list_sessions()
            if session.status not in {
                SessionStatus.FAILED,
                SessionStatus.ENDED,
                SessionStatus.STOPPED,
            }
        ]
        if active:
            raise HTTPException(
                status_code=409,
                detail="parity start fault refuses another active managed "
                f"session: {active[0]}",
            )

    # Caller-affinity reuse: if the caller supplies a caller_id (e.g. a
    # Neuron-Forge worktree GUID) and an alive session already exists for
    # that (agent, caller_id) pair, return it instead of spawning a new one.
    # This makes create idempotent for HTTP consumers -- a duplicate POST
    # from a reload or double-click resolves to the same session/worktree
    # rather than creating a second one.  Pass force_new to opt out.
    if req.caller_id and not req.force_new:
        existing = _find_reusable_session(mgr, agent_name, req.caller_id)
        if existing is not None:
            return StartSessionResponse(
                session_id=existing.session_id,
                name=existing.name,
                status=existing.status,
            )

    # Session-lifecycle head guard (agent-fabric
    # `single-current-session-per-worktree`). Creating a session *into an
    # existing worktree* (``worktree_id`` set -- e.g. a Neuron-Forge session
    # roll) whose ground-layer head is still ``active`` would silently spawn a
    # second, parallel session in a worktree that already has a current one.
    # Refuse it: the caller must reuse (preferred), hand off, or sunset the
    # incumbent -- ``reclaim=true`` is the deliberate break-glass take-over. The
    # head is *derived* from agent-worktrees (the ground-layer owner); agent-
    # bridge keeps no rival pointer (``derive-dont-duplicate``). Fails open: if
    # the ground layer can't be read, ``active`` is False and create proceeds.
    #
    # This is the create-time sibling of the ``resume_worktree`` liveness guard
    # (409 ``live_cli_holds_worktree``, also reclaim-bypassed): that one refuses
    # owning a worktree a live *process* holds; this one refuses spawning atop a
    # worktree an *asserted* head owns. Together they are one story -- a worktree
    # has one current session, and taking it over is an explicit act.
    if req.worktree_id and not req.reclaim:
        _enforce_worktree_head_guard(req.worktree_id)

    if agent_name:
        # Resolve agent via registry
        if not resolver:
            raise HTTPException(
                status_code=500,
                detail="No agent resolver configured -- topology not loaded",
            )
        try:
            target = await resolver.resolve_async(
                agent_name, sender_repo=req.sender_repo,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            # Ambiguous bare name (collision across namespaces): balk with the
            # enumerated candidates so the caller can disambiguate (#50).
            from ..agent_registry import AmbiguousAgentError

            if isinstance(exc, AmbiguousAgentError):
                raise HTTPException(status_code=409, detail=str(exc))
            raise
        # Session roll: reuse existing worktree instead of creating a new one
        if req.worktree_id:
            target.worktree_id = req.worktree_id
        if req.target_dir:
            target.cwd = req.target_dir
            target.explicit_cwd = True
    else:
        target = SpawnTarget(
            type="local",
            cwd=req.target_dir or ".",
        )

    if req.parity_fault:
        is_remote = (
            isinstance(target.container, dict)
            and bool(target.container.get("name"))
        ) or (
            isinstance(target.codespace, dict)
            and bool(target.codespace.get("name"))
        )
        if not is_remote:
            raise HTTPException(
                status_code=400,
                detail="failed ACP handshake parity requires a structured "
                "container or CodeSpace target",
            )

    # Per-session env overrides (e.g. BYOK provider selection) merge onto the
    # resolved agent's declared env, per-session winning. Applied to the spawned
    # Copilot process by the transport (``env.update(target.env)``).
    if req.env:
        target.env = {**target.env, **req.env}

    try:
        session = await mgr.start_session(
            target, agent_name=agent_name, caller_id=req.caller_id,
            mcp_servers=req.mcp_servers,
            copilot_args=req.copilot_args,
            env_overrides=req.env,
            caller_owner_ref=req.caller_owner_ref,
            model=req.model, effort=req.effort,
            parity_fault=req.parity_fault,
        )
    except DaemonDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
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

    parity_fault_result = None
    if req.parity_fault:
        parity_fault_result = await mgr.finalize_parity_fault_start(
            session,
            req.parity_fault,
        )

    return StartSessionResponse(
        session_id=session.session_id,
        name=session.name,
        status=session.status,
        parity_fault_result=parity_fault_result,
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(request: Request, status: str | None = None):
    mgr: SessionManager = request.app.state.session_manager
    status = status or None
    primary_sessions = mgr.list_sessions()
    infos = [_session_info(session) for session in primary_sessions]
    if status is not None:
        infos = [info for info in infos if info.status.value == status]

    # Elevated sessions live in a separate daemon/database. The primary daemon
    # remains their discovery surface after that daemon idle-exits; rows already
    # represented by a primary relay session are omitted to avoid showing the
    # same conversation twice.
    if not elevated.is_subdaemon():
        rows = await asyncio.to_thread(elevated.persisted_session_rows)
        if rows:
            daemon_running = await asyncio.to_thread(
                elevated.is_up, timeout=0.2
            )
            represented = {
                info.acp_session_id for info in infos if info.acp_session_id
            }
            represented.update(info.session_id for info in infos)
            for row in rows:
                if row["id"] in represented:
                    continue
                info = _persisted_session_info(
                    row, daemon_running=daemon_running
                )
                if status is None or info.status.value == status:
                    infos.append(info)

    infos.sort(key=lambda info: info.updated_at, reverse=True)
    return SessionListResponse(sessions=infos)


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

    status, at_rest, _liveness = session.public_state()
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
        "status": status.value,
        "at_rest": at_rest,
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

    status, at_rest, _liveness = session.public_state()
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
        "status": status.value,
        "at_rest": at_rest,
        "turn_count": session.turn_count,
        "context_pct": session.context_pct,
        "usage_model": session.usage_model,
        "head_id": head_id,
        "last_acked_id": last_acked,
        "behind": max(0, head_id - last_acked),
        "active_tool": active,
        "active_background_tasks": session.active_background_tasks,
        "pending_ask_user": (
            session.client.pending_ask_user() if session.client else []
        ),
        "progress": dict(session.progress),
        "updated_at": datetime.fromtimestamp(
            session.updated_at, tz=timezone.utc
        ).isoformat(),
    }


@router.get(
    "/{session_ref}/result",
    response_model=DelegatedResultSnapshot,
)
def get_result_snapshot(
    session_ref: str,
    request: Request,
    position: str | None = Query(default=None, max_length=2048),
    max_items: int = Query(default=DEFAULT_MAX_ITEMS, ge=1, le=MAX_MAX_ITEMS),
    max_text_chars: int = Query(
        default=DEFAULT_MAX_TEXT_CHARS, ge=256, le=MAX_MAX_TEXT_CHARS
    ),
):
    """Return a bounded, cursor-neutral result snapshot for an owned session."""
    mgr: SessionManager = request.app.state.session_manager
    session = _resolve_result_session(mgr, session_ref)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session or worktree {session_ref} not found",
        )
    if session.event_log is None:
        raise HTTPException(
            status_code=409,
            detail="Session history is not loaded in the active bridge generation",
        )
    try:
        return build_owned_result_snapshot(
            db=mgr.db,
            session=session,
            requested_ref=session_ref,
            position=position,
            max_items=max_items,
            max_text_chars=max_text_chars,
        )
    except ResultTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{session_ref}/result/detail")
def get_result_detail(
    session_ref: str,
    request: Request,
    ref: str = Query(max_length=2048),
):
    """Resolve an opaque result detail reference without moving a cursor."""
    mgr: SessionManager = request.app.state.session_manager
    session = _resolve_result_session(mgr, session_ref)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session or worktree {session_ref} not found",
        )
    if session.event_log is None:
        raise HTTPException(
            status_code=409,
            detail="Session history is not loaded in the active bridge generation",
        )
    try:
        return expand_owned_result_ref(
            db=mgr.db,
            event_log=session.event_log,
            session_id=session.session_id,
            token=ref,
        )
    except ResultHistoryChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ResultTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        detail = str(exc.args[0]) if exc.args else "Result detail is unavailable"
        raise HTTPException(status_code=404, detail=detail) from exc


@router.get(
    "/{session_ref}/attention",
    response_model=AttentionWaitResponse,
)
async def wait_for_attention(
    session_ref: str,
    request: Request,
    reason: list[AttentionReason] = Query(min_length=1),
    position: str | None = Query(default=None, max_length=2048),
    timeout_seconds: float = Query(default=30.0, ge=0.0, le=30.0),
):
    """Wait for the earliest selected durable attention boundary."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        if position is None:
            session = _resolve_result_session(mgr, session_ref)
        else:
            observed_session_id = attention_position_session_id(position)
            session = mgr.get_session(observed_session_id)
            if (
                session is not None
                and session_ref != session.target.worktree_id
            ):
                requested_session = _resolve_result_session(mgr, session_ref)
                if (
                    requested_session is None
                    or requested_session.session_id != session.session_id
                ):
                    raise AttentionTokenError(
                        "attention position targets a different delegate"
                    )
    except AttentionTokenError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session or worktree {session_ref} not found",
        )
    if session.event_log is None:
        raise HTTPException(
            status_code=409,
            detail="Session history is not loaded in the active bridge generation",
        )

    deadline = time.monotonic() + timeout_seconds
    while True:
        after = session.event_log.latest_id
        try:
            result = evaluate_owned_attention(
                db=mgr.db,
                session=session,
                requested_ref=session_ref,
                reasons=reason,
                position=position,
            )
        except AttentionHistoryChangedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AttentionTokenError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result.settled or result.identity.successor_id:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return result
        await session.event_log.wait_for_events(
            after, timeout=min(remaining, 30.0)
        )


@router.post("/{session_id}/turns", response_model=SubmitPromptResponse)
async def submit_prompt(
    session_id: str,
    req: SubmitPromptRequest,
    request: Request,
    response: Response,
):
    """Submit a prompt to a session.

    Default (``queue=false``) preserves the legacy contract: run now, or 409 if
    the session is busy. With ``queue=true`` the prompt is durably queued when
    the session is busy (persisted to ``pending_prompts``, delivered FIFO on
    settle -- surviving remount/crash/restart); the response is 202 with
    ``queued=true`` and the queue position.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        if req.queue:
            result = await mgr.submit_or_queue_prompt(
                session_id, req.prompt, caller_id=req.caller_id
            )
        else:
            turn_index = await mgr.submit_prompt(session_id, req.prompt)
            result = {"queued": False, "turn_index": turn_index}
    except DaemonDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ProviderTargetRefreshError as exc:
        raise HTTPException(
            status_code=502,
            detail=ProviderTargetRefreshError.public_message,
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    session = mgr.get_session(session_id)
    status = session.status if session else SessionStatus.IDLE
    if result.get("queued"):
        response.status_code = 202
        return SubmitPromptResponse(
            status=status,
            queued=True,
            queue_id=result.get("queue_id"),
            position=result.get("position"),
        )
    return SubmitPromptResponse(
        turn_index=result.get("turn_index"),
        status=status,
    )


@router.get("/{session_id}/queue", response_model=PendingQueueResponse)
async def get_pending_queue(session_id: str, request: Request):
    """Snapshot a session's durable pending-prompt queue (FIFO order)."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        rows = mgr.list_pending_queue(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return PendingQueueResponse(
        session_id=session_id,
        pending=[PendingPrompt(**r) for r in rows],
    )


@router.delete("/{session_id}/queue/{queue_id}", status_code=204)
async def remove_pending_prompt(session_id: str, queue_id: int, request: Request):
    """Drop one queued follow-up by id (operator removes a chip)."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        hit = mgr.remove_pending_prompt(session_id, queue_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if not hit:
        raise HTTPException(
            status_code=404,
            detail=f"Queued prompt {queue_id} not found for session {session_id}",
        )


@router.delete("/{session_id}/queue", status_code=204)
async def clear_pending_queue(session_id: str, request: Request):
    """Clear a session's whole durable pending-prompt queue."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        mgr.clear_pending_queue(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@router.post("/{session_id}/resync", response_model=ResyncSessionResponse)
async def resync_session(session_id: str, request: Request):
    """Rebuild a session's event log from the agent's authoritative replay.

    Heals logs truncated by a mid-session disconnect. Reattaches the ACP
    session and leaves it IDLE, ready for prompts.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        count = await mgr.resync_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ProviderTargetRefreshError as exc:
        raise HTTPException(
            status_code=502,
            detail=ProviderTargetRefreshError.public_message,
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    session = mgr.get_session(session_id)
    latest_id = (
        session.event_log.latest_id
        if session and session.event_log
        else count
    )
    return ResyncSessionResponse(
        event_count=count,
        latest_id=latest_id,
        status=session.status if session else SessionStatus.IDLE,
    )


@router.get("/{session_id}/events")
async def get_events(
    session_id: str,
    request: Request,
    after: int | None = None,
    caller_id: str | None = None,
    controlled: bool = False,
    continuity_id: str | None = Query(
        default=None, min_length=1, max_length=128
    ),
    transient: bool = False,
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

    cursor_state = None
    current_continuity_id = None
    if controlled:
        if not caller_id:
            raise HTTPException(
                status_code=422,
                detail="controlled event delivery requires caller_id",
            )
        if transient and (after is None or continuity_id is None):
            raise HTTPException(
                status_code=422,
                detail=(
                    "transient controlled resume requires after and continuity_id"
                ),
            )
        cursor_state = mgr.db.get_controlled_cursor_state(
            _cursor_key(caller_id), session_id
        )
        invalidation = cursor_state.get("invalidation")
        if invalidation:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cursor_invalidated",
                    "message": "the caller cursor was invalidated by an event-log rebuild",
                    "action": "full_reconcile",
                    **invalidation,
                },
            )
        current_continuity_id = cursor_state["continuity_id"]
        if (
            continuity_id is not None
            and continuity_id != current_continuity_id
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cursor_invalidated",
                    "message": "the authoritative event log continuity changed",
                    "action": "full_reconcile",
                    "prior_continuity_id": continuity_id,
                    "continuity_id": current_continuity_id,
                },
            )

    if after is None:
        start = (
            cursor_state["last_acked_id"]
            if cursor_state is not None
            else mgr.db.get_cursor(_cursor_key(caller_id), session_id)
        )
    else:
        start = after
    if controlled:
        durable_cursor = int(cursor_state["last_acked_id"])
        transient_resume = (
            transient
            and after is not None
            and after >= durable_cursor
            and continuity_id == current_continuity_id
        )
        if after is not None and after != durable_cursor and not transient_resume:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "cursor_mismatch",
                    "message": "the requested start does not match the durable caller cursor",
                    "action": "full_reconcile",
                    "requested_after": after,
                    "last_acked_id": durable_cursor,
                },
            )
        head_id = int(cursor_state["head_id"])
        if start > head_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "replay_gap",
                    "message": "the durable caller cursor is beyond the authoritative event head",
                    "action": "full_reconcile",
                    "last_acked_id": start,
                    "head_id": head_id,
                },
            )
        mgr.db.ensure_cursor(
            _cursor_key(caller_id), session_id, time.time()
        )

    server = getattr(request.app.state, "uvicorn_server", None)
    return StreamingResponse(
        _sse_event_stream(session, start, server=server,
                          is_disconnected=getattr(request, "is_disconnected", None),
                          mgr=mgr,
                          signal_gaps=controlled,
                          expected_continuity_id=(
                              current_continuity_id
                              if controlled
                              else continuity_id
                          ),
                          heartbeat_interval=5.0 if controlled else 30.0),
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
    state = mgr.db.get_controlled_cursor_state(
        _cursor_key(caller_id), session_id
    )
    return CursorInfo(
        session_id=session_id,
        caller_id=caller_id,
        last_acked_id=state["last_acked_id"],
        head_id=state["head_id"],
        continuity_id=state["continuity_id"],
        cursor_registered=state["registered"],
        invalidation=state["invalidation"],
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
    if req.continuity_id is None:
        effective = mgr.db.set_cursor(
            _cursor_key(req.caller_id),
            session_id,
            req.last_id,
            time.time(),
        )
        state = mgr.db.get_controlled_cursor_state(
            _cursor_key(req.caller_id), session_id
        )
        return CursorInfo(
            session_id=session_id,
            caller_id=req.caller_id,
            last_acked_id=effective,
            head_id=state["head_id"],
            continuity_id=state["continuity_id"],
            cursor_registered=True,
            invalidation=state["invalidation"],
        )

    result = mgr.db.acknowledge_controlled_cursor(
        _cursor_key(req.caller_id),
        session_id,
        req.last_id,
        time.time(),
        continuity_id=req.continuity_id,
    )
    if not result["accepted"]:
        code = result["code"]
        raise HTTPException(
            status_code=409,
            detail={
                "code": code,
                "message": (
                    "the acknowledgement names a replaced event log"
                    if code == "cursor_invalidated"
                    else "the acknowledgement is beyond the event head"
                ),
                "action": "full_reconcile",
                "prior_continuity_id": req.continuity_id,
                "continuity_id": result["continuity_id"],
                "head_id": result["head_id"],
                **(result["invalidation"] or {}),
            },
        )
    effective = result["last_acked_id"]
    return CursorInfo(
        session_id=session_id,
        caller_id=req.caller_id,
        last_acked_id=effective,
        head_id=result["head_id"],
        continuity_id=result["continuity_id"],
        cursor_registered=True,
    )


@router.post("/{session_id}/stop", status_code=204)
async def stop_session(
    session_id: str, request: Request, force: bool = False, reap_host: bool = False
):
    """Stop a session, preserving state for resume.

    ``reap_host=true`` additionally FREES the Session-Host child immediately
    (the same primitive the idle-reaper uses) instead of merely detaching it to
    keep it reattachable. A caller that never reattaches over the bridge (e.g.
    the AI reviewer, which resumes from on-disk session-state
    + worktree via a fresh child) uses this to reclaim the ~280 MB child on the
    spot rather than waiting out the idle-reaper TTL -- while the session stays
    STOPPED and resumable via ``load_session`` replay. Default ``false`` keeps
    the reattach-friendly behavior for fronts like Neuron Forge.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        await mgr.stop_session(session_id, force=force, reap_host=reap_host)
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{session_id}/parity/interrupt-relays")
async def interrupt_relays_for_parity(
    session_id: str,
    request: Request,
    timeout: float = 90.0,
):
    """Interrupt one harness-owned relay and wait for its supervisor to heal."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        return await mgr.interrupt_relays_for_parity(
            session_id,
            timeout=timeout,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=504, detail=str(exc))


@router.post("/{session_id}/parity/recreate-container")
async def recreate_container_for_parity(
    session_id: str,
    request: Request,
    timeout: float = 600.0,
):
    """Recreate one harness-owned container and return its replacement."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        return await mgr.recreate_container_for_parity(
            session_id,
            timeout=timeout,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise HTTPException(status_code=504, detail=str(exc))


@router.post("/{session_id}/interrupt", response_model=SessionInfo)
async def interrupt_turn(session_id: str, request: Request):
    """Interrupt the in-flight turn, leaving the session alive and idle.

    Cancels the *current turn* (ACP session/cancel) and returns the agent to
    idle -- distinct from ``/stop`` and ``DELETE`` (which tear the session down).
    A no-op if nothing is in flight. Returns the session's resulting state.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        session = await mgr.interrupt_turn(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return _session_info(session)


@router.post("/{session_id}/ask-user")
async def answer_ask_user(
    session_id: str, req: AnswerAskUserRequest, request: Request,
):
    """Answer a parked ``ask_user`` elicitation, unblocking the agent's turn.

    Resolves the session's pending ACP ``elicitation/create`` for the given
    tool call with the human's answer so the agent's ``ask_user`` completes.
    ``409`` if no matching request is outstanding (already answered, withdrawn,
    or never asked).
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        resolved = await mgr.answer_ask_user(
            session_id, req.tool_call_id, req.content, action=req.action,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not resolved:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No pending ask_user for tool call {req.tool_call_id} "
                f"on session {session_id}"
            ),
        )
    return {"status": "answered"}


@router.post("/{session_id}/permission")
async def answer_permission(
    session_id: str, req: AnswerPermissionRequest, request: Request,
):
    """Resolve the currently parked correlated permission request."""
    mgr: SessionManager = request.app.state.session_manager
    try:
        resolved = await mgr.answer_permission(
            session_id, req.request_id, req.option_id
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not resolved:
        raise HTTPException(
            status_code=409,
            detail=(
                f"No pending permission request {req.request_id} "
                f"on session {session_id}"
            ),
        )
    return {"status": "answered"}


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
    except ProviderTargetRefreshError as exc:
        raise HTTPException(
            status_code=502,
            detail=ProviderTargetRefreshError.public_message,
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return _session_info(session)


@router.post("/{session_id}/handoff", response_model=SessionInfo)
async def handoff_session(
    session_id: str,
    request: Request,
    reason: str | None = None,
    seed: bool = True,
):
    """Hand a hosted session off to a fresh successor in the SAME worktree.

    The in-place, bridge-native analogue of the interactive context handoff:
    the retiring child authors a continuation brief, a successor is spawned in
    the same worktree/agent/caller, a ``session_handoff`` event announces the
    changeover on both event streams, the successor is seeded with the brief,
    and the predecessor is retired (STOPPED, resumable). Returns the
    **successor** session so a caller can follow the baton in place.

    ``reason`` is an optional free-form label carried on the event (defaults to
    ``context-pressure``). ``seed=false`` skips seeding the successor's opening
    turn (the caller drives it instead).

    Errors: 404 (no such session), 409 (single-checkout agent or mid-turn),
    502 (successor failed to spawn -- predecessor retained), 503 (draining).
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        successor = await mgr.handoff_session(session_id, reason=reason, seed=seed)
    except DaemonDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return _session_info(successor)


@router.delete("/{session_id}", status_code=204)
async def end_session(session_id: str, request: Request, force: bool = False):
    mgr: SessionManager = request.app.state.session_manager
    try:
        await mgr.end_session(session_id, force=force)
    except SessionBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
