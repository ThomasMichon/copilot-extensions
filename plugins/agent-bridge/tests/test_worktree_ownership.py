"""Tests for the #2912 ownership primitives (db layer + register route):

* a per-worktree ACP-ownership **reservation** the resume verb takes before
  spawning ACP, which a live-session registration must respect; and
* a terminal **``taken-over``** registration state a heartbeat re-register
  refuses to revive.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.db import Database
from agent_bridge.routes import live_sessions


@pytest.fixture
def tmp_db(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


def _register(db: Database, sid: str, wt: str | None, now: float) -> str:
    return db.register_live_session(
        sid, machine="m", cwd="/w", worktree_id=wt, repo=None,
        branch=None, pid=1, role="picker", now=now,
    )


def _owned_session(db: Database, sid: str, status: str, now: float) -> None:
    """Create a bridge (owned ACP) session row for the reservation JOIN."""
    db.create_session(sid, "owned", "agent", "/w", "local", status, now)


# -- Primitive #2: terminal `taken-over` ------------------------------------


class TestTakenOverTerminalState:
    def test_take_over_sets_taken_over_not_expired(self, tmp_db: Database) -> None:
        now = time.time()
        assert _register(tmp_db, "cli-1", "wt-A", now) == "live"
        n = tmp_db.expire_live_sessions_for_worktree("wt-A", now=now + 1)
        assert n == 1
        row = tmp_db.get_live_session("cli-1")
        assert row is not None and row["status"] == "taken-over"

    def test_register_refuses_to_revive_taken_over(self, tmp_db: Database) -> None:
        """A killed predecessor's late heartbeat cannot resurrect its row."""
        now = time.time()
        _register(tmp_db, "cli-1", "wt-A", now)
        tmp_db.expire_live_sessions_for_worktree("wt-A", now=now + 1)
        # Late heartbeat from the killed predecessor (same session id).
        result = _register(tmp_db, "cli-1", "wt-A", now + 2)
        assert result == "taken-over"
        row = tmp_db.get_live_session("cli-1")
        assert row is not None and row["status"] == "taken-over"
        # It is not fresh, so it holds nothing.
        assert tmp_db.list_fresh_live_sessions("wt-A", now=now + 2) == []

    def test_successor_new_id_registers_after_take_over(self, tmp_db: Database) -> None:
        now = time.time()
        _register(tmp_db, "cli-1", "wt-A", now)
        tmp_db.expire_live_sessions_for_worktree("wt-A", now=now + 1)
        # A fresh interactive CLI (new session id) may hold the worktree.
        assert _register(tmp_db, "cli-2", "wt-A", now + 2) == "live"
        holders = tmp_db.list_fresh_live_sessions("wt-A", now=now + 2)
        assert [h["session_id"] for h in holders] == ["cli-2"]

    def test_reaper_expired_is_revivable(self, tmp_db: Database) -> None:
        """Lease-lapse `expired` (distinct from take-over) revives normally."""
        now = time.time()
        _register(tmp_db, "cli-1", "wt-A", now)
        # Lapse the lease (past the 120s window, within the purge grace so the
        # expired row survives to be revived); the CLI's process is gone, so
        # reap -> expired (a live process would reconcile to 'wedged', #3145).
        reaped = tmp_db.reap_stale_live_sessions(
            now=now + 200, pid_alive=lambda _p: False
        )
        assert reaped == 1
        assert tmp_db.get_live_session("cli-1")["status"] == "expired"
        # A returning CLI (same id) revives from expired.
        assert _register(tmp_db, "cli-1", "wt-A", now + 201) == "live"
        assert tmp_db.get_live_session("cli-1")["status"] == "live"


# -- Primitive #1: ownership reservation ------------------------------------


class TestOwnershipReservation:
    def test_reserve_free_worktree(self, tmp_db: Database) -> None:
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now) is True
        row = tmp_db.get_worktree_ownership("wt-A")
        assert row is not None and row["session_id"] == "acp-1"

    def test_reserve_refused_when_fresh_live_cli_holds(self, tmp_db: Database) -> None:
        now = time.time()
        _register(tmp_db, "cli-1", "wt-A", now)
        _owned_session(tmp_db, "acp-1", "running", now)
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now) is False
        assert tmp_db.get_worktree_ownership("wt-A") is None

    def test_register_refused_when_active_reservation_holds(self, tmp_db: Database) -> None:
        """The register-must-respect-reservation half: a new live CLI is refused
        while an active owned ACP session holds the worktree."""
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now) is True
        assert _register(tmp_db, "cli-1", "wt-A", now + 1) == "reserved"
        assert tmp_db.get_live_session("cli-1") is None

    def test_register_allowed_when_owner_not_active(self, tmp_db: Database) -> None:
        """A reservation whose owning session is stopped does not block."""
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now)
        # Owner stops -> reservation is no longer 'active' (derived freshness).
        tmp_db.update_session_status("acp-1", "stopped", now + 1)
        assert _register(tmp_db, "cli-1", "wt-A", now + 2) == "live"

    def test_reclaim_force_takes_over_live_holder(self, tmp_db: Database) -> None:
        now = time.time()
        _register(tmp_db, "cli-1", "wt-A", now)
        _owned_session(tmp_db, "acp-1", "running", now)
        # Non-reclaim refused; reclaim force-takes.
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now) is False
        assert (
            tmp_db.reserve_worktree_ownership(
                "wt-A", "acp-1", now=now, reclaim=True
            )
            is True
        )
        assert tmp_db.get_worktree_ownership("wt-A")["session_id"] == "acp-1"

    def test_release_frees_worktree(self, tmp_db: Database) -> None:
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now)
        assert _register(tmp_db, "cli-1", "wt-A", now + 1) == "reserved"
        assert tmp_db.release_worktree_ownership(session_id="acp-1") == 1
        assert tmp_db.get_worktree_ownership("wt-A") is None
        assert _register(tmp_db, "cli-1", "wt-A", now + 2) == "live"

    def test_reserve_same_session_idempotent(self, tmp_db: Database) -> None:
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now) is True
        assert (
            tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now + 1) is True
        )

    def test_reserve_refused_when_other_owner_active(self, tmp_db: Database) -> None:
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        _owned_session(tmp_db, "acp-2", "running", now)
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now) is True
        # A different owner cannot steal it while acp-1 is active.
        assert tmp_db.reserve_worktree_ownership("wt-A", "acp-2", now=now) is False
        assert tmp_db.get_worktree_ownership("wt-A")["session_id"] == "acp-1"

    def test_reserve_reclaimed_when_prior_owner_ended(self, tmp_db: Database) -> None:
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        _owned_session(tmp_db, "acp-2", "running", now)
        tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now)
        tmp_db.update_session_status("acp-1", "ended", now + 1)
        # Prior owner is no longer active -> a new owner may reserve.
        assert (
            tmp_db.reserve_worktree_ownership("wt-A", "acp-2", now=now + 2) is True
        )
        assert tmp_db.get_worktree_ownership("wt-A")["session_id"] == "acp-2"

    def test_null_worktree_registration_never_blocked(self, tmp_db: Database) -> None:
        now = time.time()
        assert _register(tmp_db, "cli-1", None, now) == "live"


# -- Register route surfaces the rejection as 409 ---------------------------


class TestRegisterRoute409:
    def _client(self, db: Database) -> TestClient:
        app = FastAPI()
        app.include_router(live_sessions.router)
        app.state.db = db
        return TestClient(app)

    def test_register_route_409_on_reserved(self, tmp_db: Database) -> None:
        now = time.time()
        _owned_session(tmp_db, "acp-1", "running", now)
        tmp_db.reserve_worktree_ownership("wt-A", "acp-1", now=now)
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions",
            json={"session_id": "cli-1", "worktree_id": "wt-A", "role": "picker"},
        )
        assert resp.status_code == 409
        assert "reserved" in resp.json()["detail"].get("reason", "")

    def test_register_route_409_on_taken_over(self, tmp_db: Database) -> None:
        now = time.time()
        _register(tmp_db, "cli-1", "wt-A", now)
        tmp_db.expire_live_sessions_for_worktree("wt-A", now=now + 1)
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions",
            json={"session_id": "cli-1", "worktree_id": "wt-A", "role": "picker"},
        )
        assert resp.status_code == 409

    def test_register_route_ok_when_free(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions",
            json={"session_id": "cli-1", "worktree_id": "wt-A", "role": "picker"},
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "cli-1"
