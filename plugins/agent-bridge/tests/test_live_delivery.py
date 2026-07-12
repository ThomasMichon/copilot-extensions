"""Tests for Phase 2 live-message delivery (queue db layer + HTTP routes)."""

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


class TestLiveMessageQueue:
    def test_enqueue_list_ack_flow(self, tmp_db: Database) -> None:
        now = time.time()
        m1 = tmp_db.enqueue_live_message("s", "alice", "hello", now)
        m2 = tmp_db.enqueue_live_message("s", "bob", "world", now + 1)
        assert m2 > m1  # autoincrement id ordering == delivery order

        pending = tmp_db.list_pending_live_messages("s")
        assert [p["id"] for p in pending] == [m1, m2]
        assert pending[0]["sender"] == "alice"
        assert pending[0]["body"] == "hello"
        assert pending[0]["delivered_at"] is None

        acked = tmp_db.ack_live_messages("s", [m1], now=now + 2)
        assert acked == 1
        # m1 no longer pending; m2 still is
        assert [p["id"] for p in tmp_db.list_pending_live_messages("s")] == [m2]

    def test_ack_is_idempotent_and_session_scoped(self, tmp_db: Database) -> None:
        now = time.time()
        mid = tmp_db.enqueue_live_message("s", "alice", "hi", now)
        assert tmp_db.ack_live_messages("s", [mid], now=now) == 1
        # re-ack: no rows change (delivered_at IS NULL guard)
        assert tmp_db.ack_live_messages("s", [mid], now=now) == 0
        # a different session cannot ack s's message
        other = tmp_db.enqueue_live_message("s2", "x", "y", now)
        assert tmp_db.ack_live_messages("s", [other], now=now) == 0
        assert [p["id"] for p in tmp_db.list_pending_live_messages("s2")] == [other]

    def test_enqueue_carries_reply_to(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.enqueue_live_message("s", "alice", "hi", now, reply_to="alice-sess")
        tmp_db.enqueue_live_message("s", "bob", "yo", now)  # no reply_to
        pending = tmp_db.list_pending_live_messages("s")
        assert pending[0]["reply_to"] == "alice-sess"
        assert pending[1]["reply_to"] is None

    def test_ack_empty_ids_is_noop(self, tmp_db: Database) -> None:
        assert tmp_db.ack_live_messages("s", [], now=time.time()) == 0

    def test_pending_is_per_session(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.enqueue_live_message("a", "u", "1", now)
        tmp_db.enqueue_live_message("b", "u", "2", now)
        assert len(tmp_db.list_pending_live_messages("a")) == 1
        assert len(tmp_db.list_pending_live_messages("b")) == 1

    def test_deregister_session_clears_its_messages(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.register_live_session(
            "s", machine=None, cwd=None, worktree_id=None, repo=None,
            branch=None, pid=None, role=None, now=now,
        )
        tmp_db.enqueue_live_message("s", "alice", "hi", now)
        tmp_db.deregister_live_session("s")
        assert tmp_db.list_pending_live_messages("s") == []


# -- Migration --------------------------------------------------------------


def test_migration_v7_to_v8_adds_reply_to(tmp_path: Path) -> None:
    """A pre-v8 database bumps to v8 with a usable live_messages.reply_to."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version (version) VALUES (7);"
        "CREATE TABLE live_messages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
        " sender TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL,"
        " delivered_at REAL);"
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    try:
        ver = db.execute_read("SELECT version FROM schema_version")[0]["version"]
        assert ver == SCHEMA_VERSION == 8
        mid = db.enqueue_live_message(
            "s", "alice", "hi", time.time(), reply_to="alice-sess"
        )
        assert mid > 0
        pending = db.list_pending_live_messages("s")
        assert pending[0]["reply_to"] == "alice-sess"
    finally:
        db.close()


# -- Route layer ------------------------------------------------------------


@pytest.fixture
def client(tmp_db: Database) -> TestClient:
    app = FastAPI()
    app.state.db = tmp_db
    app.include_router(live_sessions.router)
    return TestClient(app)


def _register(client: TestClient, sid: str = "cli-1") -> None:
    assert client.post(
        "/api/v1/live-sessions", json={"session_id": sid}
    ).status_code == 200


def test_post_message_requires_registration(client: TestClient) -> None:
    r = client.post(
        "/api/v1/live-sessions/ghost/messages",
        json={"sender": "alice", "body": "hi"},
    )
    assert r.status_code == 404  # clear refusal when not serviceable


def test_message_roundtrip_poll_and_ack(client: TestClient) -> None:
    _register(client)
    r = client.post(
        "/api/v1/live-sessions/cli-1/messages",
        json={"sender": "alice", "body": "please rebase", "reply_to": "alice-sess"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    mid = body["message_id"]
    assert mid > 0

    # poll: message is pending, carrying its reply-to address
    listed = client.get("/api/v1/live-sessions/cli-1/messages").json()["messages"]
    assert [m["id"] for m in listed] == [mid]
    assert listed[0]["sender"] == "alice"
    assert listed[0]["body"] == "please rebase"
    assert listed[0]["reply_to"] == "alice-sess"

    # ack: marks delivered, drops from pending
    acked = client.post(
        "/api/v1/live-sessions/cli-1/messages/ack", json={"ids": [mid]}
    ).json()
    assert acked["acked"] == 1
    assert client.get("/api/v1/live-sessions/cli-1/messages").json()["messages"] == []

    # re-ack is idempotent
    assert client.post(
        "/api/v1/live-sessions/cli-1/messages/ack", json={"ids": [mid]}
    ).json()["acked"] == 0


def test_poll_and_ack_require_registration(client: TestClient) -> None:
    assert client.get("/api/v1/live-sessions/ghost/messages").status_code == 404
    assert client.post(
        "/api/v1/live-sessions/ghost/messages/ack", json={"ids": [1]}
    ).status_code == 404


def test_messages_are_ordered_oldest_first(client: TestClient) -> None:
    _register(client)
    ids = [
        client.post(
            "/api/v1/live-sessions/cli-1/messages",
            json={"sender": "s", "body": str(i)},
        ).json()["message_id"]
        for i in range(3)
    ]
    listed = client.get("/api/v1/live-sessions/cli-1/messages").json()["messages"]
    assert [m["id"] for m in listed] == ids
