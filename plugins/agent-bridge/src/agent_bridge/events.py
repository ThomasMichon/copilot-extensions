"""Append-only event log with monotonic IDs and SQLite persistence.

Each session has its own EventLog. Events are appended with
auto-incrementing integer IDs. The SSE endpoint uses ``get_events(after=N)``
to enable reconnect-safe streaming -- the client sends the last seen ID
and gets only newer events.

SSE event log for agent-bridge sessions.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import TYPE_CHECKING, Any

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
    ) -> None:
        self._events: list[SseEvent] = []
        self._lock = Lock()
        self._next_id = 1
        self._waiters: list[asyncio.Event] = []
        self._db = db
        self._session_id = session_id

    @classmethod
    def from_db(cls, db: Database, session_id: str) -> EventLog:
        """Create an EventLog pre-populated with persisted events."""
        log = cls(db=db, session_id=session_id)
        rows = db.get_events(session_id, after=0)
        for row in rows:
            evt = SseEvent(
                id=row["event_id"],
                event=row["event_type"],
                data=row["data"],
                timestamp=row["timestamp"],
            )
            log._events.append(evt)

        max_id = db.get_max_event_id(session_id)
        log._next_id = max_id + 1
        return log

    def append(self, event_type: str, data: dict[str, Any]) -> SseEvent:
        """Append an event and return it with its assigned ID.

        Persists to DB before adding to the in-memory list, so SSE
        consumers never see events that failed to persist.
        """
        ts = time.time()

        with self._lock:
            event_id = self._next_id
            self._next_id += 1

        if self._db is not None and self._session_id is not None:
            self._db.append_event(
                self._session_id, event_id, event_type, data, ts,
            )

        evt = SseEvent(id=event_id, event=event_type, data=data, timestamp=ts)

        with self._lock:
            self._events.append(evt)

        for waiter in self._waiters:
            waiter.set()
        return evt

    def get_events(self, after: int = 0) -> list[SseEvent]:
        """Return events with ID > ``after``."""
        with self._lock:
            if after == 0:
                return list(self._events)
            return [e for e in self._events if e.id > after]

    def rebuild(self, events: list[tuple[str, dict[str, Any]]]) -> int:
        """Replace the entire log with ``events`` (event_type, data) pairs.

        Clears both the persisted and in-memory events for this session and
        re-appends the supplied sequence from ID 1. Used by the resync flow,
        which treats the agent's load-time replay as the authoritative
        conversation history -- healing logs truncated by a mid-session
        disconnect. Returns the number of events written.
        """
        with self._lock:
            if self._db is not None and self._session_id is not None:
                self._db.delete_events(self._session_id)
            self._events = []
            self._next_id = 1
            ts = time.time()
            for event_type, data in events:
                event_id = self._next_id
                self._next_id += 1
                if self._db is not None and self._session_id is not None:
                    self._db.append_event(
                        self._session_id, event_id, event_type, data, ts,
                    )
                self._events.append(
                    SseEvent(id=event_id, event=event_type, data=data, timestamp=ts)
                )
            count = len(self._events)
        for waiter in self._waiters:
            waiter.set()
        return count

    @property
    def latest_id(self) -> int:
        """The ID of the most recent event, or 0 if empty."""
        with self._lock:
            return self._events[-1].id if self._events else 0

    def active_tool_call(self) -> dict[str, Any] | None:
        """Return the most recent in-flight tool call, or ``None`` if idle.

        A tool call is *in-flight* once a ``tool_call_start`` is seen and until
        a later ``tool_call_update`` for the same ``tool_call_id`` carries a
        terminal status. This is what lets a live feed say *"still running:
        Build webapp — rush build … (17m)"* during a long, output-buffered
        tool call instead of a contentless heartbeat -- so a watcher can tell a
        busy agent from a hung one, and knows the last thing it was doing.

        Derived purely from the in-memory log; never persisted and never
        assigned an event id (so it cannot move a delivery cursor).
        """
        with self._lock:
            events = list(self._events)

        open_calls: dict[str, SseEvent] = {}
        for e in events:
            if e.event == "tool_call_start":
                tid = e.data.get("tool_call_id") or ""
                open_calls[tid] = e
            elif e.event == "tool_call_update":
                status = e.data.get("status")
                if status and str(status).lower() in _TERMINAL_TOOL_STATUSES:
                    open_calls.pop(e.data.get("tool_call_id") or "", None)

        if not open_calls:
            return None

        start = max(open_calls.values(), key=lambda ev: ev.id)
        raw = start.data.get("raw_input") or {}
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

    async def wait_for_events(
        self, after: int, timeout: float = 30.0
    ) -> list[SseEvent]:
        """Wait until events with ID > ``after`` are available, or timeout."""
        events = self.get_events(after)
        if events:
            return events

        waiter = asyncio.Event()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
            return self.get_events(after)
        except (TimeoutError, asyncio.TimeoutError):
            return []
        finally:
            self._waiters.remove(waiter)
