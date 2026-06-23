"""Tests for the EventLog."""

from __future__ import annotations

import asyncio
import time

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog


class TestEventLog:
    """EventLog append, get, and wait operations."""

    def test_append_returns_event_with_id(self, event_log: EventLog) -> None:
        evt = event_log.append("test_event", {"key": "val"})
        assert evt.id == 1
        assert evt.event == "test_event"
        assert evt.data == {"key": "val"}

    def test_sequential_ids(self, event_log: EventLog) -> None:
        e1 = event_log.append("a", {})
        e2 = event_log.append("b", {})
        e3 = event_log.append("c", {})
        assert e1.id == 1
        assert e2.id == 2
        assert e3.id == 3

    def test_get_events_all(self, event_log: EventLog) -> None:
        event_log.append("a", {})
        event_log.append("b", {})
        events = event_log.get_events()
        assert len(events) == 2

    def test_get_events_after(self, event_log: EventLog) -> None:
        event_log.append("a", {})
        event_log.append("b", {})
        event_log.append("c", {})
        events = event_log.get_events(after=1)
        assert len(events) == 2
        assert events[0].event == "b"

    def test_latest_id(self, event_log: EventLog) -> None:
        assert event_log.latest_id == 0
        event_log.append("a", {})
        assert event_log.latest_id == 1
        event_log.append("b", {})
        assert event_log.latest_id == 2

    def test_rebuild_replaces_log(self, event_log: EventLog) -> None:
        event_log.append("old1", {"n": 1})
        event_log.append("old2", {"n": 2})

        count = event_log.rebuild([
            ("a", {"x": 1}),
            ("b", {"x": 2}),
            ("c", {"x": 3}),
        ])

        assert count == 3
        events = event_log.get_events()
        assert [e.event for e in events] == ["a", "b", "c"]
        # IDs restart from 1 after a rebuild.
        assert [e.id for e in events] == [1, 2, 3]
        assert event_log.latest_id == 3
        # A subsequent append continues from the rebuilt sequence.
        nxt = event_log.append("d", {})
        assert nxt.id == 4

    def test_rebuild_persists_to_db(self, event_log: EventLog, tmp_db: Database) -> None:
        event_log.append("stale", {})
        event_log.rebuild([("only", {"k": "v"})])
        rows = tmp_db.get_events("test-session", after=0)
        assert [r["event_type"] for r in rows] == ["only"]
        assert rows[0]["event_id"] == 1

    def test_burst_append_flush_persists_all_in_order(
        self, event_log: EventLog, tmp_db: Database
    ) -> None:
        for i in range(80):
            event_log.append("agent_message", {"i": i})

        tmp_db.flush()

        rows = tmp_db.get_events("test-session", after=0)
        assert [r["event_id"] for r in rows] == list(range(1, 81))
        assert [r["data"]["i"] for r in rows] == list(range(80))

        range_rows = tmp_db.get_events_range("test-session", 25, 30)
        assert [r["event_id"] for r in range_rows] == list(range(25, 31))
        assert [r["data"]["i"] for r in range_rows] == list(range(24, 30))

    def test_rebuild_empty_clears_log(self, event_log: EventLog) -> None:
        event_log.append("x", {})
        count = event_log.rebuild([])
        assert count == 0
        assert event_log.get_events() == []
        assert event_log.latest_id == 0

    @pytest.mark.asyncio
    async def test_wait_for_events_immediate(self, event_log: EventLog) -> None:
        event_log.append("a", {"x": 1})
        events = await event_log.wait_for_events(after=0, timeout=1.0)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_wait_for_events_blocking(self, event_log: EventLog) -> None:
        """Wait blocks until an event is appended."""
        async def delayed_append():
            await asyncio.sleep(0.1)
            event_log.append("delayed", {"x": 1})

        asyncio.create_task(delayed_append())
        events = await event_log.wait_for_events(after=0, timeout=2.0)
        assert len(events) == 1
        assert events[0].event == "delayed"

    @pytest.mark.asyncio
    async def test_wait_for_events_timeout(self, event_log: EventLog) -> None:
        events = await event_log.wait_for_events(after=0, timeout=0.1)
        assert events == []


class TestActiveToolCall:
    """Deriving the in-flight tool call for liveness markers."""

    def test_none_when_no_tool_calls(self, event_log: EventLog) -> None:
        event_log.append("agent_message", {"text": "hi"})
        assert event_log.active_tool_call() is None

    def test_open_tool_call_is_active(self, event_log: EventLog) -> None:
        event_log.append(
            "tool_call_start",
            {
                "tool_call_id": "t1",
                "title": "Build webapp",
                "kind": "execute",
                "raw_input": {"command": "rush build -t @scope/webapp"},
            },
        )
        active = event_log.active_tool_call()
        assert active is not None
        assert active["tool_call_id"] == "t1"
        assert active["title"] == "Build webapp"
        assert active["command"] == "rush build -t @scope/webapp"
        assert "started_at" in active

    def test_completed_tool_call_clears_active(self, event_log: EventLog) -> None:
        event_log.append("tool_call_start", {"tool_call_id": "t1", "title": "Read"})
        event_log.append(
            "tool_call_update", {"tool_call_id": "t1", "status": "completed"}
        )
        assert event_log.active_tool_call() is None

    def test_non_terminal_update_keeps_active(self, event_log: EventLog) -> None:
        event_log.append("tool_call_start", {"tool_call_id": "t1", "title": "Read"})
        event_log.append("tool_call_update", {"tool_call_id": "t1", "status": None})
        active = event_log.active_tool_call()
        assert active is not None
        assert active["tool_call_id"] == "t1"

    def test_most_recent_open_call_wins(self, event_log: EventLog) -> None:
        event_log.append("tool_call_start", {"tool_call_id": "t1", "title": "First"})
        event_log.append(
            "tool_call_update", {"tool_call_id": "t1", "status": "completed"}
        )
        event_log.append("tool_call_start", {"tool_call_id": "t2", "title": "Second"})
        active = event_log.active_tool_call()
        assert active is not None
        assert active["title"] == "Second"

    def test_falls_back_to_kind_for_title(self, event_log: EventLog) -> None:
        event_log.append("tool_call_start", {"tool_call_id": "t1", "kind": "execute"})
        active = event_log.active_tool_call()
        assert active is not None
        assert active["title"] == "execute"

    def test_description_used_when_no_command(self, event_log: EventLog) -> None:
        event_log.append(
            "tool_call_start",
            {"tool_call_id": "t1", "title": "X", "raw_input": {"description": "do X"}},
        )
        active = event_log.active_tool_call()
        assert active is not None
        assert active["command"] == "do X"


class TestEventLogFromDB:
    """EventLog restoration from database."""

    def test_from_db_restores_events(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)

        # Write events via DB directly
        tmp_db.append_event("s1", 1, "agent_message", {"text": "hello"}, now)
        tmp_db.append_event("s1", 2, "tool_call_start", {"id": "tc1"}, now + 1)

        # Restore from DB
        log = EventLog.from_db(tmp_db, "s1")
        events = log.get_events()
        assert len(events) == 2
        assert events[0].event == "agent_message"
        assert events[1].id == 2

    def test_from_db_flushes_queued_burst(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        source = EventLog(db=tmp_db, session_id="s1")
        for i in range(75):
            source.append("agent_message", {"i": i})

        restored = EventLog.from_db(tmp_db, "s1")

        events = restored.get_events()
        assert [e.id for e in events] == list(range(1, 76))
        assert [e.data["i"] for e in events] == list(range(75))
        assert restored.append("tail", {}).id == 76

    def test_from_db_next_id_continues(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.append_event("s1", 1, "a", {}, now)
        tmp_db.append_event("s1", 5, "b", {}, now)

        log = EventLog.from_db(tmp_db, "s1")
        new_evt = log.append("c", {})
        assert new_evt.id == 6
