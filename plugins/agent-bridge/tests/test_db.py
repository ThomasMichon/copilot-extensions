"""Tests for the SQLite database layer."""

from __future__ import annotations

import time

import pytest

from agent_bridge.db import Database


class TestSessionCRUD:
    """Session create/read/update/delete operations."""

    def test_create_and_get_session(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session(
            session_id="s1",
            name="test-session",
            agent_name="test-agent",
            target_dir="/tmp/work",
            target_type="local",
            status="idle",
            now=now,
        )
        row = tmp_db.get_session("s1")
        assert row is not None
        assert row["id"] == "s1"
        assert row["name"] == "test-session"
        assert row["agent_name"] == "test-agent"
        assert row["target_dir"] == "/tmp/work"
        assert row["status"] == "idle"

    def test_get_nonexistent_session(self, tmp_db: Database) -> None:
        assert tmp_db.get_session("nope") is None

    def test_list_sessions(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "alpha", None, ".", "local", "idle", now)
        tmp_db.create_session("s2", "beta", None, ".", "local", "stopped", now + 1)
        all_sessions = tmp_db.list_sessions()
        assert len(all_sessions) == 2

        idle_only = tmp_db.list_sessions(status="idle")
        assert len(idle_only) == 1
        assert idle_only[0]["id"] == "s1"

    def test_update_session_status(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "starting", now)
        tmp_db.update_session_status("s1", "idle", now + 1, pid=42)
        row = tmp_db.get_session("s1")
        assert row["status"] == "idle"
        assert row["pid"] == 42

    def test_update_status_clears_pid_when_omitted(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.update_session_status("s1", "idle", now, pid=42)
        tmp_db.update_session_status("s1", "stopped", now + 1)
        row = tmp_db.get_session("s1")
        assert row["pid"] is None

    def test_update_session_acp_id(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.update_session_acp_id("s1", "acp-xyz")
        row = tmp_db.get_session("s1")
        assert row["acp_session_id"] == "acp-xyz"

    def test_delete_session(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.create_turn("s1", 0, "hello", now)
        tmp_db.append_event("s1", 1, "test_event", {"key": "val"}, now)
        tmp_db.delete_session("s1")
        assert tmp_db.get_session("s1") is None
        assert len(tmp_db.get_turns("s1")) == 0
        assert len(tmp_db.get_events("s1")) == 0

    def test_delete_session_clears_delivery_cursor(self, tmp_db: Database) -> None:
        # Regression: a delivery_cursors row has a FK to sessions. With
        # PRAGMA foreign_keys=ON, omitting it from delete_session raised
        # "FOREIGN KEY constraint failed" -- which left ENDED sessions
        # undeletable and crashed _rehydrate's ENDED-cleanup on startup.
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.append_event("s1", 1, "agent_message", {"text": "hi"}, now)
        tmp_db.set_cursor("caller-a", "s1", 1, now)
        # Must not raise a FOREIGN KEY constraint error.
        tmp_db.delete_session("s1")
        assert tmp_db.get_session("s1") is None
        assert tmp_db.get_cursor("caller-a", "s1") == 0

    def test_delete_events_keeps_session(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.create_turn("s1", 0, "hello", now)
        tmp_db.append_event("s1", 1, "a", {}, now)
        tmp_db.append_event("s1", 2, "b", {}, now)
        tmp_db.delete_events("s1")
        assert len(tmp_db.get_events("s1")) == 0
        # Session and turns are untouched.
        assert tmp_db.get_session("s1") is not None
        assert len(tmp_db.get_turns("s1")) == 1


class TestTurnCRUD:
    """Turn create/read/update operations."""

    def test_create_and_get_turn(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.create_turn("s1", 0, "What is 2+2?", now)
        turns = tmp_db.get_turns("s1")
        assert len(turns) == 1
        assert turns[0]["prompt"] == "What is 2+2?"

    def test_get_single_turn(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.create_turn("s1", 0, "hello", now)
        turn = tmp_db.get_turn("s1", 0)
        assert turn is not None
        assert turn["prompt"] == "hello"
        assert tmp_db.get_turn("s1", 99) is None

    def test_update_turn(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.create_turn("s1", 0, "prompt", now)
        tmp_db.update_turn(
            "s1", 0,
            response_text="answer",
            thought_text="thinking",
            stop_reason="end_turn",
            tool_calls_json="[]",
            completed_at=now + 1,
        )
        turn = tmp_db.get_turn("s1", 0)
        assert turn["response_text"] == "answer"
        assert turn["thought_text"] == "thinking"
        assert turn["stop_reason"] == "end_turn"
        assert turn["completed_at"] is not None


class TestEventCRUD:
    """Event append/read operations."""

    def test_append_and_get_events(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.append_event("s1", 1, "agent_message", {"text": "hi"}, now)
        tmp_db.append_event("s1", 2, "tool_call_start", {"id": "tc1"}, now + 1)
        events = tmp_db.get_events("s1")
        assert len(events) == 2
        assert events[0]["event_type"] == "agent_message"
        assert events[0]["data"] == {"text": "hi"}

    def test_get_events_after(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        tmp_db.append_event("s1", 1, "a", {}, now)
        tmp_db.append_event("s1", 2, "b", {}, now)
        tmp_db.append_event("s1", 3, "c", {}, now)
        events = tmp_db.get_events("s1", after=1)
        assert len(events) == 2
        assert events[0]["event_type"] == "b"

    def test_get_max_event_id(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "idle", now)
        assert tmp_db.get_max_event_id("s1") == 0
        tmp_db.append_event("s1", 5, "test", {}, now)
        tmp_db.append_event("s1", 10, "test", {}, now)
        assert tmp_db.get_max_event_id("s1") == 10
