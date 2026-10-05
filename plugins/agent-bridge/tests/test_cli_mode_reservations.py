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


def _register(
    db: Database,
    sid: str,
    wt: str | None,
    now: float,
    *,
    pid: int | None = 1,
    driven_by: str | None = None,
    started: float | None = None,
    machine: str | None = "m",
) -> str:
    return db.register_live_session(
        sid, machine=machine, cwd="/w", worktree_id=wt, repo=None,
        branch=None, pid=pid, role="picker", now=now, driven_by=driven_by,
        process_started_at=started,
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

    def test_unclaimed_only_release_keeps_claimed_reservation(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        reservation_id = tmp_db.create_cli_mode_reservation("wt-A", now=now)
        assert _register(tmp_db, "cli-1", "wt-A", now + 1) == "live"

        assert tmp_db.release_cli_mode_reservation(
            "wt-A", reservation_id=reservation_id, unclaimed_only=True,
        ) == 0
        row = tmp_db.get_cli_mode_reservation("wt-A")
        assert row["reservation_id"] == reservation_id
        assert row["claimed_by_session_id"] == "cli-1"

        assert tmp_db.release_cli_mode_reservation(
            "wt-A", reservation_id=reservation_id,
        ) == 1


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
        assert _register(tmp_db, "cli-1", "wt-A", now + 1, pid=1) == "live"
        assert _register(tmp_db, "cli-2", "wt-A", now + 2, pid=2) == "live"
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

    def test_same_process_session_id_change_inherits_claim_and_aliases_old_id(
        self, tmp_db: Database
    ) -> None:
        import json as _json

        now = time.time()
        venue = {"kind": "codespace", "target": "cs-1", "mux_session_name": "wt-anchor-example"}
        tmp_db.create_cli_mode_reservation(
            "anchor-example@cs-1", now=now, venue=_json.dumps(venue),
        )
        assert _register(
            tmp_db, "placeholder", "anchor-example@cs-1", now + 1,
            pid=4242, driven_by="orchestrator",
        ) == "live"
        assert _register(
            tmp_db, "resumed", "anchor-example@cs-1", now + 2, pid=4242,
        ) == "live"

        assert tmp_db.get_cli_mode_reservation("anchor-example@cs-1")[
            "claimed_by_session_id"
        ] == "resumed"
        assert tmp_db.get_live_session_exact("placeholder") is None
        row = tmp_db.get_live_session("resumed")
        assert row["cli_mode"] == 1
        assert row["driven_by"] == "orchestrator"
        assert _json.loads(row["venue"]) == venue
        assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"
        assert tmp_db.resolve_live_session("placeholder", now=now + 3)["session_id"] == "resumed"
        tmp_db.deregister_live_session("placeholder")
        assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


    def test_idle_rollover_keeps_the_predecessors_observations(self, tmp_db: Database) -> None:
        import json as _json

        now = time.time()
        tmp_db.create_cli_mode_reservation("anchor-example@cs-1", now=now)
        _register(tmp_db, "placeholder", "anchor-example@cs-1", now + 1, pid=4242)
        tmp_db.update_live_turn_state("placeholder", turn_state="idle", last_activity_at=now + 1.5)
        progress = _json.dumps({"summary": "DONE", "ts": now + 1.6, "phase": "done"})
        tmp_db.update_live_progress("placeholder", latest_progress=progress, now=now + 1.6)
        # Idle predecessor, no later events: nothing will re-populate the successor.
        _register(tmp_db, "resumed", "anchor-example@cs-1", now + 2, pid=4242)

        row = tmp_db.get_live_session_exact("resumed")
        assert row["turn_state"] == "idle"
        assert row["last_activity_at"] == now + 1.5
        assert row["latest_progress"] == progress

    def test_rollover_keeps_the_successors_newer_observations(self, tmp_db: Database) -> None:
        import json as _json

        now = time.time()
        tmp_db.create_cli_mode_reservation("anchor-example@cs-1", now=now)
        _register(tmp_db, "placeholder", "anchor-example@cs-1", now + 1, pid=4242)
        tmp_db.update_live_turn_state("placeholder", turn_state="idle", last_activity_at=now + 1.1)
        old = _json.dumps({"summary": "old", "ts": now + 1.1})
        tmp_db.update_live_progress("placeholder", latest_progress=old, now=now + 1.1)
        # Registered before its PID resolved, so the rollover happens later.
        _register(tmp_db, "resumed", "anchor-example@cs-1", now + 1.2, pid=None)
        tmp_db.update_live_turn_state("resumed", turn_state="running", last_activity_at=now + 1.5)
        new = _json.dumps({"summary": "new", "ts": now + 1.5})
        tmp_db.update_live_progress("resumed", latest_progress=new, now=now + 1.5)
        _register(tmp_db, "resumed", "anchor-example@cs-1", now + 2, pid=4242)  # PID resolved
        assert tmp_db.get_live_session_exact("placeholder") is None
        row = tmp_db.get_live_session_exact("resumed")
        assert (row["turn_state"], row["last_activity_at"]) == ("running", now + 1.5)
        assert row["latest_progress"] == new


    def test_get_live_session_resolves_alias_and_row_in_one_read(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("anchor-example@cs-1", now=now)
        _register(tmp_db, "placeholder", "anchor-example@cs-1", now + 1, pid=4242)
        _register(tmp_db, "resumed", "anchor-example@cs-1", now + 2, pid=4242)
        reads = []
        real = tmp_db.execute_read

        def counting(sql, params=()):
            reads.append(sql)
            return real(sql, params)

        tmp_db.execute_read = counting
        # A rollover can't slip between alias resolution and the row fetch.
        assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"
        assert len(reads) == 1


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


_VENUE = {"kind": "codespace", "target": "cs-1", "mux_session_name": "wt-anchor-example"}


class TestReservationVenueInheritance:
    """A reserving venue launcher records the venue; the claiming session
    inherits it (never supplied by the registering client itself)."""

    def test_claim_inherits_reservation_venue(self, tmp_db: Database) -> None:
        import json as _json

        now = time.time()
        tmp_db.create_cli_mode_reservation(
            "anchor-example@cs-1", now=now, venue=_json.dumps(_VENUE),
        )
        assert _register(tmp_db, "cli-1", "anchor-example@cs-1", now + 1) == "live"
        row = tmp_db.get_live_session("cli-1")
        assert row["cli_mode"] == 1
        assert _json.loads(row["venue"]) == _VENUE

    def test_heartbeat_reregister_keeps_inherited_venue(self, tmp_db: Database) -> None:
        import json as _json

        now = time.time()
        tmp_db.create_cli_mode_reservation(
            "anchor-example@cs-1", now=now, venue=_json.dumps(_VENUE),
        )
        _register(tmp_db, "cli-1", "anchor-example@cs-1", now + 1)
        # The extension's 30s heartbeat re-POST carries no venue.
        assert _register(tmp_db, "cli-1", "anchor-example@cs-1", now + 31) == "live"
        assert _json.loads(tmp_db.get_live_session("cli-1")["venue"]) == _VENUE

    def test_reservation_without_venue_leaves_session_venue_null(
        self, tmp_db: Database,
    ) -> None:
        now = time.time()
        tmp_db.create_cli_mode_reservation("wt-A", now=now)
        _register(tmp_db, "cli-1", "wt-A", now + 1)
        assert tmp_db.get_live_session("cli-1")["venue"] is None

    def test_reaper_skips_host_pid_probe_for_venue_sessions(
        self, tmp_db: Database,
    ) -> None:
        """A remote session's pid is meaningless on this host: a lapsed lease
        must expire it, never mark it wedged because some unrelated local
        process happens to have that pid."""
        import json as _json

        now = time.time()
        tmp_db.create_cli_mode_reservation(
            "anchor-example@cs-1", now=now, venue=_json.dumps(_VENUE),
        )
        _register(tmp_db, "remote", "anchor-example@cs-1", now)
        _register(tmp_db, "local", "wt-local", now)
        probed: list = []

        def _alive(pid):
            probed.append(pid)
            return True

        tmp_db.reap_stale_live_sessions(
            now=now + 10_000, stale_seconds=60, purge_seconds=10**9, pid_alive=_alive,
        )
        assert tmp_db.get_live_session("remote")["status"] == "expired"
        assert tmp_db.get_live_session("local")["status"] == "wedged"
        assert len(probed) == 1  # only the local row was probed


class TestReservationVenueRoutes:
    def _client(self, db: Database) -> TestClient:
        app = FastAPI()
        app.include_router(live_sessions.router)
        app.state.db = db
        return TestClient(app)

    def test_create_route_records_and_returns_venue(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        resp = client.post(
            "/api/v1/live-sessions/cli-mode-reservations/anchor-example@cs-1",
            json={"worktree_id": "anchor-example@cs-1", "venue": _VENUE},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["venue"] == {**_VENUE, "supervisor_ref": None}

    def test_registered_session_id_change_forwards_old_handle(
        self, tmp_db: Database
    ) -> None:
        client = self._client(tmp_db)
        assert client.post(
            "/api/v1/live-sessions/cli-mode-reservations/anchor-example@cs-1",
            json={"worktree_id": "anchor-example@cs-1", "venue": _VENUE},
        ).status_code == 200
        first = client.post(
            "/api/v1/live-sessions",
            json={
                "session_id": "placeholder",
                "worktree_id": "anchor-example@cs-1",
                "machine": "host-1",
                "pid": 4242,
                "driven_by": "orchestrator",
            },
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/v1/live-sessions",
            json={
                "session_id": "resumed",
                "worktree_id": "anchor-example@cs-1",
                "machine": "host-1",
                "pid": 4242,
            },
        )
        assert second.status_code == 200, second.text
        assert second.json()["session_id"] == "resumed"
        assert second.json()["venue"] == {**_VENUE, "supervisor_ref": None}
        assert second.json()["driven_by"] == "orchestrator"

        resolved = client.get(
            "/api/v1/live-sessions/resolve", params={"handle": "placeholder"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["session_id"] == "resumed"
        sent = client.post(
            "/api/v1/live-sessions/placeholder/messages",
            json={"sender": "tester", "body": "hello"},
        )
        assert sent.status_code == 200, sent.text
        assert sent.json()["session_id"] == "resumed"
        pending = tmp_db.list_pending_live_messages("resumed")
        assert len(pending) == 1 and pending[0]["body"] == "hello"
        got = client.get(
            "/api/v1/live-sessions/cli-mode-reservations/anchor-example@cs-1"
        )
        assert got.json()["venue"] == {**_VENUE, "supervisor_ref": None}

    def test_release_route_compare_and_delete(self, tmp_db: Database) -> None:
        client = self._client(tmp_db)
        created = client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A"},
        ).json()
        miss = client.delete(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            params={"reservation_id": "not-it"},
        )
        assert miss.json() == {"removed": 0}
        hit = client.delete(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            params={"reservation_id": created["reservation_id"]},
        )
        assert hit.json() == {"removed": 1}

    def test_release_route_unclaimed_only_survives_claim_between_check_and_delete(
        self, tmp_db: Database
    ) -> None:
        client = self._client(tmp_db)
        created = client.post(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            json={"worktree_id": "wt-A"},
        ).json()
        checked = client.get("/api/v1/live-sessions/cli-mode-reservations/wt-A")
        assert checked.json()["claimed_by_session_id"] is None

        claimed = client.post(
            "/api/v1/live-sessions",
            json={"session_id": "cli-1", "worktree_id": "wt-A", "role": "picker"},
        )
        assert claimed.status_code == 200
        cleanup = client.delete(
            "/api/v1/live-sessions/cli-mode-reservations/wt-A",
            params={
                "reservation_id": created["reservation_id"],
                "unclaimed_only": "true",
            },
        )
        assert cleanup.json() == {"removed": 0}
        survived = client.get("/api/v1/live-sessions/cli-mode-reservations/wt-A")
        assert survived.status_code == 200
        assert survived.json()["claimed_by_session_id"] == "cli-1"

    def test_claimed_session_exposes_venue_in_live_session_view(
        self, tmp_db: Database,
    ) -> None:
        client = self._client(tmp_db)
        client.post(
            "/api/v1/live-sessions/cli-mode-reservations/anchor-example@cs-1",
            json={"worktree_id": "anchor-example@cs-1", "venue": _VENUE},
        )
        _register(tmp_db, "cli-1", "anchor-example@cs-1", time.time())
        view = client.get("/api/v1/live-sessions/cli-1").json()
        assert view["cli_mode"] is True
        assert view["venue"] == {**_VENUE, "supervisor_ref": None}


def test_live_sessions_deregister_verb_deletes_the_exact_session(monkeypatch, capsys):
    """`live-sessions deregister --session-id` -> DELETE of that exact id (never a handle)."""
    from agent_bridge import __main__ as m
    from agent_bridge.client import BridgeClient

    requests: list[tuple[str, str]] = []
    client = BridgeClient.__new__(BridgeClient)
    monkeypatch.setattr(
        client, "_request",
        lambda method, path, *a, **k: requests.append((method, path)) or {"ok": True},
        raising=False,
    )
    monkeypatch.setattr(m, "_get_client", lambda **kw: client)
    args = m.build_parser().parse_args(
        ["--json", "live-sessions", "deregister", "--session-id", "sid-1"]
    )
    args.func(args)
    assert requests == [("DELETE", "/api/v1/live-sessions/sid-1")]
    assert '"ok": true' in capsys.readouterr().out


# -- session-id rollover finds its predecessor without the reservation --------


def _claimed_placeholder(db: Database, now: float, *, ttl: float = 300.0, pid=4242) -> None:
    db.create_cli_mode_reservation("wt-R", now=now, ttl_seconds=ttl)
    assert _register(db, "placeholder", "wt-R", now + 1, pid=pid) == "live"


def test_rollover_after_the_launcher_released_the_reservation(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert tmp_db.release_cli_mode_reservation("wt-R") == 1
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    assert tmp_db.get_live_session("resumed")["cli_mode"] == 1
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"
    assert tmp_db.get_cli_mode_reservation("wt-R") is None


def test_rollover_after_the_reservation_expired(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now, ttl=10)
    assert _register(tmp_db, "resumed", "wt-R", now + 60, pid=4242) == "live"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


@pytest.mark.parametrize("pred_pid, succ_pid", [(None, 4242), (4242, None), (4242, 99)])
def test_rollover_needs_the_same_known_pid(tmp_db: Database, pred_pid, succ_pid) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now, pid=pred_pid)
    assert _register(tmp_db, "other", "wt-R", now + 2, pid=succ_pid) == "live"
    assert tmp_db.get_live_session("other")["cli_mode"] == 0
    assert tmp_db.get_live_session_exact("placeholder") is not None
    assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"


def test_two_unknown_machines_never_inherit(tmp_db: Database) -> None:
    """Hosts that both failed their machine lookup can share a scope, PID and
    start time; with no known machine on either side they stay independent."""
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now, ttl_seconds=300)
    assert _register(tmp_db, "placeholder", "wt-R", now + 1, pid=4242, started=now, machine=None) == "live"
    assert _register(tmp_db, "other-host", "wt-R", now + 2, pid=4242, started=now, machine=None) == "live"
    assert tmp_db.get_live_session("other-host")["cli_mode"] == 0
    assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"


def test_rollover_matches_the_machine_name_case_insensitively(tmp_db: Database) -> None:
    """A same-process resume that reports its host in another case (as the
    incarnation check already allows) still inherits the claim and alias."""
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now, ttl_seconds=300)
    assert _register(tmp_db, "placeholder", "wt-R", now + 1, pid=4242, started=now,
                     machine="HOST-1") == "live"
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242, started=now,
                     machine="host-1") == "live"
    assert tmp_db.get_live_session("resumed")["cli_mode"] == 1
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


def test_an_alias_heartbeat_from_another_incarnation_never_overwrites_the_successor(
    tmp_db: Database,
) -> None:
    """``placeholder -> resumed`` on host-a: a registration for ``placeholder``
    from host-b (same pid, no start time), or from another pid or start time on
    host-a, is a different incarnation. It is refused inside the write, after
    alias resolution, and the successor keeps its identity, claim and venue."""
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now, ttl_seconds=300)
    assert _register(tmp_db, "placeholder", "wt-R", now + 1, pid=4242, started=now,
                     machine="host-a") == "live"
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242, started=now,
                     machine="host-a") == "live"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"
    for kw in ({"pid": 4242, "started": None, "machine": "host-b"},
               {"pid": 777, "started": None, "machine": "host-a"},
               {"pid": 4242, "started": now + 30, "machine": "host-a"}):
        assert _register(tmp_db, "placeholder", "wt-R", now + 3, **kw) == "incarnation_mismatch", kw
    row = tmp_db.get_live_session("resumed")
    assert (row["machine"], row["pid"], row["cli_mode"]) == ("host-a", 4242, 1)
    # The same incarnation (or an id-only heartbeat) still refreshes it.
    assert _register(tmp_db, "placeholder", "wt-R", now + 4, pid=4242, started=now,
                     machine="HOST-A") == "live"
    assert _register(tmp_db, "placeholder", "wt-R", now + 5, pid=None, machine=None) == "live"


def test_an_alias_heartbeat_without_a_pid_keeps_the_successors_pid(tmp_db: Database) -> None:
    """A late heartbeat through the retired id that omits its pid must not clear
    the successor's known pid, or its next rollover can't inherit the claim."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    assert _register(tmp_db, "placeholder", "wt-R", now + 3, pid=None) == "live"
    assert tmp_db.get_live_session("resumed")["pid"] == 4242
    assert _register(tmp_db, "resumed-again", "wt-R", now + 4, pid=4242) == "live"
    assert tmp_db.get_live_session("resumed-again")["cli_mode"] == 1
    assert tmp_db.get_live_session("resumed")["session_id"] == "resumed-again"


def test_rollover_moves_delivered_messages_so_retries_stay_idempotent(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    mid, reason = tmp_db.enqueue_live_message_if_fresh(
        "placeholder", sender="op", body="seed", now=now + 2, idempotency_key="k1")
    assert reason is None and mid is not None
    assert tmp_db.ack_live_messages("placeholder", [mid], now + 3) == 1
    assert _register(tmp_db, "resumed", "wt-R", now + 4, pid=4242) == "live"
    again, reason = tmp_db.enqueue_live_message_if_fresh(
        tmp_db.resolve_live_session_id("placeholder"), sender="op", body="seed",
        now=now + 5, idempotency_key="k1")
    assert (again, reason) == (mid, None)


def test_an_identical_retry_racing_a_rollover_is_not_an_idempotency_conflict(
    tmp_db: Database, monkeypatch,
) -> None:
    """A retry naming the original ``expected_session_id`` while a rollover
    commits mid-check (between its handle resolutions, or right before the
    check) still names the same session: it returns the original message."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    mid, reason = tmp_db.enqueue_live_message_if_fresh(
        "placeholder", sender="op", body="seed", now=now + 2, idempotency_key="k1",
        expected_session_id="placeholder")
    assert reason is None and mid is not None
    assert _register(tmp_db, "resumed-1", "wt-R", now + 3, pid=4242) == "live"
    rolled: list[bool] = []

    def roll_once() -> None:
        if not rolled:
            rolled.append(True)
            assert _register(tmp_db, "resumed-2", "wt-R", now + 4, pid=4242) == "live"

    real_resolve, real_read, resolves = tmp_db.resolve_live_session_id, tmp_db.execute_read, []

    def resolve(sid):  # an older, read-by-read check: roll over between its reads
        out = real_resolve(sid)
        resolves.append(sid)
        if len(resolves) == 2:
            roll_once()
        return out

    def read(sql, params=()):
        if "idempotency_key = ?" in sql:
            roll_once()  # or right before the check reads anything
        return real_read(sql, params)

    monkeypatch.setattr(tmp_db, "resolve_live_session_id", resolve)
    monkeypatch.setattr(tmp_db, "execute_read", read)
    again, reason = tmp_db.enqueue_live_message_if_fresh(
        "resumed-1", sender="op", body="seed", now=now + 5, idempotency_key="k1",
        expected_session_id="placeholder")
    assert rolled and (again, reason) == (mid, None)



def test_a_rollover_after_the_purge_snapshot_keeps_its_alias(tmp_db: Database, monkeypatch) -> None:
    """The reaper listed the expired placeholder, then the same process
    re-registered as ``resumed`` before the purge ran: the new alias survives."""
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now)
    assert _register(tmp_db, "placeholder", "wt-R", now + 1, pid=4242, started=now - 600) == "live"
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='placeholder'")
    real_read = tmp_db.execute_read

    def _rollover_after_snapshot(sql, params=()):
        rows = real_read(sql, params)
        if "status IN ('expired', 'taken-over')" in sql:
            rows = [{"session_id": "placeholder"}]
            assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242,
                             started=now - 600) == "live"
        return rows

    monkeypatch.setattr(tmp_db, "execute_read", _rollover_after_snapshot)
    tmp_db.reap_stale_live_sessions(
        now=now + 3, stale_seconds=10**9, purge_seconds=0, pid_alive=lambda _p: True,
    )
    monkeypatch.undo()
    assert tmp_db.resolve_live_session_id("placeholder") == "resumed"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"

def test_a_heartbeat_that_resolved_before_a_rename_never_reverses_it(
    tmp_db: Database, monkeypatch
) -> None:
    """The placeholder's heartbeat looked itself up just before the rollover
    committed; its upsert must still land on the renamed row, not resurrect
    the retired id and fold the successor back into it."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    real, calls = tmp_db.resolve_live_session_id, []

    def _stale_first(session_id: str) -> str:
        calls.append(session_id)
        return session_id if len(calls) == 1 else real(session_id)

    monkeypatch.setattr(tmp_db, "resolve_live_session_id", _stale_first)
    assert _register(tmp_db, "placeholder", "wt-R", now + 3, pid=4242) == "live"
    monkeypatch.undo()
    assert tmp_db.get_live_session_exact("placeholder") is None
    assert tmp_db.get_live_session_exact("resumed")["updated_at"] == now + 3
    assert tmp_db.resolve_live_session_id("placeholder") == "resumed"
    assert tmp_db.resolve_live_session_id("resumed") == "resumed"

def test_a_rename_keeps_its_place_behind_a_newer_process(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)  # pid 4242, registered at now+1
    assert _register(tmp_db, "newer", "wt-R", now + 2, pid=99) == "live"
    assert _register(tmp_db, "resumed", "wt-R", now + 3, pid=4242) == "live"
    assert tmp_db.current_live_session_for_worktree("wt-R", now=now + 3) == "newer"


def test_an_older_daemons_delete_of_the_current_id_takes_its_aliases(tmp_db: Database) -> None:
    """A protocol-20 daemon deregisters with a bare row delete; the database
    drops the aliases that pointed at that row, so the retired id isn't left
    dangling and fenced off from ever registering again."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    assert tmp_db.resolve_live_session_id("placeholder") == "resumed"
    tmp_db.execute_write("DELETE FROM live_sessions WHERE session_id=?", ("resumed",))  # the old SQL path
    assert tmp_db.execute_read("SELECT * FROM live_session_aliases") == []
    assert tmp_db.resolve_live_session_id("placeholder") == "placeholder"
    assert _register(tmp_db, "placeholder", "wt-R", now + 3, pid=4242) == "live"
    assert tmp_db.get_live_session_exact("placeholder") is not None


def test_a_registration_right_after_the_purge_keeps_its_new_alias(
    tmp_db: Database, monkeypatch,
) -> None:
    """The reaper purges an expired ``resumed`` row; before its sweep ends, the
    resumed process registers that id again and folds the live placeholder into
    it. The new ``placeholder -> resumed`` alias must survive the sweep."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)  # placeholder, pid 4242, live
    assert _register(tmp_db, "resumed", "wt-X", now + 1, pid=4242) == "live"
    tmp_db.execute_write("UPDATE live_sessions SET status='expired', updated_at=0 WHERE session_id='resumed'")
    real_write = tmp_db.execute_write

    def _register_after_the_purge(sql, params=()):
        cur = real_write(sql, params)
        if sql.startswith("DELETE FROM live_sessions WHERE session_id IN"):
            assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
        return cur

    monkeypatch.setattr(tmp_db, "execute_write", _register_after_the_purge)
    tmp_db.reap_stale_live_sessions(now=now + 3, stale_seconds=10**9, purge_seconds=60,
                                    pid_alive=lambda _p: True)
    monkeypatch.undo()
    assert tmp_db.resolve_live_session_id("placeholder") == "resumed"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


def test_a_reused_pid_never_inherits_a_confirmed_dead_registration(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='placeholder'")
    assert _register(tmp_db, "stranger", "wt-R", now + 30, pid=4242) == "live"
    assert tmp_db.get_live_session_exact("placeholder") is not None
    assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"


def test_mixed_version_identity_is_never_folded_and_is_reported(
    tmp_db: Database, caplog,
) -> None:
    """A legacy registration (no start time) and a successor with one: the only
    bridge between them would compare the bridge's clock with the venue's, so
    neither a pid reuse nor an apparently-same process is folded in -- whatever
    the clocks say -- and the limitation is logged. The reverse (only the
    predecessor timed) is mixed evidence too."""
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now)
    assert _register(tmp_db, "placeholder", "wt-R", now + 1, pid=4242) == "live"
    assert _register(tmp_db, "stranger", "wt-R", now + 11, pid=4242, started=now + 10) == "live"
    assert tmp_db.get_live_session("stranger")["cli_mode"] == 0
    assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"
    with caplog.at_level("WARNING", logger="agent-bridge"):
        # The venue's clock is 120s ahead: its start time reads as after the
        # registration; behind, as before it. Neither decides anything.
        for i, started in enumerate((now + 120, now - 5)):
            assert _register(tmp_db, f"resumed-{i}", "wt-R", now + 12 + i, pid=4242, started=started) == "live"
            assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"
    assert "not folding in placeholder" in caplog.text
    assert tmp_db.get_cli_mode_reservation("wt-R")["claimed_by_session_id"] == "placeholder"
    # Only the predecessor reports a start time: identity can't be established.
    tmp_db.create_cli_mode_reservation("wt-S", now=now)
    assert _register(tmp_db, "timed", "wt-S", now + 1, pid=77, started=now - 5) == "live"
    assert _register(tmp_db, "untimed", "wt-S", now + 2, pid=77) == "live"
    assert tmp_db.get_live_session("timed")["session_id"] == "timed"


def test_a_reused_pid_with_a_later_start_time_is_a_different_process(tmp_db: Database) -> None:
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now)
    assert _register(tmp_db, "placeholder", "wt-R", now + 1, pid=4242, started=now - 600) == "live"
    assert _register(tmp_db, "stranger", "wt-R", now + 2, pid=4242, started=now + 1.5) == "live"
    # Closely spaced but different starts are different processes too.
    assert _register(tmp_db, "close", "wt-R", now + 2.5, pid=4242, started=now - 599.0) == "live"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "placeholder"
    # The same process (same start time) still rolls over, even once its lease lapsed.
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='placeholder'")
    assert _register(tmp_db, "resumed", "wt-R", now + 300, pid=4242, started=now - 600) == "live"
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


def test_a_pid_reused_within_250ms_is_still_a_different_process(tmp_db: Database) -> None:
    """A short-lived process's pid reused 100 ms later: the start times differ,
    so the newcomer neither inherits the claim nor passes the incarnation check
    -- no minimum lifetime is assumed of the original."""
    now = time.time()
    tmp_db.create_cli_mode_reservation("wt-R", now=now)
    assert _register(tmp_db, "short", "wt-R", now + 0.05, pid=4242, started=now) == "live"
    assert _register(tmp_db, "reuser", "wt-R", now + 0.2, pid=4242, started=now + 0.1) == "live"
    assert tmp_db.get_live_session("short")["session_id"] == "short"  # nothing folded
    assert tmp_db.get_live_session("reuser")["cli_mode"] == 0
    assert _register(tmp_db, "short", "wt-R", now + 0.3, pid=4242, started=now + 0.1) == (
        "incarnation_mismatch")


def test_a_rename_after_a_fresh_rejoin_claim_still_rolls_over(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert tmp_db.release_cli_mode_reservation("wt-R") == 1
    tmp_db.create_cli_mode_reservation("wt-R", now=now + 2)  # a rejoin reserves again
    assert _register(tmp_db, "resumed", "wt-R", now + 3, pid=4242) == "live"
    assert tmp_db.get_cli_mode_reservation("wt-R")["claimed_by_session_id"] == "resumed"
    assert tmp_db.get_live_session_exact("placeholder") is None
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


def test_restart_fenced_on_a_renamed_holder_still_invalidates_it(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    mid, _ = tmp_db.enqueue_live_message_if_fresh("placeholder", sender="op", body="x", now=now + 2)
    assert mid is not None
    assert _register(tmp_db, "resumed", "wt-R", now + 3, pid=4242) == "live"  # rollover mid-stop
    assert tmp_db.expire_live_sessions_for_worktree(
        "wt-R", now=now + 4, expected_session_id="placeholder") == 1
    assert tmp_db.get_live_session_exact("resumed")["status"] == "taken-over"
    assert tmp_db.list_pending_live_messages("resumed") == []


def test_restart_fence_still_spares_an_unrelated_process(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    tmp_db.deregister_live_session("placeholder")
    assert _register(tmp_db, "stranger", "wt-R", now + 3, pid=99) == "live"
    assert tmp_db.expire_live_sessions_for_worktree(
        "wt-R", now=now + 4, expected_session_id="placeholder") == 0
    assert tmp_db.get_live_session_exact("stranger")["status"] == "live"


def test_a_concurrent_deregister_cannot_split_a_rollover(tmp_db: Database, monkeypatch) -> None:
    """Another connection's deregistration attempted between the successor's
    upsert and the predecessor fold-in must wait for the whole rollover: the
    successor keeps the claim and the old handle still resolves."""
    import sqlite3

    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert tmp_db.release_cli_mode_reservation("wt-R") == 1
    real, raced = tmp_db.resolve_live_session_id, []

    def _race(session_id: str) -> str:
        if session_id == "resumed" and not raced:
            other = sqlite3.connect(str(tmp_db.db_path), timeout=0)
            try:
                other.execute("DELETE FROM live_sessions WHERE session_id='placeholder'")
                other.commit()
                raced.append("deleted")
            except sqlite3.OperationalError:
                raced.append("blocked")
            finally:
                other.close()
        return real(session_id)

    monkeypatch.setattr(tmp_db, "resolve_live_session_id", _race)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    monkeypatch.undo()
    assert raced == ["blocked"]
    assert tmp_db.get_live_session("resumed")["cli_mode"] == 1
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"


def test_deregister_reports_whether_it_deleted_the_exact_registration(tmp_db: Database) -> None:
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    # The predecessor's late deregister lost the race to the rollover: it
    # deletes nothing, and the alias to the live successor survives.
    assert tmp_db.deregister_live_session("placeholder") is False
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"
    assert tmp_db.deregister_live_session("resumed") is True
    assert tmp_db.get_live_session("placeholder") is None


def test_a_metadata_free_alias_heartbeat_keeps_the_successors_targeting(tmp_db: Database) -> None:
    """A late heartbeat through the retired id carrying only the id (the
    extension's metadata still resolving) must not erase what's known."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    assert tmp_db.register_live_session(
        "placeholder", machine=None, cwd=None, worktree_id=None, repo=None, branch=None,
        pid=None, role=None, now=now + 3) == "live"
    row = tmp_db.get_live_session("resumed")
    assert (row["machine"], row["worktree_id"], row["cwd"], row["pid"], row["role"]) == (
        "m", "wt-R", "/w", 4242, "picker")
    assert _register(tmp_db, "resumed-again", "wt-R", now + 4, pid=4242) == "live"  # still rolls over
    assert tmp_db.get_live_session("resumed")["session_id"] == "resumed-again"


@pytest.mark.parametrize("heartbeat_id", ["resumed", "placeholder"])
def test_an_id_only_heartbeat_never_revives_a_registration_on_an_owned_worktree(
    tmp_db: Database, heartbeat_id: str,
) -> None:
    """An id-only heartbeat keeps the row's worktree, so a running bridge-owned
    session on that worktree refuses it, through the canonical id or an alias."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    tmp_db.create_session("acp-1", "owned", "agent", "/w", "local", "running", now + 3)
    assert tmp_db.reserve_worktree_ownership("wt-R", "acp-1", now=now + 3, reclaim=True) is True
    assert tmp_db.register_live_session(
        heartbeat_id, machine=None, cwd=None, worktree_id=None, repo=None, branch=None,
        pid=None, role=None, now=now + 4) == "reserved"
    assert tmp_db.get_live_session("resumed")["updated_at"] < now + 4

def test_an_older_daemons_old_id_heartbeat_cannot_recreate_a_rolled_over_predecessor(
    tmp_db: Database,
) -> None:
    """Mixed generations during a cutover: a protocol-20 daemon upserts the old
    id verbatim (no alias resolution). The database itself must refuse to bring
    the folded-away predecessor back, or delivery would pick it over the
    resumed session."""
    now = time.time()
    _claimed_placeholder(tmp_db, now)
    assert _register(tmp_db, "resumed", "wt-R", now + 2, pid=4242) == "live"
    conn = tmp_db._get_conn()
    with tmp_db._write_lock:
        cur = conn.execute(
            "INSERT INTO live_sessions (session_id, machine, cwd, worktree_id, pid, role, "
            "status, registered_at, updated_at) VALUES ('placeholder', 'm', '/w', 'wt-R', 4242, "
            "'picker', 'live', ?, ?) ON CONFLICT(session_id) DO UPDATE SET status='live', "
            "updated_at=excluded.updated_at",
            (now + 3, now + 3),
        )
        conn.commit()
    assert cur.rowcount == 0
    rows = conn.execute("SELECT session_id FROM live_sessions WHERE worktree_id='wt-R'").fetchall()
    assert [r["session_id"] for r in rows] == ["resumed"]
    assert tmp_db.get_live_session("placeholder")["session_id"] == "resumed"
