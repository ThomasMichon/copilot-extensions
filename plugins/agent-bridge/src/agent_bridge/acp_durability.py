"""Independent committed SQLite observations for explicitly idle ACP clients.

This is not activation authority, a database claim or power-loss durability.
No source writer is flushed: uncommitted event delivery refuses observation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter
from zdd.private_protocol import MAX_FRAME_BYTES, Deadline, PrivateIPCError, Refusal

from .acp_continuation import capture_idle_continuation, decode_idle_continuation
from .db import Database
from .events import EventLog
from .models import SessionStatus
from .resolver_runtime import _encode
from .session_host.private_receipt import VerifiedHostReceipt
from .session_manager import Session

_ROW_LIMIT = 2048


@dataclass(frozen=True)
class DurableEvent:
    event_id: int
    event_type: str
    data: dict[str, Any] = field(repr=False)
    timestamp: float


@dataclass(frozen=True)
class DurableTurn:
    turn_index: int
    prompt: str = field(repr=False)
    response_text: str = field(repr=False)
    thought_text: str = field(repr=False)
    stop_reason: str
    tool_calls: list[dict[str, Any]] = field(repr=False)
    started_at: float
    completed_at: float


@dataclass(frozen=True)
class IdleDurability:
    schema: Literal[1]
    coverage: Literal["independent-sqlite-idle-events-turns-v1"]
    continuation_sha256: str
    database_identity: tuple[int, int]
    session_id: str
    acp_session_id: str
    next_event_id: int
    events: list[DurableEvent] = field(repr=False)
    turns: list[DurableTurn] = field(repr=False)


_ADAPTER = TypeAdapter(IdleDurability)


def decode_idle_durability(payload: object) -> IdleDurability:
    try:
        wire = _encode(payload)
        if len(wire) > MAX_FRAME_BYTES - 4096:
            raise PrivateIPCError(Refusal.SIZE)
        state = _ADAPTER.validate_json(wire, strict=True)
        if (
            type(state.schema) is not int
            or _encode(json.loads(_ADAPTER.dump_json(state))) != wire
            or len(state.continuation_sha256) != 64
            or any(c not in "0123456789abcdef" for c in state.continuation_sha256)
            or any(type(value) is not int or value < 0 for value in state.database_identity)
            or state.database_identity == (0, 0)
            or not state.session_id or "\0" in state.session_id
            or not state.acp_session_id or "\0" in state.acp_session_id
            or type(state.next_event_id) is not int
            or len(state.events) > _ROW_LIMIT or len(state.turns) > _ROW_LIMIT
            or [event.event_id for event in state.events] != list(range(1, len(state.events) + 1))
            or state.next_event_id != len(state.events) + 1
            or [turn.turn_index for turn in state.turns] != list(range(len(state.turns)))
            or any(not event.event_type or not math.isfinite(event.timestamp)
                   for event in state.events)
            or any(
                not turn.stop_reason or turn.stop_reason.startswith("error:")
                or not math.isfinite(turn.started_at) or not math.isfinite(turn.completed_at)
                or turn.completed_at < turn.started_at
                for turn in state.turns
            )
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        return state
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def _events(log: EventLog) -> list[DurableEvent]:
    with log._lock:
        if log.merged_into is not None or len(log._events) > _ROW_LIMIT:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return [
            DurableEvent(event.id, event.event, event.data, event.timestamp)
            for event in log._events
        ]


def _read_committed(
    path: Path, file_id: tuple[int, int], session_id: str, deadline: Deadline,
) -> tuple[dict[str, Any], list[DurableEvent], list[DurableTurn]]:
    """Use a separate mode=ro owner and one coherent WAL read transaction."""
    reader = None
    try:
        deadline.remaining()
        reader = Database(path, read_only=True)
        if reader._read_only_file_id != file_id:
            raise PrivateIPCError(Refusal.IDENTITY)
        conn = reader._get_conn()
        end = time.monotonic() + deadline.remaining()
        conn.set_progress_handler(lambda: int(time.monotonic() >= end), 100)
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT id, acp_session_id, status FROM sessions WHERE id=?", (session_id,),
        ).fetchall()
        if len(rows) != 1:
            raise PrivateIPCError(Refusal.AUTHORITY)
        budget = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(data_json AS BLOB)) "
            "+ LENGTH(CAST(event_type AS BLOB))),0) FROM events WHERE session_id=?",
            (session_id,),
        ).fetchone()
        turn_budget = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(prompt AS BLOB)) "
            "+ LENGTH(CAST(response_text AS BLOB)) + LENGTH(CAST(thought_text AS BLOB)) "
            "+ LENGTH(CAST(tool_calls_json AS BLOB)) + LENGTH(CAST(stop_reason AS BLOB))),0) "
            "FROM turns WHERE session_id=?", (session_id,),
        ).fetchone()
        if (
            budget[0] > _ROW_LIMIT or turn_budget[0] > _ROW_LIMIT
            or budget[1] + turn_budget[1] > MAX_FRAME_BYTES - 4096
        ):
            raise PrivateIPCError(Refusal.SIZE)
        events = conn.execute(
            "SELECT event_id, event_type, data_json, timestamp FROM events "
            "WHERE session_id=? ORDER BY event_id LIMIT ?", (session_id, _ROW_LIMIT + 1),
        ).fetchall()
        turns = conn.execute(
            "SELECT turn_index, prompt, response_text, thought_text, stop_reason, "
            "tool_calls_json, started_at, completed_at FROM turns "
            "WHERE session_id=? ORDER BY turn_index LIMIT ?", (session_id, _ROW_LIMIT + 1),
        ).fetchall()
        if len(events) > _ROW_LIMIT or len(turns) > _ROW_LIMIT:
            raise PrivateIPCError(Refusal.SIZE)
        if conn.execute(
            "SELECT 1 FROM pending_prompts WHERE session_id=? LIMIT 1", (session_id,),
        ).fetchone() is not None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        result = (
            dict(rows[0]),
            [DurableEvent(row["event_id"], row["event_type"],
                          json.loads(row["data_json"]), row["timestamp"]) for row in events],
            [DurableTurn(row["turn_index"], row["prompt"], row["response_text"],
                         row["thought_text"], row["stop_reason"],
                         json.loads(row["tool_calls_json"]),
                         row["started_at"], row["completed_at"]) for row in turns],
        )
        after = path.stat()
        if (after.st_dev, after.st_ino) != file_id:
            raise PrivateIPCError(Refusal.IDENTITY)
        deadline.remaining()
        return result
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError):
        deadline.remaining()
        raise PrivateIPCError(Refusal.AUTHORITY) from None
    finally:
        if reader is not None:
            reader.close()


def _require_reducer(state: IdleDurability, continuation: object) -> None:
    idle = decode_idle_continuation(continuation)
    if (
        state.continuation_sha256 != hashlib.sha256(_encode(continuation)).hexdigest()
        or state.session_id != idle.host.session_id
        or state.acp_session_id != idle.acp_session_id
        or state.next_event_id != idle.next_event_id or idle.reducer.prompt_error is not None
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    if state.turns:
        turn = state.turns[-1]
        if (
            not idle.reducer.prompt_complete
            or turn.response_text != "".join(idle.reducer.response)
            or turn.thought_text != "".join(idle.reducer.thought)
            or turn.stop_reason != idle.reducer.stop_reason
            or _encode(turn.tool_calls) != _encode([asdict(tool) for tool in idle.reducer.tools])
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
    elif idle.reducer.prompt_complete or idle.reducer.response or idle.reducer.thought or idle.reducer.tools:
        raise PrivateIPCError(Refusal.AUTHORITY)


def _require_session(session: Session, continuation: object) -> None:
    idle = decode_idle_continuation(continuation)
    if (
        type(session) is not Session or session.status is not SessionStatus.IDLE
        or session.session_id != idle.host.session_id or session.acp_session_id != idle.acp_session_id
        or type(session.event_log) is not EventLog or session.client is None
        or type(session.turn_count) is not int or not 0 <= session.turn_count <= _ROW_LIMIT
        or (session._prompt_task is not None and not session._prompt_task.done())
        or session._lifecycle_lock.locked() or session._turn_start_lock.locked()
        or session._handoff_pending or session._stop_requested
        or session.has_active_background_tasks
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)


async def capture_idle_durability(
    session: Session, continuation: object, receipt: VerifiedHostReceipt, deadline: Deadline,
) -> dict[str, object]:
    """Capture exact committed contents only; never flush a pending source writer."""
    _require_session(session, continuation)
    log = session.event_log
    source = log._db
    if type(source) is not Database or type(deadline) is not Deadline:
        raise PrivateIPCError(Refusal.AUTHORITY)
    path = source.db_path
    try:
        before = await asyncio.to_thread(path.stat)
    except OSError:
        raise PrivateIPCError(Refusal.IDENTITY) from None
    row, events, turns = await asyncio.to_thread(
        _read_committed, path, (before.st_dev, before.st_ino), session.session_id, deadline,
    )
    _require_session(session, continuation)
    if (
        row != {"id": session.session_id, "acp_session_id": session.acp_session_id, "status": "idle"}
        or log is not session.event_log or log._db is not source or source.db_path != path
        or session.turn_count != len(turns)
        or _encode([asdict(event) for event in _events(log)])
        != _encode([asdict(event) for event in events])
        or _encode(capture_idle_continuation(session.client, receipt, log, deadline))
        != _encode(continuation)
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    state = IdleDurability(
        1, "independent-sqlite-idle-events-turns-v1",
        hashlib.sha256(_encode(continuation)).hexdigest(), (before.st_dev, before.st_ino), session.session_id,
        session.acp_session_id, log._next_id, events, turns,
    )
    payload = json.loads(_encode(asdict(state)))
    validated = decode_idle_durability(payload)
    _require_reducer(validated, continuation)
    return payload


async def verify_idle_durability(
    payload: object, continuation: object, log: EventLog, source: Database, deadline: Deadline,
    *, log_database: Database | None = None,
) -> IdleDurability:
    """Re-read independently in the receiver; returned data grants no mutation."""
    return await asyncio.to_thread(
        require_idle_durable_contents, payload, continuation, log, source, deadline,
        log_database=log_database,
    )


def require_idle_durable_contents(
    payload: object, continuation: object, log: EventLog, source: Database, deadline: Deadline,
    *, log_database: Database | None = None,
) -> IdleDurability:
    """Bounded independent readback usable at a synchronous receiver commit fence."""
    state = decode_idle_durability(payload)
    _require_reducer(state, continuation)
    if (
        type(log) is not EventLog or type(source) is not Database or type(deadline) is not Deadline
        or not source.read_only
        or log._db is not (source if log_database is None else log_database)
        or source._read_only_file_id is None
        or source._read_only_file_id != state.database_identity
        or log._session_id != state.session_id
        or log._telemetry._identity.get("acp_session_id") != state.acp_session_id
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    if log_database is not None and (
        type(log_database) is not Database or log_database.read_only
        or log_database.db_path != source.db_path
        or log_database._read_only_file_id != source._read_only_file_id
        or log_database._writer_error is not None
        or log_database._event_write_q.unfinished_tasks
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    row, events, turns = _read_committed(
        source.db_path, source._read_only_file_id, state.session_id, deadline,
    )
    with log._lock:
        cursor = log._next_id
    if (
        row != {"id": state.session_id, "acp_session_id": state.acp_session_id, "status": "idle"}
        or cursor != state.next_event_id
        or _encode([asdict(event) for event in events]) != _encode([asdict(event) for event in state.events])
        or _encode([asdict(turn) for turn in turns]) != _encode([asdict(turn) for turn in state.turns])
        or _encode([asdict(event) for event in _events(log)]) != _encode([asdict(event) for event in state.events])
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    deadline.remaining()
    return state
