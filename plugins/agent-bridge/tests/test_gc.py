"""Tests for session garbage collection (db primitives + manager orchestration)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from agent_bridge.db import Database
from agent_bridge.models import RetentionConfig
from agent_bridge.session_manager import SessionManager


def _mk(db: Database, sid: str, status: str, updated_at: float) -> None:
    """Insert a session row with a specific status + updated_at."""
    db.create_session(
        session_id=sid,
        name=sid,
        agent_name="a",
        target_dir=".",
        target_type="local",
        status=status,
        now=updated_at,
    )


class TestDbGcPrimitives:
    def test_eligible_ids_filters_status_and_age(self, tmp_db: Database) -> None:
        now = time.time()
        old = now - 10 * 86400  # 10 days
        recent = now - 60  # 1 minute
        _mk(tmp_db, "old-stopped", "stopped", old)
        _mk(tmp_db, "old-failed", "failed", old)
        _mk(tmp_db, "old-idle", "idle", old)        # live status -> excluded
        _mk(tmp_db, "recent-stopped", "stopped", recent)  # too new -> excluded

        cutoff = now - 7 * 86400
        ids = tmp_db.gc_eligible_session_ids(["stopped", "failed", "ended"], cutoff)

        assert set(ids) == {"old-stopped", "old-failed"}

    def test_eligible_ids_empty_statuses(self, tmp_db: Database) -> None:
        _mk(tmp_db, "s1", "stopped", 0.0)
        assert tmp_db.gc_eligible_session_ids([], time.time()) == []

    def test_db_size_info_shape(self, tmp_db: Database) -> None:
        info = tmp_db.db_size_info()
        assert info["page_size"] > 0
        assert info["total_bytes"] == info["page_size"] * info["page_count"]
        assert info["free_bytes"] == info["page_size"] * info["freelist_count"]

    def test_vacuum_reclaims_freelist(self, tmp_db: Database) -> None:
        now = time.time()
        # Create a session with a chunk of events, then delete it to make
        # freelist pages, and confirm VACUUM zeroes the freelist.
        _mk(tmp_db, "big", "stopped", now)
        for i in range(1, 2001):
            tmp_db.append_event("big", i, "agent_message", {"text": "x" * 200}, now)
        tmp_db.delete_session("big")
        assert tmp_db.db_size_info()["freelist_count"] > 0

        tmp_db.vacuum()
        assert tmp_db.db_size_info()["freelist_count"] == 0


class TestManagerGc:
    def test_prunes_old_terminal_keeps_live_and_recent(self, tmp_db: Database) -> None:
        mgr = SessionManager(
            tmp_db, retention=RetentionConfig(max_age_hours=168.0, vacuum=False)
        )
        now = time.time()
        old = now - 10 * 86400
        recent = now - 60
        _mk(tmp_db, "old-stopped", "stopped", old)
        _mk(tmp_db, "old-ended", "ended", old)
        _mk(tmp_db, "old-idle", "idle", old)
        _mk(tmp_db, "recent-failed", "failed", recent)

        res = mgr.gc(now=now)

        assert set(res["pruned"]) == {"old-stopped", "old-ended"}
        assert res["pruned_count"] == 2
        assert tmp_db.get_session("old-stopped") is None
        assert tmp_db.get_session("old-ended") is None
        assert tmp_db.get_session("old-idle") is not None       # live status
        assert tmp_db.get_session("recent-failed") is not None  # too new

    def test_skips_session_with_running_client(self, tmp_db: Database) -> None:
        mgr = SessionManager(tmp_db, retention=RetentionConfig(vacuum=False))
        now = time.time()
        _mk(tmp_db, "busy", "stopped", now - 10 * 86400)
        fake = MagicMock()
        fake.client.is_running = True
        mgr._sessions["busy"] = fake

        res = mgr.gc(now=now)

        assert "busy" not in res["pruned"]
        assert tmp_db.get_session("busy") is not None

    def test_disabled_is_noop(self, tmp_db: Database) -> None:
        mgr = SessionManager(tmp_db, retention=RetentionConfig(enabled=False))
        _mk(tmp_db, "old-stopped", "stopped", time.time() - 99 * 86400)

        res = mgr.gc()

        assert res["enabled"] is False
        assert res["pruned_count"] == 0
        assert tmp_db.get_session("old-stopped") is not None

    def test_vacuum_triggered_when_threshold_met(self, tmp_db: Database) -> None:
        now = time.time()
        # Construct first (startup GC is a no-op on the empty DB), then seed an
        # old terminal session with freelist-inducing events so the manual gc()
        # call is what prunes + vacuums.
        mgr = SessionManager(
            tmp_db,
            retention=RetentionConfig(vacuum=True, vacuum_min_free_mb=0.0),
        )
        _mk(tmp_db, "old", "stopped", now - 10 * 86400)
        for i in range(1, 3001):
            tmp_db.append_event("old", i, "agent_message", {"text": "x" * 300}, now)

        res = mgr.gc(now=now)

        assert "old" in res["pruned"]
        assert res["vacuumed"] is True
        assert tmp_db.db_size_info()["freelist_count"] == 0
