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
            machine="lambda-core",
            cwd="/home/x/wt",
            worktree_id="wt-abc",
            repo="aperture-labs",
            branch="worktree/x",
            pid=4242,
            role=None,
            now=now,
        )
        row = tmp_db.get_live_session("cli-1")
        assert row is not None
        assert row["session_id"] == "cli-1"
        assert row["machine"] == "lambda-core"
        assert row["worktree_id"] == "wt-abc"
        assert row["pid"] == 4242
        assert row["status"] == "live"
        assert row["registered_at"] == now

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

    def test_worktree_handle_picks_freshest_session(self, tmp_db: Database) -> None:
        # Two sessions in the same worktree (a handoff: old + new). The freshest
        # heartbeat wins, so a reply routes to the live successor, not the corpse.
        tmp_db.register_live_session(
            "old", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=1000.0,
        )
        tmp_db.register_live_session(
            "new", machine=None, cwd=None, worktree_id="wt", repo=None,
            branch=None, pid=None, role=None, now=2000.0,
        )
        row = tmp_db.resolve_live_session("wt", now=2001.0)
        assert row is not None and row["session_id"] == "new"

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
        json={"session_id": "cli-1", "machine": "lambda-core", "worktree_id": "wt-1"},
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
