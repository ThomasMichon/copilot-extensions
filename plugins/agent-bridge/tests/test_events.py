"""Tests for the EventLog."""

from __future__ import annotations

import asyncio
import time
from threading import Event, Thread

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
        event_log.append("tool_call_start", {"tool_call_id": "t1"})
        count = event_log.rebuild([])
        assert count == 0
        assert event_log.get_events() == []
        assert event_log.latest_id == 0
        assert event_log.active_tool_call() is None

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

    @pytest.mark.asyncio
    async def test_wait_for_events_registration_is_atomic(
        self, event_log: EventLog
    ) -> None:
        registration_started = Event()
        append_attempted = Event()

        class BlockingWaiters(list):
            def append(self, item) -> None:
                registration_started.set()
                assert append_attempted.wait(timeout=1.0)
                super().append(item)

        event_log._waiters = BlockingWaiters()

        def append_during_registration() -> None:
            assert registration_started.wait(timeout=1.0)
            append_attempted.set()
            event_log.append("raced", {"x": 1})

        thread = Thread(target=append_during_registration)
        thread.start()
        events = await event_log.wait_for_events(after=0, timeout=1.0)
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert [event.event for event in events] == ["raced"]


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

    @pytest.mark.parametrize("raw_input", ["list files", ["list", "files"], 7, True])
    def test_non_mapping_raw_input_has_no_command(
        self, event_log: EventLog, raw_input: object
    ) -> None:
        event_log.append(
            "tool_call_start",
            {"tool_call_id": "t1", "title": "X", "raw_input": raw_input},
        )
        active = event_log.active_tool_call()
        assert active is not None
        assert active["command"] is None


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

    def test_from_db_restores_active_tool(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.append_event(
            "s1",
            1,
            "tool_call_start",
            {"tool_call_id": "tc1", "title": "Build"},
            now,
        )

        log = EventLog.from_db(tmp_db, "s1")

        assert log.active_tool_call()["tool_call_id"] == "tc1"

    def test_durable_snapshot_reads_db_head_before_event_lock(
        self, tmp_db: Database, monkeypatch
    ) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        log = EventLog(db=tmp_db, session_id="s1")
        log.append("agent_message", {"text": "done"})
        original = tmp_db.get_max_event_id

        def get_max_event_id(session_id: str) -> int:
            assert log._lock.acquire(blocking=False)
            log._lock.release()
            return original(session_id)

        monkeypatch.setattr(tmp_db, "get_max_event_id", get_max_event_id)

        continuity, head, events = log.snapshot_window(
            after=None, limit=10, durable=True
        )
        detail_continuity, event = log.snapshot_event(1, durable=True)

        assert continuity == detail_continuity
        assert head == 1
        assert [item.id for item in events] == [1]
        assert event is not None and event.id == 1

    def test_first_event_after_empty_rebuild_updates_invalidation_epoch(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        log = EventLog(db=tmp_db, session_id="s1")
        log.append("agent_message", {"text": "before"})
        tmp_db.set_cursor("caller-a", "s1", 1, now)

        log.rebuild([])
        assert tmp_db.get_cursor_state(
            "caller-a", "s1"
        )["invalidation"]["current_continuity_id"] is None

        log.append("session_state_changed", {"status": "idle"})

        invalidation = tmp_db.get_cursor_state(
            "caller-a", "s1"
        )["invalidation"]
        assert invalidation["current_continuity_id"] == log.continuity_id

    def test_rebuild_invalidates_cursor_before_replacing_events(
        self, tmp_db: Database, monkeypatch
    ) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        log = EventLog(db=tmp_db, session_id="s1")
        log.append("agent_message", {"text": "before"})
        tmp_db.set_cursor("caller-a", "s1", 1, now)

        def fail_append(*_args, **_kwargs):
            raise RuntimeError("simulated crash before event replacement")

        monkeypatch.setattr(tmp_db, "append_event", fail_append)

        with pytest.raises(RuntimeError, match="simulated crash"):
            log.rebuild([("agent_message", {"text": "after"})])

        invalidation = tmp_db.get_cursor_state(
            "caller-a", "s1"
        )["invalidation"]
        assert invalidation is not None
        assert invalidation["prior_last_acked_id"] == 1
        assert tmp_db.get_max_event_id("s1") == 0

    def test_failed_rebuild_boundary_preserves_in_memory_generation(
        self, tmp_db: Database, monkeypatch
    ) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        log = EventLog(db=tmp_db, session_id="s1")
        log.append("agent_message", {"text": "before"})
        prior_continuity = log.continuity_id

        def fail_boundary(*_args, **_kwargs):
            raise RuntimeError("simulated rebuild transaction failure")

        monkeypatch.setattr(tmp_db, "begin_event_rebuild", fail_boundary)

        with pytest.raises(RuntimeError, match="transaction failure"):
            log.rebuild([("agent_message", {"text": "after"})])

        assert log.continuity_id == prior_continuity
        assert log.latest_id == 1

    @pytest.mark.asyncio
    async def test_wait_snapshot_discards_detached_prior_generation(
        self, monkeypatch
    ) -> None:
        log = EventLog()
        stale = log.append("agent_message", {"text": "stale"})

        async def rebuild_during_wait(_after, timeout=30.0):
            log.rebuild([("agent_message", {"text": "replacement"})])
            return [stale]

        monkeypatch.setattr(log, "wait_for_events", rebuild_during_wait)

        continuity, events = await log.wait_for_events_snapshot(0)

        assert continuity == log.continuity_id
        assert [event.data["text"] for event in events] == ["replacement"]

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
