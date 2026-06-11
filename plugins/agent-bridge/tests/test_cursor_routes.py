"""Tests for the delivery-cursor and range HTTP endpoints."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        yield c


def _seed_session(app, sid="sess-1", events=None):
    """Add an in-memory session + DB row (+ optional events)."""
    mgr: SessionManager = app.state.session_manager
    target = SpawnTarget(type="local", cwd="/wt")
    session = Session(sid, "calm-lake", target, "test-agent")
    session.status = SessionStatus.IDLE
    mgr._sessions[sid] = session
    mgr.db.create_session(sid, "calm-lake", "test-agent", "/wt", "local",
                          "idle", time.time())
    for e in events or []:
        mgr.db.append_event(sid, e["id"], e["event"], e["data"], time.time())
    return mgr


class TestCursorEndpoints:
    def test_get_cursor_defaults_zero(self, client, app) -> None:
        _seed_session(app)
        resp = client.get("/api/v1/sessions/sess-1/cursor", params={"caller_id": "a"})
        assert resp.status_code == 200
        assert resp.json()["last_acked_id"] == 0

    def test_ack_then_get(self, client, app) -> None:
        _seed_session(app)
        ack = client.post(
            "/api/v1/sessions/sess-1/cursor",
            json={"caller_id": "a", "last_id": 7},
        )
        assert ack.status_code == 200
        assert ack.json()["last_acked_id"] == 7

        got = client.get("/api/v1/sessions/sess-1/cursor", params={"caller_id": "a"})
        assert got.json()["last_acked_id"] == 7

    def test_ack_is_monotonic(self, client, app) -> None:
        _seed_session(app)
        client.post("/api/v1/sessions/sess-1/cursor",
                    json={"caller_id": "a", "last_id": 10})
        resp = client.post("/api/v1/sessions/sess-1/cursor",
                           json={"caller_id": "a", "last_id": 3})
        # Stale ack ignored.
        assert resp.json()["last_acked_id"] == 10

    def test_cursor_per_caller(self, client, app) -> None:
        _seed_session(app)
        client.post("/api/v1/sessions/sess-1/cursor",
                    json={"caller_id": "a", "last_id": 5})
        client.post("/api/v1/sessions/sess-1/cursor",
                    json={"caller_id": "b", "last_id": 2})
        a = client.get("/api/v1/sessions/sess-1/cursor", params={"caller_id": "a"})
        b = client.get("/api/v1/sessions/sess-1/cursor", params={"caller_id": "b"})
        assert a.json()["last_acked_id"] == 5
        assert b.json()["last_acked_id"] == 2

    def test_cursor_unknown_session_404(self, client) -> None:
        resp = client.get("/api/v1/sessions/nope/cursor")
        assert resp.status_code == 404


class TestRangeEndpoint:
    def test_range_returns_events(self, client, app) -> None:
        _seed_session(app, events=[
            {"id": 1, "event": "agent_message", "data": {"text": "a"}},
            {"id": 2, "event": "agent_message", "data": {"text": "b"}},
            {"id": 3, "event": "agent_message", "data": {"text": "c"}},
        ])
        resp = client.get(
            "/api/v1/sessions/sess-1/events/range",
            params={"start": 2, "end": 3},
        )
        assert resp.status_code == 200
        ids = [e["id"] for e in resp.json()["events"]]
        assert ids == [2, 3]

    def test_range_does_not_move_cursor(self, client, app) -> None:
        _seed_session(app, events=[
            {"id": 1, "event": "agent_message", "data": {"text": "a"}},
        ])
        client.get("/api/v1/sessions/sess-1/events/range", params={"start": 1})
        cur = client.get("/api/v1/sessions/sess-1/cursor")
        assert cur.json()["last_acked_id"] == 0
