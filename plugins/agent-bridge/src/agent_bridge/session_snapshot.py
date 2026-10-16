"""Explicit in-memory session metadata staging, without ACP continuation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, Mapping

from pydantic import TypeAdapter
from zdd.private_protocol import MAX_FRAME_BYTES, Deadline, PrivateIPCError, Refusal

from .events import EventLog, SseEvent
from .models import SessionStatus
from .resolver_runtime import _encode
from .session_host.private_receipt import HostReceipt, VerifiedHostReceipt, decode_host_receipt
from .transport import SpawnTarget

if TYPE_CHECKING:
    from .session_manager import SessionManager


@dataclass(frozen=True)
class SessionObservation:
    client_present: bool
    prompt_pending: bool
    lifecycle_locked: bool
    turn_start_locked: bool
    subscriber_count: int


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    name: str
    target: SpawnTarget = field(repr=False)
    agent_name: str | None
    caller_id: str | None
    caller_session_id: str | None
    status: SessionStatus
    restart_status: str | None
    background_recovery_enabled: bool
    turn_count: int
    context_size: int | None
    context_used: int | None
    usage_model: str | None
    model_override: str | None
    effort_override: str | None
    last_usage_at: float | None
    created_at: float
    updated_at: float
    last_output_at: float | None
    last_heartbeat_at: float | None
    acp_session_id: str | None
    mcp_servers: list[dict[str, object]] = field(repr=False)
    parity_fault_result: dict[str, object] | None = field(repr=False)
    progress: dict[str, str]
    crossed_thresholds: list[str]
    handoff_pending: bool
    stop_requested: bool
    stop_notices: int
    events: list[SseEvent] | None = field(repr=False)
    next_event_id: int | None
    observation: SessionObservation
    host: HostReceipt | None = field(repr=False)


@dataclass(frozen=True)
class SessionSnapshot:
    schema: Literal[1]
    coverage: Literal["metadata-events-native-host-observations-v1"]
    sessions: list[SessionMetadata] = field(repr=False)


_ADAPTER = TypeAdapter(SessionSnapshot)
_SEMANTIC = (
    "session_id", "name", "target", "agent_name", "caller_id", "caller_session_id",
    "status", "restart_status", "background_recovery_enabled", "turn_count",
    "context_size", "context_used", "usage_model", "model_override", "effort_override",
    "last_usage_at", "created_at", "updated_at", "last_output_at", "last_heartbeat_at",
    "acp_session_id", "mcp_servers", "parity_fault_result", "progress",
)


def decode_sessions(payload: object) -> SessionSnapshot:
    from .session_manager import SessionManager

    try:
        wire = _encode(payload)
        if len(wire) > MAX_FRAME_BYTES - 4096:
            raise PrivateIPCError(Refusal.SIZE)
        state = _ADAPTER.validate_json(wire, strict=True)
        if (
            type(state.schema) is not int
            or len(state.sessions) > SessionManager.MAX_SESSIONS
            or _encode(json.loads(_ADAPTER.dump_json(state))) != wire
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        seen: set[str] = set()
        hosts: set[tuple[int, str]] = set()
        for session in state.sessions:
            if (
                not session.session_id or "\0" in session.session_id
                or session.session_id in seen
                or session.turn_count < 0 or session.stop_notices < 0
                or any(value is not None and value < 0 for value in (
                    session.created_at, session.updated_at, session.last_usage_at,
                    session.last_output_at, session.last_heartbeat_at,
                    session.context_size, session.context_used,
                ))
                or session.observation.subscriber_count < 0
                or len(set(session.crossed_thresholds)) != len(session.crossed_thresholds)
                or ((session.events is None) != (session.next_event_id is None))
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            seen.add(session.session_id)
            if session.events is not None:
                ids = [event.id for event in session.events]
                if (
                    any(index <= 0 for index in ids) or ids != sorted(set(ids))
                    or session.next_event_id is None
                    or session.next_event_id <= max(ids, default=0)
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
            if session.host is not None:
                receipt = decode_host_receipt(asdict(session.host))
                birth = (receipt.host.pid, receipt.host.start_time)
                if receipt.session_id != session.session_id or birth in hosts:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                hosts.add(birth)
            elif session.observation.client_present:
                raise PrivateIPCError(Refusal.AUTHORITY)
        return state
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_sessions(
    manager: SessionManager, receipts: Mapping[str, VerifiedHostReceipt], deadline: Deadline,
) -> dict[str, object]:
    """Observe metadata on the owner loop; handles/tasks/readers stay with owners."""
    from .acp_client import AcpClient
    from .session_manager import Session, SessionManager

    if type(manager) is not SessionManager:
        raise PrivateIPCError(Refusal.AUTHORITY)
    try:
        records = {record.session_id: record for record in manager._host_index.all()}
        if (
            not set(receipts) <= set(manager._sessions)
            or not set(records) <= set(receipts)
            or any(getattr(manager, name) for name in (
                "_forwards", "_relays", "_container_locks", "_codespace_locks",
                "_remote_reap_tasks", "_auto_handoff_tasks",
            ))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        sessions = []
        for sid, session in manager._sessions.items():
            deadline.remaining()
            if type(session) is not Session or session.session_id != sid:
                raise PrivateIPCError(Refusal.AUTHORITY)
            host = None
            if sid in receipts:
                if type(receipts[sid]) is not VerifiedHostReceipt:
                    raise PrivateIPCError(Refusal.IDENTITY)
                host = decode_host_receipt(receipts[sid].private_payload(deadline))
                record = records.get(sid)
                if (
                    host.session_id != sid or record is None or record.boundary != "local"
                    or record.port != host.data_endpoint.port or record.host_pid != host.host.pid
                    or record.child_pid != host.child_pid
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
            if session.client is not None and (
                type(session.client) is not AcpClient or host is None
                or not session.client._host_mode or session.client.pid != host.child_pid
                or session.client.acp_session_id != session.acp_session_id
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            value = {name: getattr(session, name) for name in _SEMANTIC}
            value["target"] = asdict(session.target)
            value["status"] = session.status.value
            value.update(
                crossed_thresholds=sorted(session._crossed_thresholds),
                handoff_pending=session._handoff_pending,
                stop_requested=session._stop_requested, stop_notices=session._stop_notices,
                events=None, next_event_id=None,
                observation=asdict(SessionObservation(
                    session.client is not None,
                    session._prompt_task is not None and not session._prompt_task.done(),
                    session._lifecycle_lock.locked(), session._turn_start_lock.locked(),
                    session.subscriber_count,
                )),
                host=asdict(host) if host is not None else None,
            )
            if session.event_log is not None:
                event_log = session.event_log
                if type(event_log) is not EventLog or event_log.merged_into is not None:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                with event_log._lock:
                    value["events"] = [asdict(event) for event in event_log._events]
                    value["next_event_id"] = event_log._next_id
            sessions.append(value)
        payload = {
            "schema": 1, "coverage": "metadata-events-native-host-observations-v1",
            "sessions": sessions,
        }
        decode_sessions(payload)
        return json.loads(_encode(payload))
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def hydrate_sessions(manager: SessionManager, snapshot: SessionSnapshot) -> None:
    """Prepare detached metadata; never claim/attach, replay ACP or restore tasks."""
    from .session_manager import Session

    state = decode_sessions(asdict(snapshot))
    if manager._sessions:
        raise PrivateIPCError(Refusal.AUTHORITY)
    for metadata in state.sessions:
        session = Session(
            metadata.session_id, metadata.name, metadata.target,
            metadata.agent_name, metadata.caller_id, metadata.caller_session_id,
            preserve_target=True,
        )
        for name in _SEMANTIC:
            setattr(session, name, getattr(metadata, name))
        session._crossed_thresholds = set(metadata.crossed_thresholds)
        session._handoff_pending = metadata.handoff_pending
        session._stop_requested = metadata.stop_requested
        session._stop_notices = metadata.stop_notices
        if metadata.events is not None:
            session.event_log = EventLog.from_captured_events(
                metadata.events, metadata.next_event_id, db=manager._db,
                session_id=metadata.session_id, acp_session_id=metadata.acp_session_id,
                worktree_id=metadata.target.worktree_id,
            )
        manager._sessions[metadata.session_id] = session
    # Observed in-flight flags and receipts are NOT restored Tasks or custody.
    manager._private_session_snapshot = state
