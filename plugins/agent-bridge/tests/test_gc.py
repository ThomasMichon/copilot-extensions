"""Tests for session garbage collection (db primitives + manager orchestration)."""

from __future__ import annotations

import time
import asyncio
import json
import sqlite3
from unittest.mock import MagicMock

import pytest

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
    @pytest.mark.parametrize("state", ["preparing", "unknown"])
    def test_successor_recovery_journal_retains_original_handle(self, tmp_db, tmp_path, state):
        from agent_bridge import creation_writer_schema
        from agent_bridge.creation_worktree_writer import JOURNAL_KEY
        from agent_bridge.transport import SpawnTarget
        from agent_bridge.runtime_custody import RuntimeCustodyError

        now = time.time()
        manager = SessionManager(tmp_db, retention=RetentionConfig(vacuum=False))
        for sid in ("predecessor", "successor", "unrelated"):
            _mk(tmp_db, sid, "stopped", now - 10 * 86400)
        for sid, key in (("successor", "original-key"), ("unrelated", "other-key")):
            target = SpawnTarget(
                type="local", project="fixture", worktree_id="shared-worktree",
                cwd=str(tmp_path), venue={JOURNAL_KEY: key},
            )
            tmp_db.update_session_target(sid, target.to_json(), str(tmp_path))
        tmp_db.execute_write(
            "INSERT INTO workspace_embodiments "
            "(key_digest,facts_digest,state,session_id,creation_recoveries_json) "
            "VALUES ('original-key','original-facts','allocated','predecessor',?)",
            (json.dumps({"original": {"request": {}, "state": state}}),),
        )
        original_guard = tmp_db.execute_read(
            "SELECT sql FROM sqlite_master WHERE name='creation_recovery_session_retained'"
        )[0]["sql"]
        tmp_db.execute_write("DROP TRIGGER creation_recovery_lineage_retained")
        with tmp_db._runtime_transaction(write=True) as conn:
            creation_writer_schema.ensure_schema(conn)
            creation_writer_schema.validate_schema(conn)
        assert tmp_db.execute_read(
            "SELECT sql FROM sqlite_master WHERE name='creation_recovery_session_retained'"
        )[0]["sql"] == original_guard
        assert tmp_db.gc_eligible_session_ids(["stopped"], now) == ["unrelated"]
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            tmp_db.execute_write("DELETE FROM sessions WHERE id='successor'")
        assert manager.gc(now=now)["pruned"] == ["unrelated"]
        restarted = SessionManager(tmp_db, retention=RetentionConfig(vacuum=False))
        retained = restarted.get_session("successor")
        assert retained is not None
        assert tmp_db.retained_recovery_held("successor")
        with pytest.raises(RuntimeCustodyError, match="outcome reconciliation"):
            asyncio.run(restarted.end_session("successor", force=True))
        assert restarted.get_session("successor") is retained
        with pytest.raises(RuntimeCustodyError, match="outcome reconciliation"):
            tmp_db.delete_session("successor")

    @pytest.mark.parametrize("status", ["stopped", "ended"])
    @pytest.mark.parametrize("state", [
        "preparing", "invoking", "unknown", "complete", "not-started", "malformed", "gate-only",
    ])
    def test_retained_recovery_survives_selection_deletion_startup_and_periodic_gc(
        self, tmp_db, status, state,
    ):
        from agent_bridge.runtime_custody import RuntimeCustodyError

        manager = SessionManager(tmp_db, retention=RetentionConfig(vacuum=False))
        now = time.time()
        _mk(tmp_db, "retained", status, now - 10 * 86400)
        raw = (
            '{"original":"opaque"}' if state == "malformed" else
            None if state == "gate-only" else
            json.dumps({"original": {"request": {}, "state": state}})
        )
        tmp_db.execute_write(
            "INSERT INTO workspace_embodiments "
            "(key_digest,facts_digest,state,session_id,creation_recoveries_json) "
            "VALUES ('original-key','original-facts','allocated','retained',?)", (raw,),
        )
        if state == "gate-only":
            from agent_bridge.acquisition_store import AcquisitionStore

            AcquisitionStore(tmp_db)
            tmp_db.execute_write(
                "INSERT INTO acquisition_launch_gates VALUES "
                "('original-worktree','original-recovery','cli','retained','native','retained-recovery')"
            )
        held = state not in {"complete", "not-started"}
        assert ("retained" in tmp_db.gc_eligible_session_ids([status], now)) is not held
        if held:
            with pytest.raises(RuntimeCustodyError, match="outcome reconciliation"):
                tmp_db.delete_session("retained")
            with pytest.raises(sqlite3.IntegrityError, match="retained"):
                tmp_db.execute_write("DELETE FROM sessions WHERE id=?", ("retained",))
            assert tmp_db.get_session("retained") is not None
        result = manager.gc(now=now)
        assert ("retained" in result["pruned"]) is not held
        assert (tmp_db.get_session("retained") is not None) is held
        if held:
            restarted = SessionManager(tmp_db, retention=RetentionConfig(vacuum=False))
            assert restarted.get_session("retained") is not None
            assert "retained" not in restarted.gc(now=now)["pruned"]

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

    def test_status_summary_reports_population_bounds(self, tmp_db: Database) -> None:
        now = time.time()
        old = now - 10 * 86400
        recent = now - 60
        _mk(tmp_db, "old-stopped", "stopped", old)
        _mk(tmp_db, "recent-failed", "failed", recent)
        _mk(tmp_db, "live-idle", "idle", now)

        summary = tmp_db.gc_status_summary(["stopped", "failed", "ended"])

        assert summary == pytest.approx({
            "count": 2,
            "oldest_updated_at": old,
            "newest_updated_at": recent,
        })

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

    def test_noop_reports_eligibility_and_vacuum_metrics(self, tmp_db: Database) -> None:
        mgr = SessionManager(tmp_db, retention=RetentionConfig(vacuum=True))
        now = time.time()
        old = now - 6 * 86400
        _mk(tmp_db, "recent-stopped", "stopped", old)

        res = mgr.gc(now=now)

        assert res["terminal_count"] == 1
        assert res["eligible_count"] == 0
        assert res["oldest_terminal_age_hours"] == pytest.approx(6 * 24, rel=0.01)
        assert res["vacuumed"] is False
        assert res["free_bytes_before_vacuum"] >= 0
        assert res["vacuum_threshold_bytes"] == int(128.0 * 1024 * 1024)

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
