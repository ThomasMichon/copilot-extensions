"""Append-only event log with monotonic IDs and SQLite persistence.

Each session has its own EventLog. Events are appended with
auto-incrementing integer IDs. The SSE endpoint uses ``get_events(after=N)``
to enable reconnect-safe streaming -- the client sends the last seen ID
and gets only newer events.

SSE event log for agent-bridge sessions.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any

from . import telemetry

if TYPE_CHECKING:
    from .db import Database


# Tool-call statuses (carried on tool_call_update events) that mean the call
# has finished, one way or another. Mirrors render._TERMINAL_TOOL_STATUS but
# kept local so the event log has no dependency on the display layer.
_TERMINAL_TOOL_STATUSES = frozenset(
    {
        "completed", "complete", "success", "succeeded",
        "failed", "error", "cancelled", "canceled",
    }
)


@dataclass
class SseEvent:
    """A single SSE-ready event with a monotonic ID."""

    id: int
    event: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class EventLog:
    """Thread-safe append-only event log for a single session.

    When a ``Database`` and ``session_id`` are provided, events are
    persisted to SQLite on every append. The in-memory list is kept for
    live SSE consumers.
    """

    def __init__(
        self,
        *,
        db: Database | None = None,
        session_id: str | None = None,
        acp_session_id: str | None = None,
        worktree_id: str | None = None,
        telemetry_source: str = "owned",
    ) -> None:
        self._events: list[SseEvent] = []
        self._open_tool_calls: dict[str, SseEvent] = {}
        self._lock = Lock()
        self._next_id = 1
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = []
        self._db = db
        self._session_id = session_id
        self._telemetry = telemetry.SessionTraceReducer(
            session_id,
            acp_session_id=acp_session_id,
            worktree_id=worktree_id,
            source=telemetry_source,
        )

    @classmethod
    def from_db(
        cls,
        db: Database,
        session_id: str,
        *,
        acp_session_id: str | None = None,
        worktree_id: str | None = None,
        telemetry_source: str = "owned",
    ) -> EventLog:
        """Create an EventLog pre-populated with persisted events."""
        db.flush()
        log = cls(
            db=db,
            session_id=session_id,
            acp_session_id=acp_session_id,
            worktree_id=worktree_id,
            telemetry_source=telemetry_source,
        )
        rows = db.get_events(session_id, after=0)
        with log._lock:
            for row in rows:
                evt = SseEvent(
                    id=row["event_id"],
                    event=row["event_type"],
                    data=row["data"],
                    timestamp=row["timestamp"],
                )
                log._events.append(evt)
                log._track_tool_event(evt)
                if evt.id == 1:
                    log._telemetry.set_log_origin(evt.timestamp)
                log._telemetry.observe(
                    evt.event, evt.data, event_id=evt.id
                )

            max_id = db.get_max_event_id(session_id)
            log._next_id = max_id + 1
        return log

    def append(self, event_type: str, data: dict[str, Any]) -> SseEvent:
        """Append an event and return it with its assigned ID.

        Adds the event to the in-memory list and wakes SSE consumers before
        queueing the durable write, so live delivery is not blocked by SQLite.
        """
        ts = time.time()

        with self._lock:
            event_id = self._next_id
            self._next_id += 1
            evt = SseEvent(id=event_id, event=event_type, data=data, timestamp=ts)
            self._events.append(evt)
            self._track_tool_event(evt)
            waiters = list(self._waiters)
            if event_id == 1:
                self._telemetry.set_log_origin(ts)
            telemetry_records = self._telemetry.observe(
                event_type, data, event_id=event_id
            )

        for loop, waiter in waiters:
            if not loop.is_closed():
                loop.call_soon_threadsafe(waiter.set)
        if self._db is not None and self._session_id is not None:
            self._db.append_event(
                self._session_id, event_id, event_type, data, ts,
            )
            if event_id == 1 and self._telemetry.log_epoch:
                self._db.flush()
                self._db.update_delivery_cursor_invalidation_continuity(
                    self._session_id,
                    self._telemetry.log_epoch,
                    timestamp=ts,
                )
        # Generic telemetry seam: emit only the reducer's content-free
        # structural transitions (no-op unless a consumer registered a sink).
        for record in telemetry_records:
            telemetry.emit(record)
        return evt

    def set_telemetry_identity(
        self,
        *,
        acp_session_id: str | None = None,
        worktree_id: str | None = None,
    ) -> None:
        """Fill stable identities learned after session startup."""
        with self._lock:
            self._telemetry.update_identity(
                acp_session_id=acp_session_id,
                worktree_id=worktree_id,
            )

    @property
    def telemetry_conversation_state(self) -> str | None:
        """Conversation state derived from the durable event sequence."""
        with self._lock:
            return self._telemetry.conversation_state

    def get_events(self, after: int = 0) -> list[SseEvent]:
        """Return events with ID > ``after``."""
        with self._lock:
            if after == 0:
                return list(self._events)
            return [e for e in self._events if e.id > after]

    def snapshot_history(
        self, *, durable: bool = False
    ) -> tuple[str | None, list[SseEvent]]:
        """Atomically snapshot one event-log history and its continuity."""
        with self._lock:
            visible_count = len(self._events)
            if durable and self._db is not None and self._session_id is not None:
                durable_head = self._db.get_max_event_id(self._session_id)
                visible_count = min(visible_count, max(0, durable_head))
            continuity = (
                (self._telemetry.log_epoch or None)
                if visible_count > 0
                else None
            )
            return (continuity, list(self._events[:visible_count]))

    def rebuild(self, events: list[tuple[str, dict[str, Any]]]) -> int:
        """Replace the entire log with ``events`` (event_type, data) pairs.

        Clears both the persisted and in-memory events for this session and
        re-appends the supplied sequence from ID 1. Used by the resync flow,
        which treats the agent's load-time replay as the authoritative
        conversation history -- healing logs truncated by a mid-session
        disconnect. Returns the number of events written.
        """
        with self._lock:
            prior_head_id = self._events[-1].id if self._events else 0
            prior_origin_timestamp = (
                self._events[0].timestamp if self._events else None
            )
            prior_epoch = self._telemetry.log_epoch
            ts = time.time()
            if prior_origin_timestamp is not None:
                ts = max(
                    ts,
                    math.nextafter(prior_origin_timestamp, math.inf),
                )
            if self._db is not None and self._session_id is not None:
                self._db.begin_event_rebuild(
                    self._session_id,
                    prior_head_id=prior_head_id,
                    prior_continuity_id=prior_epoch or None,
                    timestamp=ts,
                )
            self._telemetry.begin_rebuild()
            self._events = []
            self._open_tool_calls = {}
            self._next_id = 1
            for event_type, data in events:
                event_id = self._next_id
                self._next_id += 1
                if self._db is not None and self._session_id is not None:
                    self._db.append_event(
                        self._session_id, event_id, event_type, data, ts,
                    )
                evt = SseEvent(
                    id=event_id, event=event_type, data=data, timestamp=ts
                )
                self._events.append(evt)
                self._track_tool_event(evt)
                if event_id == 1:
                    self._telemetry.set_log_origin(ts)
                # Rebuild reducer state from authoritative history, but do not
                # emit historical telemetry again.
                self._telemetry.observe(event_type, data, event_id=event_id)
            count = len(self._events)
            rebuild_marker = self._telemetry.complete_rebuild(
                prior_epoch, count
            )
            if self._db is not None and self._session_id is not None:
                self._db.flush()
                if self._telemetry.log_epoch:
                    self._db.update_delivery_cursor_invalidation_continuity(
                        self._session_id,
                        self._telemetry.log_epoch,
                        timestamp=ts,
                    )
        for loop, waiter in self._waiters:
            if not loop.is_closed():
                loop.call_soon_threadsafe(waiter.set)
        telemetry.emit(rebuild_marker)
        return count

    @property
    def latest_id(self) -> int:
        """The ID of the most recent event, or 0 if empty."""
        with self._lock:
            return self._events[-1].id if self._events else 0

    @property
    def continuity_id(self) -> str | None:
        """Private event-log continuity identity for opaque read positions.

        The telemetry reducer derives this value from the first event's durable
        timestamp, so an owned log reproduces it across restart and rotates it
        when resync rebuilds history. Empty logs have no usable position.
        Callers must never expose or parse this value directly.
        """
        with self._lock:
            if not self._events:
                return None
            return self._telemetry.log_epoch or None

    def snapshot_window(
        self, *, after: int | None, limit: int, durable: bool = False
    ) -> tuple[str | None, int, list[SseEvent]]:
        """Atomically snapshot continuity, head, and a bounded event window.

        ``after=None`` selects the latest tail. An integer selects the ascending
        prefix after that event. Keeping all three reads under one lock prevents
        an append or rebuild from mixing a position from one history with events
        from another.
        """
        durable_head: int | None = None
        if durable and self._db is not None and self._session_id is not None:
            durable_head = self._db.get_max_event_id(self._session_id)
        with self._lock:
            if not self._events:
                return (None, 0, [])
            head_limit = (
                durable_head if durable_head is not None else self._events[-1].id
            )
            visible_count = min(len(self._events), max(0, head_limit))
            if visible_count <= 0:
                return (None, 0, [])
            continuity = self._telemetry.log_epoch or None
            head = self._events[visible_count - 1].id
            if limit <= 0:
                return (continuity, head, [])
            if after is None:
                start = max(0, visible_count - limit)
                rows = list(self._events[start:visible_count])
            else:
                start = max(0, after)
                end = min(visible_count, start + limit)
                rows = list(self._events[start:end])
            return (continuity, head, rows)

    def snapshot_event(
        self, event_id: int, *, durable: bool = False
    ) -> tuple[str | None, SseEvent | None]:
        """Atomically read one event with the continuity that identifies it."""
        durable_head: int | None = None
        if durable and self._db is not None and self._session_id is not None:
            durable_head = self._db.get_max_event_id(self._session_id)
        with self._lock:
            head_limit = (
                durable_head
                if durable_head is not None
                else (self._events[-1].id if self._events else 0)
            )
            visible_count = min(len(self._events), max(0, head_limit))
            continuity = (
                (self._telemetry.log_epoch or None)
                if visible_count > 0
                else None
            )
            event = None
            if 1 <= event_id <= visible_count:
                candidate = self._events[event_id - 1]
                if candidate.id == event_id:
                    event = candidate
            return (continuity, event)

    def active_tool_call(
        self, *, include_nested: bool = True
    ) -> dict[str, Any] | None:
        """Return the most recent in-flight tool call, or ``None`` if idle.

        A tool call is *in-flight* once a ``tool_call_start`` is seen and until
        a later ``tool_call_update`` for the same ``tool_call_id`` carries a
        terminal status. This is what lets a live feed say *"still running:
        Build webapp — rush build … (17m)"* during a long, output-buffered
        tool call instead of a contentless heartbeat -- so a watcher can tell a
        busy agent from a hung one, and knows the last thing it was doing.

        Derived incrementally from the in-memory log; never persisted separately
        and never assigned an event id (so it cannot move a delivery cursor).
        """
        with self._lock:
            open_calls = list(self._open_tool_calls.values())
            if not include_nested:
                open_calls = [
                    event for event in open_calls
                    if not event.data.get("agent_id")
                ]
            if not open_calls:
                return None
            start = max(open_calls, key=lambda ev: ev.id)
        raw = start.data.get("raw_input") or {}
        command = None
        if isinstance(raw, dict):
            command = raw.get("command") or raw.get("description")
        return {
            "tool_call_id": start.data.get("tool_call_id"),
            "title": start.data.get("title")
            or start.data.get("kind")
            or "tool",
            "kind": start.data.get("kind"),
            "command": command,
            "started_at": start.timestamp,
            "started_id": start.id,
        }

    def _track_tool_event(self, event: SseEvent) -> None:
        """Update in-flight tool state while the event-log lock is held."""
        tool_call_id = event.data.get("tool_call_id") or ""
        if event.event == "tool_call_start":
            self._open_tool_calls[tool_call_id] = event
        elif event.event == "tool_call_update":
            status = event.data.get("status")
            if status and str(status).lower() in _TERMINAL_TOOL_STATUSES:
                self._open_tool_calls.pop(tool_call_id, None)

    async def wait_for_events(
        self, after: int, timeout: float = 30.0
    ) -> list[SseEvent]:
        """Wait until events with ID > ``after`` are available, or timeout."""
        loop = asyncio.get_running_loop()
        waiter = asyncio.Event()
        registration = (loop, waiter)
        with self._lock:
            events = [event for event in self._events if event.id > after]
            if events:
                return events
            self._waiters.append(registration)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
            return self.get_events(after)
        except (TimeoutError, asyncio.TimeoutError):
            return []
        finally:
            with self._lock:
                if registration in self._waiters:
                    self._waiters.remove(registration)

    async def wait_for_events_snapshot(
        self, after: int, timeout: float = 30.0
    ) -> tuple[str | None, list[SseEvent]]:
        """Wait, then snapshot one internally consistent event generation."""
        await self.wait_for_events(after, timeout=timeout)
        continuity, _head, events = self.snapshot_window(
            after=after,
            limit=2**31 - 1,
        )
        return continuity, events
