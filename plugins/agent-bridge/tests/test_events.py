"""Tests for the EventLog."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

import pytest

from agent_bridge.db import Database
from agent_bridge.event_tool_statistics import MAX_EXACT_INTEGER, ToolStartCounter
from agent_bridge.events import EventLog, SseEvent


class TestToolStartStatistics:
    def test_occurrences_are_not_unique_calls_concurrency_or_page_counts(self) -> None:
        log = EventLog()
        assert log.snapshot_observation_with_statistics()[-1].start_occurrences is None
        log.append("agent_message", {})
        assert log.snapshot_observation_with_statistics()[-1].start_occurrences == 0
        for data in ({}, {"history_replay": True}, {"agent_id": "child"}):
            log.append("tool_call_start", {"tool_call_id": "same", **data})
        log.append("tool_call_update", {"tool_call_id": "same", "status": "completed"})
        expected = log.snapshot_observation_with_statistics()[-1]
        assert (expected.start_occurrences, expected.observed_records) == (3, 5)
        assert (expected.floor, expected.head, expected.contiguous) == (1, 5, True)
        for _reader in range(3):
            continuity, floor, head, rows, statistics = log.snapshot_observation_with_statistics(
                before=4, limit=1,
            )
            assert continuity == log.continuity_id
            assert (floor, head, [row.id for row in rows]) == (1, 5, [3])
            assert statistics == expected
            assert log.snapshot_observation(before=4, limit=1) == (
                continuity, floor, head, rows,
            )
        log._telemetry.log_epoch = ""
        assert log.snapshot_observation_with_statistics()[-1].start_occurrences is None

    def test_hydration_tail_gaps_and_rebuild_keep_the_actual_range(self, tmp_db: Database) -> None:
        tmp_db.create_session("stats", "test", None, ".", "local", "idle", 100.0)
        for index, kind in enumerate(("tool_call_start", "agent_message", "tool_call_start"), 1):
            tmp_db.append_event("stats", index, kind, {}, 100.0 + index)
        restored = EventLog.from_db(tmp_db, "stats")
        prior = restored.continuity_id
        assert restored.snapshot_observation_with_statistics()[-1].start_occurrences == 2
        for ids, contiguous in (([11, 12], True), ([11, 13], False)):
            tail = EventLog.from_captured_events(
                [SseEvent(index, "tool_call_start", {}, 100.0) for index in ids], 15,
                db=tmp_db, session_id="tail", acp_session_id=None, worktree_id=None,
            )
            observed = tail.snapshot_observation_with_statistics(limit=1)[-1]
            assert (observed.floor, observed.head, observed.start_occurrences) == (11, ids[-1], 2)
            assert observed.observed_records == 2 and observed.contiguous is contiguous
            tail.append("agent_message", {})
            after = tail.snapshot_observation_with_statistics()[-1]
            assert after.head == 15 and after.start_occurrences == 2 and not after.contiguous
        restored.rebuild([("agent_message", {})])
        assert restored.continuity_id != prior
        fresh = restored.snapshot_observation_with_statistics()[-1]
        assert (fresh.floor, fresh.head, fresh.start_occurrences) == (1, 1, 0)
        restored.rebuild([])
        assert restored.snapshot_observation_with_statistics()[-1].start_occurrences is None
        restored.append("tool_call_start", {})
        assert restored.snapshot_observation_with_statistics()[-1].start_occurrences == 1

    @pytest.mark.parametrize("operation", ["append", "hydrate", "tail", "rebuild"])
    def test_counter_failure_never_undoes_admitted_records(
        self, tmp_db: Database, monkeypatch: pytest.MonkeyPatch, operation: str,
    ) -> None:
        tmp_db.create_session("stats", "test", None, ".", "local", "idle", 100.0)
        tmp_db.append_event("stats", 1, "tool_call_start", {}, 100.0)

        def broken(*_args: object, **_kwargs: object) -> None:
            raise MemoryError("synthetic ancillary failure")

        monkeypatch.setattr(ToolStartCounter, "observe", broken)
        if operation == "hydrate":
            log = EventLog.from_db(tmp_db, "stats")
        elif operation == "tail":
            log = EventLog.from_captured_events(
                [SseEvent(1, "tool_call_start", {}, 100.0)], 2,
                db=tmp_db, session_id="stats", acp_session_id=None, worktree_id=None,
            )
        else:
            log = EventLog(db=tmp_db, session_id="stats")
            if operation == "rebuild":
                assert log.rebuild([("tool_call_start", {})]) == 1
            else:
                assert log.append("tool_call_start", {}).id == 1
        tmp_db.flush()
        assert log.get_events()[0].event == "tool_call_start"
        assert tmp_db.get_events("stats", after=0)[0]["event_type"] == "tool_call_start"
        assert log.snapshot_observation_with_statistics()[-1].unavailable_reason == "counter-unavailable"

    @pytest.mark.parametrize("failure", ["initialization", "snapshot", "exhaustion", "native-id"])
    def test_unavailable_projection_does_not_break_source_reads(
        self, monkeypatch: pytest.MonkeyPatch, failure: str,
    ) -> None:
        def broken(*_args: object, **_kwargs: object) -> None:
            raise OverflowError("synthetic ancillary failure")

        if failure == "initialization":
            monkeypatch.setattr("agent_bridge.events.ToolStartCounter", broken)
        log = EventLog()
        log.append("agent_message", {})
        if failure == "snapshot":
            monkeypatch.setattr(ToolStartCounter, "snapshot", broken)
        elif failure == "exhaustion":
            log._tool_statistics.observed_records = MAX_EXACT_INTEGER
        elif failure == "native-id":
            log._next_id = MAX_EXACT_INTEGER + 1
        admitted = log.append("tool_call_start", {})
        assert log.snapshot_observation_with_statistics()[-1].start_occurrences is None
        assert log.get_events()[-1] is admitted
        assert log.snapshot_observation()[-1][-1] is admitted

    @pytest.mark.parametrize("mutation", ["append", "rebuild"])
    def test_statistics_and_page_share_an_atomic_source_cut(
        self, monkeypatch: pytest.MonkeyPatch, mutation: str,
    ) -> None:
        log = EventLog()
        log.append("tool_call_start", {})
        prior = log.continuity_id
        entered, attempted, release = Event(), Event(), Event()
        original = log._snapshot_observation_locked

        def paused(**kwargs: object) -> tuple:
            result = original(**kwargs)
            entered.set()
            assert release.wait(5)
            return result

        def write() -> None:
            attempted.set()
            if mutation == "append":
                log.append("tool_call_start", {})
            else:
                log.rebuild([("agent_message", {})])

        monkeypatch.setattr(log, "_snapshot_observation_locked", paused)
        with ThreadPoolExecutor(max_workers=2) as workers:
            reading = workers.submit(log.snapshot_observation_with_statistics)
            try:
                assert entered.wait(5)
                writing = workers.submit(write)
                assert attempted.wait(5)
            finally:
                release.set()
            continuity, floor, head, rows, statistics = reading.result(timeout=5)
            writing.result(timeout=5)
        assert (continuity, floor, head, rows[0].event) == (prior, 1, 1, "tool_call_start")
        assert statistics.start_occurrences == 1 and statistics.head == head
        current = log.snapshot_observation_with_statistics()
        assert current[-1].start_occurrences == (2 if mutation == "append" else 0)
        assert (current[0] == prior) is (mutation == "append")

    def test_visible_counter_is_not_a_persistence_barrier(
        self, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tmp_db.create_session("stats", "test", None, ".", "local", "idle", 100.0)
        log = EventLog(db=tmp_db, session_id="stats")
        entered, release = Event(), Event()
        original = tmp_db.append_event

        def paused(*args: object, **kwargs: object) -> None:
            entered.set()
            assert release.wait(5)
            original(*args, **kwargs)

        monkeypatch.setattr(tmp_db, "append_event", paused)
        with ThreadPoolExecutor(max_workers=1) as workers:
            writing = workers.submit(log.append, "tool_call_start", {})
            try:
                assert entered.wait(5)
                assert log.snapshot_observation_with_statistics()[-1].start_occurrences == 1
                assert tmp_db.get_max_event_id("stats") == 0
            finally:
                release.set()
            writing.result(timeout=5)
        tmp_db.flush()
        assert tmp_db.get_max_event_id("stats") == 1

    def test_read_work_is_page_bounded_and_never_recounts_history(self) -> None:
        class NoScan(list):
            def __iter__(self) -> None:
                pytest.fail("statistics reader scanned the retained log")

        log = EventLog()
        for _index in range(5000):
            log.append("tool_call_start", {})
        log._events = NoScan(log._events)
        for boundary in (None, 4000, 100):
            result = log.snapshot_observation_with_statistics(before=boundary, limit=3)
            assert len(result[3]) == 3
            assert result[-1].start_occurrences == 5000

    @pytest.mark.parametrize("event_id", [True, 0, -1, 1.0, MAX_EXACT_INTEGER + 1])
    def test_counter_rejects_nonexact_native_ids(self, event_id: object) -> None:
        with pytest.raises(ValueError):
            ToolStartCounter().observe(event_id, "tool_call_start")


class TestEventLog:
    """EventLog append, get, and wait operations."""

    def test_remote_history_cannot_start_or_finish_a_live_tool(self, event_log: EventLog):
        event_log.append("tool_call_start", {"tool_call_id": "live/tool", "title": "Live"})
        event_log.append("tool_call_start", {
            "tool_call_id": "historical/tool", "history_replay": True,
        })
        event_log.append("tool_call_update", {
            "tool_call_id": "live/tool", "status": "completed", "history_replay": True,
        })
        assert event_log.active_tool_call()["tool_call_id"] == "live/tool"
        events = event_log.get_events()
        event_log.rebuild([(event.event, event.data) for event in events])
        assert event_log.active_tool_call()["tool_call_id"] == "live/tool"
        assert len(event_log.get_events()) == 3

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

    @pytest.mark.asyncio
    async def test_a_merge_finished_before_registration_does_not_strand_the_reader(
        self, event_log: EventLog
    ) -> None:
        """The reader chose this log, then it was merged away (``merged_into`` set,
        its waiters woken) before the reader registered: that wake missed it, and
        later events land on the surviving log. It returns at once to follow the
        merge rather than sleep until its timeout."""
        survivor = EventLog()

        class MergeDuringRegistration(list):
            def append(self, item) -> None:
                super().append(item)
                if len(self) == 1:  # merged right as the reader registers
                    event_log.merged_into = (survivor, {0: 0})

        event_log._waiters = MergeDuringRegistration()
        started = time.monotonic()
        events = await event_log.wait_for_events(after=0, timeout=10.0)
        assert events == [] and time.monotonic() - started < 2.0
        assert list(event_log._waiters) == []  # its registration is cleaned up


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
