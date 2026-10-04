"""Tests for the live_sessions registry (db layer + HTTP route)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.db import SCHEMA_VERSION, Database
from agent_bridge.routes import live_sessions


# -- DB layer ---------------------------------------------------------------


class TestLiveSessionCRUD:
    def test_register_get_and_fields(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "cli-1",
            machine="anomalous-potato",
            cwd="/home/x/wt",
            worktree_id="wt-abc",
            repo="test-chamber",
            branch="worktree/x",
            pid=4242,
            role=None,
            now=now,
        )
        row = tmp_db.get_live_session("cli-1")
        assert row is not None
        assert row["session_id"] == "cli-1"
        assert row["machine"] == "anomalous-potato"
        assert row["worktree_id"] == "wt-abc"
        assert row["pid"] == 4242
        assert row["status"] == "live"
        assert row["registered_at"] == now

    def test_register_carries_driven_by(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "cli-d", machine=None, cwd=None, worktree_id="wt-d", repo=None,
            branch=None, pid=None, role=None, now=now, driven_by="orchestrator",
        )
        assert tmp_db.get_live_session("cli-d")["driven_by"] == "orchestrator"
        # default is NULL for an operator-launched session
        tmp_db.register_live_session(
            "cli-o", machine=None, cwd=None, worktree_id="wt-o", repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        assert tmp_db.get_live_session("cli-o")["driven_by"] is None

    def test_register_is_upsert_refreshing_updated_at(self, tmp_db: Database) -> None:
        tmp_db.register_live_session(
            "cli-1", machine="m", cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=1000.0,
        )
        first = tmp_db.get_live_session("cli-1")
        assert first["registered_at"] == 1000.0 and first["updated_at"] == 1000.0

        tmp_db.register_live_session(
            "cli-1", machine="m2", cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=2000.0,
        )
        second = tmp_db.get_live_session("cli-1")
        # upsert: registered_at preserved, updated_at + fields refreshed
        assert second["registered_at"] == 1000.0
        assert second["updated_at"] == 2000.0
        assert second["machine"] == "m2"

    def test_list_and_filter_by_worktree(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "a", machine=None, cwd=None, worktree_id="wt-1", repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        tmp_db.register_live_session(
            "b", machine=None, cwd=None, worktree_id="wt-2", repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        assert len(tmp_db.list_live_sessions()) == 2
        only1 = tmp_db.list_live_sessions(worktree_id="wt-1")
        assert [r["session_id"] for r in only1] == ["a"]

    def test_deregister_is_idempotent(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "a", machine=None, cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        tmp_db.deregister_live_session("a")
        assert tmp_db.get_live_session("a") is None
        # second delete: no error
        tmp_db.deregister_live_session("a")


# -- D3 addressing: resolve a handle -> current live session ----------------


class TestResolveLiveSession:
    def test_exact_session_id_wins(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "sess-1", machine=None, cwd=None, worktree_id="wt-1", repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        row = tmp_db.resolve_live_session("sess-1", now=now)
        assert row is not None and row["session_id"] == "sess-1"

    def test_worktree_handle_resolves_to_session(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "sess-1", machine=None, cwd=None, worktree_id="wt-abc", repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        row = tmp_db.resolve_live_session("wt-abc", now=now)
        assert row is not None and row["session_id"] == "sess-1"

    def test_worktree_handle_picks_latest_incarnation(self, tmp_db: Database) -> None:
        # Two sessions in the same worktree (a handoff: old + new). A late
        # heartbeat from the predecessor must not take routing back from the
        # newer incarnation.
        tmp_db.register_live_session(
            "old", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=1000.0,
        )
        tmp_db.register_live_session(
            "new", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=2000.0,
        )
        tmp_db.register_live_session(
            "old", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=2001.0,
        )
        row = tmp_db.resolve_live_session("wt", now=2002.0)
        assert row is not None and row["session_id"] == "new"

    def test_worktree_handle_excludes_non_live_incarnation(
        self, tmp_db: Database
    ) -> None:
        tmp_db.register_live_session(
            "old", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=1000.0,
        )
        tmp_db.register_live_session(
            "new", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=2000.0,
        )
        tmp_db.execute_write(
            "UPDATE live_sessions SET status='taken-over', updated_at=? "
            "WHERE session_id=?",
            (2001.0, "new"),
        )
        tmp_db.register_live_session(
            "old", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=2001.5,
        )

        row = tmp_db.resolve_live_session("wt", now=2002.0)

        assert row is not None and row["session_id"] == "old"

    def test_stale_worktree_rows_are_excluded(self, tmp_db: Database) -> None:
        # A predecessor that stopped heartbeating (>stale window) is not picked.
        tmp_db.register_live_session(
            "dead", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=1000.0,
        )
        assert tmp_db.resolve_live_session("wt", now=1000.0 + 1000.0) is None

    def test_exact_id_bypasses_staleness(self, tmp_db: Database) -> None:
        # An exact session-id delivery still resolves even if stale: the durable
        # message queue waits, so direct-id delivery keeps its dev130 behavior.
        tmp_db.register_live_session(
            "sess-1", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=1000.0,
        )
        row = tmp_db.resolve_live_session("sess-1", now=1000.0 + 9999.0)
        assert row is not None and row["session_id"] == "sess-1"

    def test_unknown_handle_returns_none(self, tmp_db: Database) -> None:
        assert tmp_db.resolve_live_session("nope", now=time.time()) is None


# -- Migration --------------------------------------------------------------


def test_migration_v5_to_v6_adds_live_sessions(tmp_path: Path) -> None:
    """Opening a pre-v6 database bumps it to v6 with a usable live_sessions table."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    # Seed only a v5 marker; _SCHEMA_SQL builds the rest of the schema on open,
    # and the v5->v6 migration (which adds live_sessions) then runs.
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (5);"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    try:
        ver = db.execute_read("SELECT version FROM schema_version")[0]["version"]
        assert ver == SCHEMA_VERSION
        db.register_live_session(
            "s", machine="m", cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=time.time(),
        )
        assert db.get_live_session("s") is not None
    finally:
        db.close()


def test_migration_v9_to_v10_adds_driven_by(tmp_path: Path) -> None:
    """A pre-v10 database bumps to v10 with a usable live_sessions.driven_by (D4)."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    # Seed a pre-v10 live_sessions table WITHOUT driven_by; the migration adds it.
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (9);"
        "CREATE TABLE live_sessions ("
        " session_id TEXT PRIMARY KEY, machine TEXT, cwd TEXT, worktree_id TEXT,"
        " repo TEXT, branch TEXT, pid INTEGER, role TEXT,"
        " status TEXT NOT NULL DEFAULT 'live', registered_at REAL NOT NULL,"
        " updated_at REAL NOT NULL);"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    try:
        ver = db.execute_read("SELECT version FROM schema_version")[0]["version"]
        assert ver == SCHEMA_VERSION
        db.register_live_session(
            "s", machine="m", cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=time.time(),
            driven_by="orchestrator",
        )
        assert db.get_live_session("s")["driven_by"] == "orchestrator"
    finally:
        db.close()


def test_migration_v23_to_v24_adds_live_session_aliases(tmp_path: Path) -> None:
    """A pre-v24 database gains the live-session alias table."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (23);"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    try:
        ver = db.execute_read("SELECT version FROM schema_version")[0]["version"]
        assert ver == SCHEMA_VERSION
        db.execute_write(
            "INSERT INTO live_session_aliases "
            "(alias_session_id, target_session_id, created_at) VALUES (?, ?, ?)",
            ("old", "new", 123.0),
        )
        rows = db.execute_read(
            "SELECT target_session_id FROM live_session_aliases WHERE alias_session_id=?",
            ("old",),
        )
        assert rows[0]["target_session_id"] == "new"
    finally:
        db.close()


def test_migration_v24_alone_installs_aliases_triggers_and_start_time(tmp_path: Path) -> None:
    """The v24 migration is self-contained: run on its own (without the
    every-init ensure path) it still installs the alias table, both triggers
    and ``live_sessions.process_started_at``."""
    db = Database(tmp_path / "b.db")
    try:
        conn = db._get_conn()
        conn.executescript(
            "DROP TRIGGER live_sessions_retired_id_fence;"
            "DROP TRIGGER live_sessions_drop_orphaned_aliases;"
            "DROP TABLE live_session_aliases;"
            "ALTER TABLE live_sessions DROP COLUMN process_started_at;"
            "UPDATE schema_version SET version = 23;"
        )
        db._migrate(conn, 23)
        triggers = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        fence, cleanup = "live_sessions_retired_id_fence", "live_sessions_drop_orphaned_aliases"
        assert {fence, cleanup} <= triggers
        columns = {r[1] for r in conn.execute("PRAGMA table_info(live_sessions)")}
        assert "process_started_at" in columns
        assert db.execute_read("SELECT version FROM schema_version")[0]["version"] == 24
    finally:
        db.close()


# -- Route layer ------------------------------------------------------------


@pytest.fixture
def client(tmp_db: Database) -> TestClient:
    app = FastAPI()
    app.state.db = tmp_db
    app.include_router(live_sessions.router)
    return TestClient(app)


def test_route_register_list_get_deregister(client: TestClient) -> None:
    r = client.post(
        "/api/v1/live-sessions",
        json={"session_id": "cli-1", "machine": "anomalous-potato", "worktree_id": "wt-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == "cli-1"
    assert body["status"] == "live"
    assert body["registered_at"] > 0

    r = client.get("/api/v1/live-sessions")
    assert r.status_code == 200
    assert [s["session_id"] for s in r.json()["live_sessions"]] == ["cli-1"]

    filtered = client.get("/api/v1/live-sessions", params={"worktree_id": "wt-9"})
    assert filtered.json()["live_sessions"] == []

    assert client.get("/api/v1/live-sessions/cli-1").json()["worktree_id"] == "wt-1"
    assert client.get("/api/v1/live-sessions/nope").status_code == 404

    assert client.delete("/api/v1/live-sessions/cli-1").json()["ok"] is True
    assert client.get("/api/v1/live-sessions/cli-1").status_code == 404
    # idempotent deregister
    assert client.delete("/api/v1/live-sessions/cli-1").status_code == 200


def test_route_register_carries_venue_for_remote_cli_mode(client: TestClient) -> None:
    """A venue-launched CLI-mode registration's reattach descriptor round-trips
    (agent-bridge-cli-mode-sessions Phase 4); an ordinary local registration
    carries none."""
    venue = {
        "kind": "codespace",
        "target": "example-web-codespaces",
        "mux_session_name": "wt-abc123",
    }
    r = client.post(
        "/api/v1/live-sessions",
        json={"session_id": "cli-remote", "worktree_id": "wt-1", "venue": venue},
    )
    assert r.status_code == 200, r.text
    assert r.json()["venue"] == {**venue, "supervisor_ref": None}

    # Read back consistently from every route that surfaces LiveSessionInfo.
    assert client.get("/api/v1/live-sessions/cli-remote").json()["venue"] == {**venue, "supervisor_ref": None}
    listed = client.get("/api/v1/live-sessions").json()["live_sessions"]
    assert next(s for s in listed if s["session_id"] == "cli-remote")["venue"] == {**venue, "supervisor_ref": None}

    r_local = client.post(
        "/api/v1/live-sessions",
        json={"session_id": "cli-local", "worktree_id": "wt-2"},
    )
    assert r_local.json()["venue"] is None


def test_route_list_hides_dead_by_default(
    client: TestClient, tmp_db: Database
) -> None:
    """GET /live-sessions hides expired/taken-over rows by default (#3144) and
    surfaces wedged ones (#3145); ?include_dead=true reveals the dead rows."""
    for sid, wt in (("live-1", "wt-a"), ("wedge-1", "wt-b"), ("dead-1", "wt-c")):
        client.post(
            "/api/v1/live-sessions",
            json={"session_id": sid, "machine": "m", "worktree_id": wt},
        )
    # Model the three post-reconcile states directly.
    tmp_db.execute_write("UPDATE live_sessions SET status='wedged' WHERE session_id='wedge-1'")
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='dead-1'")

    default_ids = {
        s["session_id"]
        for s in client.get("/api/v1/live-sessions").json()["live_sessions"]
    }
    assert default_ids == {"live-1", "wedge-1"}
    all_ids = {
        s["session_id"]
        for s in client.get(
            "/api/v1/live-sessions", params={"include_dead": "true"}
        ).json()["live_sessions"]
    }
    assert all_ids == {"live-1", "wedge-1", "dead-1"}


def test_route_resolve_by_handle(client: TestClient) -> None:
    # A worktree handle resolves to its live session; /resolve is matched before
    # the /{session_id} path param (no collision).
    client.post(
        "/api/v1/live-sessions",
        json={"session_id": "sess-1", "worktree_id": "wt-1"},
    )
    by_wt = client.get("/api/v1/live-sessions/resolve", params={"handle": "wt-1"})
    assert by_wt.status_code == 200, by_wt.text
    assert by_wt.json()["session_id"] == "sess-1"

    by_id = client.get(
        "/api/v1/live-sessions/resolve", params={"handle": "sess-1"}
    )
    assert by_id.status_code == 200 and by_id.json()["session_id"] == "sess-1"

    missing = client.get(
        "/api/v1/live-sessions/resolve", params={"handle": "nope"}
    )
    assert missing.status_code == 404


def test_route_driven_by_surfaces(client: TestClient) -> None:
    # D4: an embodied session registers who's driving; the bridge surfaces it so
    # Neuron Forge shows the "driven by <agent>" banner on takeover.
    r = client.post(
        "/api/v1/live-sessions",
        json={"session_id": "cli-d", "worktree_id": "wt-1",
              "driven_by": "orchestrator"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["driven_by"] == "orchestrator"
    got = client.get("/api/v1/live-sessions/cli-d").json()
    assert got["driven_by"] == "orchestrator"
    # operator-launched session has no driver
    client.post("/api/v1/live-sessions", json={"session_id": "cli-o"})
    assert client.get("/api/v1/live-sessions/cli-o").json()["driven_by"] is None


def test_another_process_on_an_expired_row_gets_the_refusal_the_extension_reads(
    client: TestClient, tmp_db: Database,
) -> None:
    """A crashed process's expired row: a resumed process (other pid) is
    refused with ``detail.reason == "incarnation_mismatch"``, which the
    extension reads to keep serving under the id it already registered."""
    assert client.post("/api/v1/live-sessions", json={"session_id": "resumed", "pid": 11}).status_code == 200
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id='resumed'")
    r = client.post("/api/v1/live-sessions", json={"session_id": "resumed", "pid": 22})
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "incarnation_mismatch"


def test_route_register_is_heartbeat_upsert(client: TestClient) -> None:
    first = client.post(
        "/api/v1/live-sessions", json={"session_id": "s", "machine": "a"}
    ).json()
    time.sleep(0.01)
    second = client.post(
        "/api/v1/live-sessions", json={"session_id": "s", "machine": "b"}
    ).json()
    assert second["registered_at"] == first["registered_at"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["machine"] == "b"
    assert len(client.get("/api/v1/live-sessions").json()["live_sessions"]) == 1


# -- Phase 7 Channel A: turn-state -------------------------------------------


class TestDeriveTurnState:
    def test_activity_marks_running(self) -> None:
        from agent_bridge.live_representation import derive_turn_state

        state, saw = derive_turn_state([{"type": "assistant.message"}])
        assert state == "running" and saw is True

    def test_turn_end_marks_idle(self) -> None:
        from agent_bridge.live_representation import derive_turn_state

        state, saw = derive_turn_state([{"type": "assistant.turn_end"}])
        assert state == "idle" and saw is False

    def test_last_signal_wins(self) -> None:
        from agent_bridge.live_representation import derive_turn_state

        state, _ = derive_turn_state([
            {"type": "user.message"},
            {"type": "assistant.message"},
            {"type": "assistant.turn_end"},
        ])
        assert state == "idle"

    def test_empty_or_inert_batch_keeps_prior(self) -> None:
        from agent_bridge.live_representation import derive_turn_state

        assert derive_turn_state([], prior_state="running") == ("running", False)
        assert derive_turn_state(
            [{"type": "assistant.usage"}], prior_state="idle"
        ) == ("idle", False)


def test_db_update_live_turn_state(tmp_db: Database) -> None:
    now = time.time()
    tmp_db.register_live_session(
        "cli-t", machine="m", cwd=None, worktree_id="wt-t", repo=None,
        branch=None, pid=None, role=None, now=now,
    )
    tmp_db.update_live_turn_state("cli-t", turn_state="running", last_activity_at=now + 5)
    row = tmp_db.get_live_session("cli-t")
    assert row["turn_state"] == "running"
    assert row["last_activity_at"] == now + 5


def test_db_update_live_turn_state_follows_a_rollover_alias(tmp_db: Database) -> None:
    """A rollover committed between the ack and the turn-state update still
    lands the state on the successor, not the retired predecessor id."""
    now = time.time()
    tmp_db.register_live_session(
        "cli-new", machine="m", cwd=None, worktree_id="wt-t", repo=None,
        branch=None, pid=None, role=None, now=now,
    )
    tmp_db.execute_write(
        "INSERT INTO live_session_aliases (alias_session_id, target_session_id, created_at) "
        "VALUES (?, ?, ?)", ("cli-old", "cli-new", now),
    )
    tmp_db.update_live_turn_state("cli-old", turn_state="running", last_activity_at=now + 5)
    assert tmp_db.get_live_session("cli-new")["turn_state"] == "running"


def test_fresh_db_has_turn_state_columns(tmp_db: Database) -> None:
    cols = {r["name"] for r in tmp_db.execute_read("PRAGMA table_info(live_sessions)")}
    assert {"turn_state", "last_activity_at"} <= cols
    assert SCHEMA_VERSION >= 11


def test_live_liveness_labels() -> None:
    now = 1000.0
    # running + recent -> active
    assert live_sessions._live_liveness(
        {"turn_state": "running", "last_activity_at": now - 1}, now=now
    ) == "active"
    # running + stale -> stalled
    assert live_sessions._live_liveness(
        {"turn_state": "running", "last_activity_at": now - 999}, now=now
    ) == "stalled"
    # idle -> idle
    assert live_sessions._live_liveness({"turn_state": "idle"}, now=now) == "idle"
    # no turn signal -> None
    assert live_sessions._live_liveness({}, now=now) is None


@pytest.fixture
def client_with_store(tmp_db: Database) -> TestClient:
    from agent_bridge.live_representation import LiveEventStore

    app = FastAPI()
    app.state.db = tmp_db
    app.state.live_event_store = LiveEventStore()
    app.include_router(live_sessions.router)
    return TestClient(app)


def test_a_deregister_leaves_a_replacement_registered_by_another_process(
    client_with_store: TestClient,
) -> None:
    """Between an exiting extension's cleanup DELETEs another process registers
    one of its ids: the DELETE carries the exiting process's identity, so the
    replacement's row (and its queue) is left alone; its own DELETE removes it."""
    c = client_with_store
    c.post("/api/v1/live-sessions",
           json={"session_id": "A", "pid": 2222, "process_started_at": 500.0})
    gone = c.delete("/api/v1/live-sessions/A", params={"pid": 1111, "process_started_at": 100.0})
    assert gone.status_code == 200
    assert c.get("/api/v1/live-sessions/A").status_code == 200  # the replacement survives
    same_pid_restarted = {"pid": 2222, "process_started_at": 100.0}
    c.delete("/api/v1/live-sessions/A", params=same_pid_restarted)
    assert c.get("/api/v1/live-sessions/A").status_code == 200  # a reused pid, another process
    c.delete("/api/v1/live-sessions/A", params={"pid": 2222, "process_started_at": 500.0})
    assert c.get("/api/v1/live-sessions/A").status_code == 404
    # Without identity (an older extension), a DELETE behaves as before.
    c.post("/api/v1/live-sessions", json={"session_id": "B", "pid": 3333})
    c.delete("/api/v1/live-sessions/B")
    assert c.get("/api/v1/live-sessions/B").status_code == 404


@pytest.mark.parametrize("started", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_process_start_is_refused(client_with_store: TestClient, started: str) -> None:
    """NaN never compares as different (``abs(nan - x) >= tol`` is false), so it would
    pass for any process: a registration or deregistration carrying one is refused."""
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "A", "pid": 2222, "process_started_at": 500.0})
    r = c.post("/api/v1/live-sessions", content=(
        '{"session_id": "A", "pid": 2222, "process_started_at": %s}' % started),
        headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert c.delete("/api/v1/live-sessions/A", params={"pid": 2222, "process_started_at": started.lower()}
                    ).status_code == 422
    assert c.get("/api/v1/live-sessions/A").status_code == 200  # untouched


def test_result_routes_answer_a_merge_still_copying_with_a_retryable_503(
    client_with_store: TestClient, monkeypatch,
) -> None:
    """A snapshot whose merge is still copying events at its deadline is not
    validated (a valid token would look like replaced history): both result
    routes answer a retryable 503 instead."""
    from agent_bridge.live_representation import LiveEventStore, MergePendingError

    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})

    def pending(self, session_id, *, timeout=5.0):
        raise MergePendingError(session_id)

    monkeypatch.setattr(LiveEventStore, "snapshot", pending)
    for path in ("/api/v1/live-sessions/s1/result",
                 "/api/v1/live-sessions/s1/result/detail?ref=x"):
        got = c.get(path)
        assert got.status_code == 503, path
        assert got.headers["retry-after"] == "1"
        assert "history is merging" in got.json()["detail"]


def test_route_ingest_updates_turn_state(client_with_store: TestClient) -> None:
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    # a turn begins -> running / active
    c.post("/api/v1/live-sessions/s1/events",
           json={"events": [{"type": "user.message", "data": {"content": "hi"}}]})
    got = c.get("/api/v1/live-sessions/s1").json()
    assert got["turn_state"] == "running"
    assert got["liveness"] == "active"
    # the turn ends -> idle
    c.post("/api/v1/live-sessions/s1/events",
           json={"events": [{"type": "assistant.turn_end", "data": {}}]})
    got = c.get("/api/v1/live-sessions/s1").json()
    assert got["turn_state"] == "idle"
    assert got["liveness"] == "idle"


def _say(text: str) -> dict:
    return {"events": [{"type": "assistant.message", "data": {"content": text}}]}


def test_route_ingest_folds_milestone_lines_into_progress(client_with_store: TestClient) -> None:
    """A live CLI worker's PROGRESS/DONE/BLOCKED lines become its progress beat
    (the UI's Progress column) with no extra tool call from the agent."""
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    c.post("/api/v1/live-sessions/s1/events", json=_say("Building.\nPROGRESS branch=user/a/b build=ok"))
    c.post("/api/v1/live-sessions/s1/events", json=_say("PROGRESS pr=2401031 pr-build=running"))
    lp = c.get("/api/v1/live-sessions/s1").json()["latest_progress"]
    assert lp["markers"] == {"branch": "user/a/b", "build": "ok", "pr": "2401031", "pr-build": "running"}
    assert lp["pr"] == "2401031" and lp["phase"] == "pr-build"
    assert "pr-build=running" in lp["summary"]

    c.post("/api/v1/live-sessions/s1/events", json=_say("BLOCKED: which library root?"))
    lp = c.get("/api/v1/live-sessions/s1").json()["latest_progress"]
    assert (lp["phase"], lp["blocker"]) == ("blocked", "which library root?")

    c.post("/api/v1/live-sessions/s1/events", json=_say("DONE: PR 2401031 green"))
    lp = c.get("/api/v1/live-sessions/s1").json()["latest_progress"]
    assert (lp["phase"], lp["summary"]) == ("done", "PR 2401031 green")
    assert "blocker" not in lp and lp["pr"] == "2401031"


def test_route_ingest_without_markers_keeps_the_prior_beat(client_with_store: TestClient) -> None:
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    c.post("/api/v1/live-sessions/s1/progress", json={"summary": "explicit beat"})
    c.post("/api/v1/live-sessions/s1/events", json=_say("just thinking out loud"))
    assert c.get("/api/v1/live-sessions/s1").json()["latest_progress"]["summary"] == "explicit beat"


def _deliver(text: str, *, agent_id: str | None = None) -> dict:
    data: dict = {"content": text}
    if agent_id:
        data["agentId"] = agent_id
    return {"events": [{"type": "user.message", "data": data}]}


def test_a_delivered_message_retires_a_blocker(client_with_store: TestClient) -> None:
    """BLOCKED means waiting on someone: once a message reaches the session and
    it goes on without blocking again, the beat stops saying it is blocked."""
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    c.post("/api/v1/live-sessions/s1/events", json=_say("PROGRESS pr=42 pr-build=running"))
    c.post("/api/v1/live-sessions/s1/events", json=_say("BLOCKED: builds still running"))
    # A sub-agent's prompt is not an answer to the blocker.
    c.post("/api/v1/live-sessions/s1/events", json=_deliver("audit files", agent_id="sub-1"))
    assert c.get("/api/v1/live-sessions/s1").json()["latest_progress"]["phase"] == "blocked"

    c.post("/api/v1/live-sessions/s1/events", json=_deliver("carry on with the chain fixes"))
    lp = c.get("/api/v1/live-sessions/s1").json()["latest_progress"]
    assert lp["phase"] == "resumed" and "blocker" not in lp
    assert lp["summary"] == "resumed after: builds still running"
    assert lp["pr"] == "42" and lp["markers"] == {"pr": "42", "pr-build": "running"}


def test_blocking_again_after_a_message_stays_blocked(client_with_store: TestClient) -> None:
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    c.post("/api/v1/live-sessions/s1/events", json=_say("BLOCKED: need a token"))
    batch = {"events": [
        {"type": "user.message", "data": {"content": "try again"}},
        {"type": "assistant.message", "data": {"content": "BLOCKED: token still rejected"}},
    ]}
    c.post("/api/v1/live-sessions/s1/events", json=batch)
    lp = c.get("/api/v1/live-sessions/s1").json()["latest_progress"]
    assert (lp["phase"], lp["blocker"]) == ("blocked", "token still rejected")


def test_a_message_without_a_prior_blocker_leaves_the_beat(client_with_store: TestClient) -> None:
    c = client_with_store
    c.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    c.post("/api/v1/live-sessions/s1/events", json=_say("PROGRESS build=ok"))
    c.post("/api/v1/live-sessions/s1/events", json=_deliver("status?"))
    lp = c.get("/api/v1/live-sessions/s1").json()["latest_progress"]
    assert lp["phase"] == "build" and lp["markers"] == {"build": "ok"}


# -- Phase 7 Slice 7c: operator-session progress ------------------------------


class TestBuildProgressSnapshot:
    def test_caps_summary_and_drops_empty_optionals(self) -> None:
        from agent_bridge.live_representation import (
            PROGRESS_SUMMARY_MAX,
            build_progress_snapshot,
        )

        snap = build_progress_snapshot("x" * 500, phase="impl", ts=1.0)
        assert len(snap["summary"]) <= PROGRESS_SUMMARY_MAX
        assert snap["summary"].endswith("\u2026")
        assert snap["phase"] == "impl"
        assert "blocker" not in snap and "pr" not in snap
        assert snap["ts"] == 1.0

    def test_empty_summary_becomes_dash(self) -> None:
        from agent_bridge.live_representation import build_progress_snapshot

        assert build_progress_snapshot("   ", ts=1.0)["summary"] == "-"


def test_db_update_live_progress(tmp_db: Database) -> None:
    now = time.time()
    tmp_db.register_live_session(
        "cli-p", machine="m", cwd=None, worktree_id="wt-p", repo=None,
        branch=None, pid=None, role=None, now=now,
    )
    ok = tmp_db.update_live_progress("cli-p", latest_progress='{"summary":"hi"}', now=now + 1)
    assert ok is True
    assert tmp_db.get_live_session("cli-p")["latest_progress"] == '{"summary":"hi"}'
    # unknown session -> False, no-op
    assert tmp_db.update_live_progress("nope", latest_progress="{}", now=now) is False


def test_route_record_progress(client: TestClient) -> None:
    client.post("/api/v1/live-sessions", json={"session_id": "s1", "worktree_id": "wt-1"})
    # address by worktree handle
    r = client.post(
        "/api/v1/live-sessions/wt-1/progress",
        json={"summary": "wired the endpoint", "phase": "impl", "pr": "pr/9"},
    )
    assert r.status_code == 200, r.text
    lp = r.json()["latest_progress"]
    assert lp["summary"] == "wired the endpoint" and lp["phase"] == "impl" and lp["pr"] == "pr/9"
    # persisted + surfaced on get
    got = client.get("/api/v1/live-sessions/s1").json()
    assert got["latest_progress"]["summary"] == "wired the endpoint"
    # latest-only overwrite
    client.post("/api/v1/live-sessions/s1/progress", json={"summary": "second"})
    assert client.get("/api/v1/live-sessions/s1").json()["latest_progress"]["summary"] == "second"
    # unknown handle -> 404
    assert client.post(
        "/api/v1/live-sessions/ghost/progress", json={"summary": "x"}
    ).status_code == 404
