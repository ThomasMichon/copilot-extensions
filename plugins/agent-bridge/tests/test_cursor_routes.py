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

    def test_cursor_reports_head_id(self, client, app) -> None:
        # head_id lets a fresh caller tell it is behind unseen history
        # without reading the whole backlog (resume-marker, issue A).
        _seed_session(app, events=[
            {"id": 1, "event": "agent_message", "data": {"text": "a"}},
            {"id": 2, "event": "agent_message", "data": {"text": "b"}},
        ])
        resp = client.get("/api/v1/sessions/sess-1/cursor", params={"caller_id": "a"})
        body = resp.json()
        assert body["last_acked_id"] == 0
        assert body["head_id"] == 2

    def test_cursor_head_id_zero_when_no_events(self, client, app) -> None:
        _seed_session(app)
        resp = client.get("/api/v1/sessions/sess-1/cursor", params={"caller_id": "a"})
        assert resp.json()["head_id"] == 0


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


class TestStatusEndpoint:
    """GET /api/v1/sessions/{id}/status -- compact dispatch status (#46.1)."""

    def test_status_nonexistent(self, client) -> None:
        resp = client.get("/api/v1/sessions/nope/status")
        assert resp.status_code == 404

    def test_status_reports_head_and_cursor_lag(self, client, app) -> None:
        _seed_session(app, events=[
            {"id": 1, "event": "agent_message", "data": {"text": "a"}},
            {"id": 2, "event": "agent_message", "data": {"text": "b"}},
            {"id": 3, "event": "agent_message", "data": {"text": "c"}},
        ])
        # Ack up to event 1 for caller "a"; status should report behind=2.
        client.post("/api/v1/sessions/sess-1/cursor",
                    json={"last_id": 1, "caller_id": "a"})
        resp = client.get("/api/v1/sessions/sess-1/status",
                          params={"caller_id": "a"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "idle"
        assert body["head_id"] == 3
        assert body["last_acked_id"] == 1
        assert body["behind"] == 2
        assert body["active_tool"] is None

    def test_status_surfaces_in_flight_tool(self, client, app) -> None:
        from agent_bridge.events import EventLog

        mgr = _seed_session(app)
        # An in-flight tool lives only in the in-memory event log; the status
        # endpoint surfaces it (with elapsed) where `read` cannot see it.
        log = EventLog()
        log.append("tool_call_start", {
            "tool_call_id": "t1", "title": "Build",
            "raw_input": {"command": "rush build"},
        })
        mgr._sessions["sess-1"].event_log = log
        resp = client.get("/api/v1/sessions/sess-1/status")
        assert resp.status_code == 200
        active = resp.json()["active_tool"]
        assert active is not None
        assert active["title"] == "Build"
        assert active["command"] == "rush build"
        assert active["elapsed_s"] >= 0.0

    def test_status_surfaces_progress_markers(self, client, app) -> None:
        # #46.3: structured PROGRESS markers the agent reported are exposed.
        mgr = _seed_session(app)
        mgr._sessions["sess-1"].progress = {"build": "ok", "pr": "42"}
        resp = client.get("/api/v1/sessions/sess-1/status")
        assert resp.status_code == 200
        assert resp.json()["progress"] == {"build": "ok", "pr": "42"}
