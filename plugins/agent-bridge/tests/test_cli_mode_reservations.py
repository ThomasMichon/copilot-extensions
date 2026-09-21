"""Tests for CLI-mode Session Host reservations
(agent-bridge-cli-mode-sessions Phase 2):

* an explicit, operator-initiated **reservation** for a worktree's next
  CLI-mode session, created *before* the muxed CLI process starts
  (§allocate-before-launch, §opt-in-not-ambient-default);
* atomic **claim** by the first live-session registration for that worktree
  (§bind-dont-self-register, §one-host-per-cwd-lane); and
* the register route's transparent ``cli_mode`` marker on the resulting row.
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


class TestCreateReservation:
    def test_create_reserves_a_free_worktree(self, tmp_db: Database) -> None:
        now = time.time()
        reservation_id = tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert reservation_id is not None
        row = tmp_db.get_cli_mode_reservation("wt-A")
        assert row is not None
        assert row["reservation_id"] == reservation_id
        assert row["claimed_by_session_id"] is None

    def test_create_refused_while_active_reservation_holds(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        first = tmp_db.create_cli_mode_reservation("wt-A", now=now, ttl_seconds=300)
        assert first is not None
        second = tmp_db.create_cli_mode_reservation(
            "wt-A", now=now + 1, ttl_seconds=300
        )
        assert second is None
        # The original reservation is unchanged.
        assert tmp_db.get_cli_mode_reservation("wt-A")["reservation_id"] == first

    def test_create_replaces_an_expired_reservation(self, tmp_db: Database) -> None:
        now = time.time()
        first = tmp_db.create_cli_mode_reservation("wt-A", now=now, ttl_seconds=10)
        assert first is not None
        second = tmp_db.create_cli_mode_reservation(
            "wt-A", now=now + 11, ttl_seconds=300
        )
        assert second is not None
        assert second != first
        assert tmp_db.get_cli_mode_reservation("wt-A")["reservation_id"] == second

    def test_create_replaces_an_expired_claimed_reservation(
        self, tmp_db: Database
    ) -> None:
        """An expired reservation is reclaimable even once claimed -- expiry,
        not claim state, is what matters for a *new* reservation."""
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now, ttl_seconds=10)
        assert _register(tmp_db, "cli-1", "wt-A", now) == "live"
        assert tmp_db.get_live_session("cli-1")["cli_mode"] == 1
        second = tmp_db.create_cli_mode_reservation(
            "wt-A", now=now + 11, ttl_seconds=300
        )
        assert second is not None
        row = tmp_db.get_cli_mode_reservation("wt-A")
        assert row["claimed_by_session_id"] is None

    def test_get_nonexistent_reservation_is_none(self, tmp_db: Database) -> None:
        assert tmp_db.get_cli_mode_reservation("wt-nope") is None

    def test_release_removes_the_reservation(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert tmp_db.release_cli_mode_reservation("wt-A") == 1
        assert tmp_db.get_cli_mode_reservation("wt-A") is None
        # Idempotent: a second release is a harmless no-op.
        assert tmp_db.release_cli_mode_reservation("wt-A") == 0

    def test_release_by_exact_reservation_id_only(self, tmp_db: Database) -> None:
        now = time.time()
        first = tmp_db.create_cli_mode_reservation("wt-A", now=now, ttl_seconds=10)
        second = tmp_db.create_cli_mode_reservation(
            "wt-A", now=now + 11, ttl_seconds=300
        )
        # Releasing the stale first id must not remove the current reservation.
        assert tmp_db.release_cli_mode_reservation(
            "wt-A", reservation_id=first
        ) == 0
        assert tmp_db.get_cli_mode_reservation("wt-A")["reservation_id"] == second


class TestClaimOnRegistration:
    def test_registration_claims_a_pending_reservation(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert _register(tmp_db, "cli-1", "wt-A", now + 1) == "live"
        row = tmp_db.get_live_session("cli-1")
        assert row["cli_mode"] == 1
        reservation = tmp_db.get_cli_mode_reservation("wt-A")
        assert reservation["claimed_by_session_id"] == "cli-1"

    def test_registration_with_no_reservation_is_ordinary(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        assert _register(tmp_db, "cli-1", "wt-A", now) == "live"
        assert tmp_db.get_live_session("cli-1")["cli_mode"] == 0
        assert tmp_db.get_cli_mode_reservation("wt-A") is None

    def test_second_registration_does_not_reclaim(self, tmp_db: Database) -> None:
        """Only the first registration for a worktree claims the reservation;
        a second, distinct interactive session for the same worktree (e.g. a
        successor) registers normally but does not steal the claim."""
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert _register(tmp_db, "cli-1", "wt-A", now + 1) == "live"
        assert _register(tmp_db, "cli-2", "wt-A", now + 2) == "live"
        assert tmp_db.get_live_session("cli-1")["cli_mode"] == 1
        assert tmp_db.get_live_session("cli-2")["cli_mode"] == 0
        assert (
            tmp_db.get_cli_mode_reservation("wt-A")["claimed_by_session_id"]
            == "cli-1"
        )

    def test_heartbeat_reregistration_is_idempotent(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert _register(tmp_db, "cli-1", "wt-A", now + 1) == "live"
        # Heartbeat re-POST -- safe no-op on the already-claimed reservation.
        assert _register(tmp_db, "cli-1", "wt-A", now + 2) == "live"
        assert tmp_db.get_live_session("cli-1")["cli_mode"] == 1

    def test_registration_for_null_worktree_never_claims(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        assert _register(tmp_db, "cli-1", None, now) == "live"
        assert tmp_db.get_live_session("cli-1")["cli_mode"] == 0

    def test_expired_reservation_is_not_claimable(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now, ttl_seconds=10)
        assert _register(tmp_db, "cli-1", "wt-A", now + 11) == "live"
        assert tmp_db.get_live_session("cli-1")["cli_mode"] == 0


class TestClaimCliModeReservationDirect:
    def test_claim_succeeds_once(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert tmp_db.claim_cli_mode_reservation("wt-A", "cli-1", now=now) is True
        assert tmp_db.claim_cli_mode_reservation("wt-A", "cli-2", now=now) is False

    def test_claim_with_no_reservation_is_false(self, tmp_db: Database) -> None:
        now = time.time()
        assert tmp_db.claim_cli_mode_reservation("wt-nope", "cli-1", now=now) is False


class TestCliModeReservationRoutes:
    def _client(self, db: Database) -> TestClient:
        app = FastAPI()
        app.include_router(live_sessions.router)
        app.state.db = db
        return TestClient(app)

    def test_create_route_returns_reservation(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A", "ttl_seconds": 120.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["worktree_id"] == "wt-A"
        assert body["claimed_by_session_id"] is None

    def test_create_route_409_when_active(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A", "ttl_seconds": 300.0},
        )
        resp = client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A", "ttl_seconds": 300.0},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "reservation_active"

    def test_create_route_400_on_mismatched_worktree_id(
        self, tmp_db: Database
    ) -> None:
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-B", "ttl_seconds": 300.0},
        )
        assert resp.status_code == 400

    def test_get_route_404_when_absent(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        resp = client.get("/api/v1/live-sessions/cli-mode-reservations/wt-nope")
        assert resp.status_code == 404

    def test_get_route_returns_current_reservation(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A", "ttl_seconds": 300.0},
        )
        resp = client.get("/api/v1/live-sessions/cli-mode-reservations/wt-A")
        assert resp.status_code == 200
        assert resp.json()["worktree_id"] == "wt-A"

    def test_delete_route_releases_reservation(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A", "ttl_seconds": 300.0},
        )
        resp = client.delete("/api/v1/live-sessions/cli-mode-reservations/wt-A")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 1
        assert (
            client.get(
                "/api/v1/live-sessions/cli-mode-reservations/wt-A"
            ).status_code
            == 404
        )

    def test_register_route_marks_cli_mode(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A", "ttl_seconds": 300.0},
        )
        resp = client.post(
            "/api/v1/live-sessions",
            json={"session_id": "cli-1", "worktree_id": "wt-A", "role": "picker"},
        )
        assert resp.status_code == 200
        assert resp.json()["cli_mode"] is True

    def test_register_route_ordinary_session_not_cli_mode(
        self, tmp_db: Database
    ) -> None:
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions",
            json={"session_id": "cli-1", "worktree_id": "wt-A", "role": "picker"},
        )
        assert resp.status_code == 200
        assert resp.json()["cli_mode"] is False
