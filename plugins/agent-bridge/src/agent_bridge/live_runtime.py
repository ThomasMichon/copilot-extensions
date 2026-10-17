"""Private quiescent represented histories; no readers or ingestion authority."""

from __future__ import annotations

import json
import math
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter
from zdd.private_protocol import PrivateIPCError, Refusal

from .events import EventLog, SseEvent
from .live_representation import LiveEventStore, MergedIds, _SEEN_ID_CAP
from .resolver_runtime import _encode
from .telemetry import SessionTraceReducer

_MAX_KEYS = 256
_MAX_EVENTS = 4096
_MAX_BYTES = 262144
_MAX_NODES = 65536


@dataclass
class _Log:
    session_id: str
    events: list[SseEvent]
    next_id: int
    telemetry: dict[str, object]
    open_tools: dict[str, int]
    tool_order: list[str]


@dataclass
class _Binding:
    log: int
    seen: int | None
    sdk: int | None


@dataclass
class _History:
    target: str
    ids: list[tuple[int, int]]


@dataclass
class _Snapshot:
    schema: Literal[1]
    logs: list[_Log]
    bindings: dict[str, _Binding]
    key_order: list[str]
    seen: list[list[str]]
    sdk: list[dict[str, list[int]]]
    history: dict[str, _History]


_ADAPTER = TypeAdapter(_Snapshot)


def _bounded(value: object) -> None:
    """Reject unsupported graphs and bound materialization before JSON encoding."""
    nodes = size = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes, size
        nodes += 1
        if depth > 32 or nodes > _MAX_NODES:
            raise PrivateIPCError(Refusal.SIZE)
        if type(item) is str:
            size += len(item.encode("utf-8")) + 2
        elif item is None or type(item) in (bool, int, float):
            if type(item) is float and not math.isfinite(item):
                raise PrivateIPCError(Refusal.AUTHORITY)
            size += 32
        elif type(item) in (list, tuple):
            for child in item:
                visit(child, depth + 1)
        elif type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise PrivateIPCError(Refusal.AUTHORITY)
        if size > _MAX_BYTES:
            raise PrivateIPCError(Refusal.SIZE)

    visit(value, 0)


def _hold(stack: ExitStack, lock) -> None:
    if not lock.acquire(blocking=False):
        raise PrivateIPCError(Refusal.AUTHORITY)
    stack.callback(lock.release)


def _log(record: _Log) -> EventLog:
    ids = [event.id for event in record.events]
    identity = record.telemetry.get("_identity")
    if (
        not record.session_id or "\0" in record.session_id
        or ids != list(range(1, len(ids) + 1)) or record.next_id != len(ids) + 1
        or type(identity) is not dict
        or set(identity) != {"acp_session_id", "worktree_id", "source"}
        or identity["source"] != "represented" or identity["acp_session_id"] is not None
        or (identity["worktree_id"] is not None and type(identity["worktree_id"]) is not str)
        or any(not event.event or event.timestamp < 0 for event in record.events)
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    log = EventLog(session_id=record.session_id, telemetry_source="represented")
    log._telemetry._identity = dict(identity)
    for event in record.events:
        log._events.append(event)
        log._track_tool_event(event)
        if event.id == 1:
            log._telemetry.set_log_origin(event.timestamp)
        log._telemetry.observe(event.event, event.data, event_id=event.id)
    log._next_id = record.next_id
    expected = vars(log._telemetry)
    if not ids:
        epoch = record.telemetry.get("log_epoch")
        if type(epoch) is not str or not epoch:
            raise PrivateIPCError(Refusal.AUTHORITY)
        expected["log_epoch"] = epoch
    if (
        _encode(expected) != _encode(record.telemetry)
        or {key: event.id for key, event in log._open_tool_calls.items()} != record.open_tools
        or list(log._telemetry.tool_states) != record.tool_order
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    return log


def restore_live_events(payload: object) -> LiveEventStore:
    """Strictly rebuild isolated ordinary logs, without emissions or subscribers."""
    try:
        _bounded(payload)
        wire = _encode(payload)
        if len(wire) > _MAX_BYTES:
            raise PrivateIPCError(Refusal.SIZE)
        state = _ADAPTER.validate_json(wire, strict=True)
        if (
            type(state.schema) is not int
            or _encode(json.loads(_ADAPTER.dump_json(state))) != wire
            or max(len(state.bindings), len(state.logs), len(state.seen),
                   len(state.sdk), len(state.history)) > _MAX_KEYS
            or sum(len(log.events) for log in state.logs) > _MAX_EVENTS
            or len(state.key_order) != len(state.bindings)
            or set(state.key_order) != set(state.bindings)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        logs = [_log(record) for record in state.logs]
        seen = []
        order = []
        for row in state.seen:
            if len(row) > _SEEN_ID_CAP or len(set(row)) != len(row) or any(not key for key in row):
                raise PrivateIPCError(Refusal.AUTHORITY)
            seen.append(set(row))
            order.append(deque(row))
        used_logs, used_seen, used_sdk = set(), set(), set()
        seen_logs, sdk_seen = {}, {}
        store = LiveEventStore()
        for sid in state.key_order:
            binding = state.bindings[sid]
            if (
                not sid or "\0" in sid or not 0 <= binding.log < len(logs)
                or (binding.seen is not None and not 0 <= binding.seen < len(seen))
                or (binding.sdk is not None and (
                    binding.seen is None or not 0 <= binding.sdk < len(state.sdk)
                ))
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            used_logs.add(binding.log)
            store._logs[sid] = logs[binding.log]
            if binding.seen is not None:
                used_seen.add(binding.seen)
                if seen_logs.setdefault(binding.seen, binding.log) != binding.log:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                store._seen_ids[sid] = seen[binding.seen]
                store._seen_order[sid] = order[binding.seen]
            if binding.sdk is not None:
                used_sdk.add(binding.sdk)
                if sdk_seen.setdefault(binding.sdk, binding.seen) != binding.seen:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                mapping = state.sdk[binding.sdk]
                if (
                    not set(mapping) <= seen[binding.seen]
                    or any(
                        not ids or ids != sorted(set(ids))
                        or any(not 0 < eid < logs[binding.log]._next_id for eid in ids)
                        for ids in mapping.values()
                    )
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                store._sdk_events[sid] = mapping
        if (
            used_logs != set(range(len(logs))) or used_seen != set(range(len(seen)))
            or used_sdk != set(range(len(state.sdk)))
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        heads = {log.continuity_id: log.latest_id for log in logs if log.continuity_id}
        if len(heads) != sum(bool(log.continuity_id) for log in logs):
            raise PrivateIPCError(Refusal.AUTHORITY)
        for source, row in state.history.items():
            keys = [key for key, _value in row.ids]
            if not source or source in heads or keys != list(range(len(keys))) or not keys:
                raise PrivateIPCError(Refusal.AUTHORITY)
            target_head = heads.get(row.target)
            if target_head is None and row.target in state.history:
                target_head = len(state.history[row.target].ids) - 1
            if target_head is None or any(not 0 <= value <= target_head for _, value in row.ids):
                raise PrivateIPCError(Refusal.AUTHORITY)
            cursor, visited = source, set()
            while cursor in state.history:
                if cursor in visited:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                visited.add(cursor)
                cursor = state.history[cursor].target
            if cursor not in heads:
                raise PrivateIPCError(Refusal.AUTHORITY)
            store._merged_history[source] = (row.target, MergedIds(dict(row.ids)))
        return store
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, RecursionError, OverflowError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_live_events(store: LiveEventStore) -> dict[str, object]:
    """Refuse ongoing operations before copying all alias/dedup/cursor state."""
    try:
        if type(store) is not LiveEventStore:
            raise PrivateIPCError(Refusal.AUTHORITY)
        with ExitStack() as stack:
            _hold(stack, store._operation_lock)
            if type(store._operations) is not int or store._operations != 0:
                raise PrivateIPCError(Refusal.AUTHORITY)
            _hold(stack, store._lock)
            if (
                store._merging or len(store._logs) > _MAX_KEYS
                or set(store._seen_ids) != set(store._seen_order)
                or not set(store._seen_ids) <= set(store._logs)
                or not set(store._sdk_events) <= set(store._seen_ids)
                or len(store._merged_history) > _MAX_KEYS
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
            logs, seen, sdk = [], [], []
            log_refs, seen_refs, order_refs, sdk_refs = {}, {}, {}, {}
            bindings = {}
            total = 0
            for sid, log in store._logs.items():
                if id(log) not in log_refs:
                    if type(log) is not EventLog:
                        raise PrivateIPCError(Refusal.AUTHORITY)
                    _hold(stack, log._lock)
                    if (
                        log._db is not None or log._waiters or log.merged_into is not None
                        or type(log._telemetry) is not SessionTraceReducer
                        or len(log._open_tool_calls) > _MAX_EVENTS
                        or any(type(event) is not SseEvent for event in log._events)
                        or any(log._events[event.id - 1] is not event
                               for event in log._open_tool_calls.values())
                    ):
                        raise PrivateIPCError(Refusal.AUTHORITY)
                    total += len(log._events)
                    if total > _MAX_EVENTS:
                        raise PrivateIPCError(Refusal.SIZE)
                    log_refs[id(log)] = len(logs)
                    record = {
                        "session_id": log._session_id,
                        "events": [vars(event) for event in log._events],
                        "next_id": log._next_id, "telemetry": vars(log._telemetry),
                        "open_tools": {key: event.id for key, event in log._open_tool_calls.items()},
                        "tool_order": list(log._telemetry.tool_states),
                    }
                    _bounded(record)
                    logs.append(record)
                seen_ref = sdk_ref = None
                if sid in store._seen_ids:
                    current, fifo = store._seen_ids[sid], store._seen_order[sid]
                    if (
                        type(current) is not set or type(fifo) is not deque
                        or fifo.maxlen is not None or len(fifo) > _SEEN_ID_CAP
                        or len(current) != len(fifo) or current != set(fifo)
                        or order_refs.setdefault(id(fifo), id(current)) != id(current)
                    ):
                        raise PrivateIPCError(Refusal.AUTHORITY)
                    if id(current) not in seen_refs:
                        seen_refs[id(current)] = (len(seen), id(fifo))
                        seen.append(list(fifo))
                    seen_ref, fifo_id = seen_refs[id(current)]
                    if fifo_id != id(fifo):
                        raise PrivateIPCError(Refusal.AUTHORITY)
                if sid in store._sdk_events:
                    mapping = store._sdk_events[sid]
                    if type(mapping) is not dict:
                        raise PrivateIPCError(Refusal.AUTHORITY)
                    _bounded(mapping)
                    if id(mapping) not in sdk_refs:
                        sdk_refs[id(mapping)] = len(sdk)
                        sdk.append(mapping)
                    sdk_ref = sdk_refs[id(mapping)]
                bindings[sid] = {"log": log_refs[id(log)], "seen": seen_ref, "sdk": sdk_ref}
            history = {}
            for source, (target, mapping) in store._merged_history.items():
                if type(mapping) is not MergedIds:
                    raise PrivateIPCError(Refusal.AUTHORITY)
                if len(mapping) > _MAX_EVENTS + 1:
                    raise PrivateIPCError(Refusal.SIZE)
                rebuilt = MergedIds(mapping)
                if (
                    mapping.keys_sorted != rebuilt.keys_sorted
                    or mapping.prefix_max != rebuilt.prefix_max
                ):
                    raise PrivateIPCError(Refusal.AUTHORITY)
                history[source] = {"target": target, "ids": sorted(mapping.items())}
            payload = {
                "schema": 1, "logs": logs, "bindings": bindings,
                "key_order": list(store._logs), "seen": seen, "sdk": sdk, "history": history,
            }
            _bounded(payload)
            wire = _encode(payload)
        detached = json.loads(wire)
        restore_live_events(detached)
        return detached
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError, RecursionError, OverflowError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
