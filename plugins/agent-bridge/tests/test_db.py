"""Tests for the SQLite database layer."""

from __future__ import annotations

import time


from agent_bridge.db import Database


def test_connection_pragmas_for_fast_ingest(tmp_db: Database) -> None:
    """WAL + synchronous=NORMAL keeps event ingestion off the per-commit fsync
    path that backpressures the ACP read loop (dotfiles #99)."""
    conn = tmp_db._get_conn()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # 1 == NORMAL


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


class TestLiveSessionLease:
    """Heartbeat-lease freshness, reaping, and take-over invalidation
    (#2879 / #2880 / #2906)."""

    def _register(
        self, db: Database, sid: str, wt: str, now: float
    ) -> None:
        db.register_live_session(
            sid, machine="m", cwd=None, worktree_id=wt, repo=None,
            branch=None, pid=None, role=None, now=now,
        )

    def test_is_fresh_predicate(self, tmp_db: Database) -> None:
        from agent_bridge.db import LIVE_SESSION_STALE_SECONDS, live_session_is_fresh

        now = 10_000.0
        fresh = {"status": "live", "updated_at": now}
        assert live_session_is_fresh(fresh, now)
        # a heartbeat just inside the window is fresh; just outside is stale
        edge = {"status": "live", "updated_at": now - LIVE_SESSION_STALE_SECONDS + 1}
        assert live_session_is_fresh(edge, now)
        stale = {"status": "live", "updated_at": now - LIVE_SESSION_STALE_SECONDS - 1}
        assert not live_session_is_fresh(stale, now)
        # a demoted (non-live) row is never fresh, even with a recent heartbeat
        expired = {"status": "expired", "updated_at": now}
        assert not live_session_is_fresh(expired, now)

    def test_get_and_list_fresh(self, tmp_db: Database) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-fresh", "wt-a", now)
        self._register(tmp_db, "cli-stale", "wt-b", now - 1000)
        assert tmp_db.get_fresh_live_session("cli-fresh", now=now) is not None
        assert tmp_db.get_fresh_live_session("cli-stale", now=now) is None
        # list_fresh excludes the stale row (so it never blocks a resume)
        assert [r["session_id"] for r in tmp_db.list_fresh_live_sessions(now=now)] == [
            "cli-fresh"
        ]
        assert tmp_db.list_fresh_live_sessions("wt-b", now=now) == []

    def test_reap_demotes_stale_and_drops_messages(self, tmp_db: Database) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-live", "wt-a", now)
        self._register(tmp_db, "cli-dead", "wt-b", now - 1000)
        tmp_db.enqueue_live_message("cli-dead", "op", "steer", now - 1000)
        tmp_db.enqueue_live_message("cli-live", "op", "steer", now)

        reaped = tmp_db.reap_stale_live_sessions(now=now)
        assert reaped == 1
        assert tmp_db.get_live_session("cli-dead")["status"] == "expired"
        assert tmp_db.get_live_session("cli-live")["status"] == "live"
        # the dead session's undelivered message is dropped; the live one's kept
        assert tmp_db.list_pending_live_messages("cli-dead") == []
        assert len(tmp_db.list_pending_live_messages("cli-live")) == 1
        # idempotent: a second sweep finds nothing left to demote
        assert tmp_db.reap_stale_live_sessions(now=now) == 0

    def test_reregister_revives_expired(self, tmp_db: Database) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-x", "wt-a", now - 1000)
        assert tmp_db.reap_stale_live_sessions(now=now) == 1
        assert tmp_db.get_live_session("cli-x")["status"] == "expired"
        # the CLI comes back and heartbeats -> live again
        self._register(tmp_db, "cli-x", "wt-a", now)
        assert tmp_db.get_live_session("cli-x")["status"] == "live"
        assert tmp_db.get_fresh_live_session("cli-x", now=now) is not None

    def test_expire_for_worktree_invalidates_and_clears_queue(
        self, tmp_db: Database
    ) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-a", "wt-shared", now)
        self._register(tmp_db, "cli-b", "wt-other", now)
        tmp_db.enqueue_live_message("cli-a", "op", "steer", now)

        n = tmp_db.expire_live_sessions_for_worktree("wt-shared", now=now)
        assert n == 1
        # Take-over demotes to the terminal `taken-over` state (#2912), not the
        # reaper's revivable `expired`.
        assert tmp_db.get_live_session("cli-a")["status"] == "taken-over"
        assert tmp_db.get_live_session("cli-b")["status"] == "live"
        # queued steer against the taken-over session is dropped
        assert tmp_db.list_pending_live_messages("cli-a") == []
        # no live row for the worktree -> resume guard won't block a reclaim
        assert tmp_db.list_fresh_live_sessions("wt-shared", now=now) == []

    def test_deregister_removes_session_and_messages(self, tmp_db: Database) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-a", "wt-a", now)
        tmp_db.enqueue_live_message("cli-a", "op", "steer", now)
        tmp_db.deregister_live_session("cli-a")
        assert tmp_db.get_live_session("cli-a") is None
        assert tmp_db.list_pending_live_messages("cli-a") == []


class TestLiveMessageAtomicEnqueue:
    """Atomic lease-checked enqueue + current-incarnation resolution
    (#2906, hardening the write path against TOCTOU)."""

    def _register(self, db, sid, wt, now, registered=None):
        db.register_live_session(
            sid, machine=None, cwd=None, worktree_id=wt, repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        # register uses `now` for both registered_at and updated_at; allow a
        # test to backdate registered_at to model an older incarnation.
        if registered is not None:
            db.execute_write(
                "UPDATE live_sessions SET registered_at=? WHERE session_id=?",
                (registered, sid),
            )

    def test_current_ignores_takeover_expired_even_if_updated_recently(
        self, tmp_db
    ) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-1", "wt-a", now)
        # take-over demotes cli-1 to expired AND bumps updated_at=now
        tmp_db.expire_live_sessions_for_worktree("wt-a", now=now)
        # no *live* incarnation remains, despite the fresh updated_at
        assert tmp_db.current_live_session_for_worktree("wt-a", now=now) is None

    def test_current_orders_by_registered_at_not_heartbeat(self, tmp_db) -> None:
        now = 10_000.0
        # cli-old started earlier; cli-new is the true successor incarnation
        self._register(tmp_db, "cli-old", "wt-a", now, registered=now - 100)
        self._register(tmp_db, "cli-new", "wt-a", now, registered=now - 1)
        # even if the old incarnation heartbeats *latest*, the newer-registered
        # incarnation stays current (immune to heartbeat timing)
        tmp_db.update_live_turn_state(
            "cli-old", turn_state="running", last_activity_at=now + 5
        )
        assert tmp_db.current_live_session_for_worktree("wt-a", now=now + 5) == (
            "cli-new"
        )

    def test_atomic_enqueue_success(self, tmp_db) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-1", "wt-a", now)
        mid, reason = tmp_db.enqueue_live_message_if_fresh(
            "cli-1", sender="op", body="steer", now=now,
        )
        assert reason is None and mid > 0
        assert len(tmp_db.list_pending_live_messages("cli-1")) == 1

    def test_atomic_enqueue_rejects_not_found(self, tmp_db) -> None:
        mid, reason = tmp_db.enqueue_live_message_if_fresh(
            "ghost", sender="op", body="x", now=10_000.0,
        )
        assert mid is None and reason == "not_found"

    def test_atomic_enqueue_rejects_stale(self, tmp_db) -> None:
        from agent_bridge.db import LIVE_SESSION_STALE_SECONDS

        now = 10_000.0
        self._register(tmp_db, "cli-1", "wt-a", now - LIVE_SESSION_STALE_SECONDS - 1)
        mid, reason = tmp_db.enqueue_live_message_if_fresh(
            "cli-1", sender="op", body="x", now=now,
        )
        assert mid is None and reason == "stale"
        assert tmp_db.list_pending_live_messages("cli-1") == []

    def test_atomic_enqueue_rejects_superseded(self, tmp_db) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-old", "wt-a", now, registered=now - 10)
        self._register(tmp_db, "cli-new", "wt-a", now, registered=now)
        mid, reason = tmp_db.enqueue_live_message_if_fresh(
            "cli-old", sender="op", body="x", now=now,
        )
        assert mid is None and reason == "superseded:cli-new"

    def test_atomic_enqueue_expected_mismatch(self, tmp_db) -> None:
        now = 10_000.0
        self._register(tmp_db, "cli-1", "wt-a", now)
        mid, reason = tmp_db.enqueue_live_message_if_fresh(
            "cli-1", sender="op", body="x", now=now,
            expected_session_id="cli-other",
        )
        assert mid is None and reason == "expected_mismatch:cli-1"

    def test_atomic_enqueue_no_worktree_checks_expected_against_self(
        self, tmp_db
    ) -> None:
        now = 10_000.0
        # a live session with no worktree still honors the expected assertion
        tmp_db.register_live_session(
            "cli-1", machine=None, cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        ok_id, ok_reason = tmp_db.enqueue_live_message_if_fresh(
            "cli-1", sender="op", body="x", now=now, expected_session_id="cli-1",
        )
        assert ok_reason is None and ok_id > 0
        _, bad = tmp_db.enqueue_live_message_if_fresh(
            "cli-1", sender="op", body="y", now=now, expected_session_id="cli-z",
        )
        assert bad == "expected_mismatch:cli-1"


def test_atomic_enqueue_is_cross_process(tmp_path) -> None:
    """The guarded INSERT...SELECT is atomic at the SQLite level, so a *second*
    Database handle on the same file (modeling a passive daemon) that expires
    the registration means the enqueue admits nothing -- no message stranded on
    an expired session (#2906)."""
    from agent_bridge.db import Database

    path = tmp_path / "shared.db"
    db_a = Database(path)
    db_b = Database(path)
    try:
        now = 10_000.0
        db_a.register_live_session(
            "cli-1", machine=None, cwd=None, worktree_id="wt-a", repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        # process B expires the registration
        db_b.expire_live_sessions_for_worktree("wt-a", now=now)
        # process A's guarded enqueue must now reject (sees B's commit)
        mid, reason = db_a.enqueue_live_message_if_fresh(
            "cli-1", sender="op", body="steer", now=now,
        )
        assert mid is None and reason == "stale"
        assert db_a.list_pending_live_messages("cli-1") == []
    finally:
        db_a.close()
        db_b.close()
