"""Tests for the in-place handoff control surface (HTTP endpoints).

The manager-level primitive is covered by ``test_handoff.py``; here we verify
the route wiring and error mapping for ``POST /sessions/{id}/handoff`` and
``POST /worktrees/{id}/handoff`` -- the explicit control surface a UI consumer
(or the CLI ``handoff`` verb) drives.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import (
    DaemonDrainingError,
    Session,
    SessionManager,
)
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


def _seed_session(app, sid="sess-1", worktree_id="wt-1"):
    mgr: SessionManager = app.state.session_manager
    target = SpawnTarget(type="local", cwd="/wt", worktree_id=worktree_id)
    session = Session(sid, "calm-lake", target, "test-agent")
    session.status = SessionStatus.IDLE
    session.caller_id = worktree_id
    mgr._sessions[sid] = session
    mgr.db.create_session(
        sid, "calm-lake", "test-agent", "/wt", "local", "idle", time.time()
    )
    return mgr


def _fake_successor(worktree_id="wt-1", sid="sess-2"):
    target = SpawnTarget(type="local", cwd="/wt", worktree_id=worktree_id)
    succ = Session(sid, "bold-peak", target, "test-agent")
    succ.status = SessionStatus.IDLE
    succ.caller_id = worktree_id
    return succ


class TestSessionHandoffRoute:
    def test_unknown_session_404(self, client, app) -> None:
        resp = client.post("/api/v1/sessions/nope/handoff")
        assert resp.status_code == 404

    def test_happy_returns_successor(self, client, app, monkeypatch) -> None:
        mgr = _seed_session(app)
        succ = _fake_successor()

        async def _fake_handoff(session_id, *, reason=None, seed=True):
            assert session_id == "sess-1"
            assert reason == "ctx"
            assert seed is False
            return succ

        monkeypatch.setattr(mgr, "handoff_session", _fake_handoff)
        resp = client.post(
            "/api/v1/sessions/sess-1/handoff",
            params={"reason": "ctx", "seed": "false"},
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-2"

    def test_value_error_maps_409(self, client, app, monkeypatch) -> None:
        mgr = _seed_session(app)

        async def _raise(session_id, *, reason=None, seed=True):
            raise ValueError("single-checkout agent")

        monkeypatch.setattr(mgr, "handoff_session", _raise)
        resp = client.post("/api/v1/sessions/sess-1/handoff")
        assert resp.status_code == 409

    def test_runtime_error_maps_502(self, client, app, monkeypatch) -> None:
        mgr = _seed_session(app)

        async def _raise(session_id, *, reason=None, seed=True):
            raise RuntimeError("successor failed to start")

        monkeypatch.setattr(mgr, "handoff_session", _raise)
        resp = client.post("/api/v1/sessions/sess-1/handoff")
        assert resp.status_code == 502

    def test_draining_maps_503(self, client, app, monkeypatch) -> None:
        mgr = _seed_session(app)

        async def _raise(session_id, *, reason=None, seed=True):
            raise DaemonDrainingError("handoff")

        monkeypatch.setattr(mgr, "handoff_session", _raise)
        resp = client.post("/api/v1/sessions/sess-1/handoff")
        assert resp.status_code == 503


class TestWorktreeHandoffRoute:
    def test_no_session_404(self, client, app) -> None:
        resp = client.post("/api/v1/worktrees/ghost-wt/handoff")
        assert resp.status_code == 404

    def test_resolves_worktree_then_hands_off(
        self, client, app, monkeypatch
    ) -> None:
        mgr = _seed_session(app, sid="sess-1", worktree_id="wt-1")
        succ = _fake_successor(worktree_id="wt-1")
        seen = {}

        async def _fake_handoff(session_id, *, reason=None, seed=True):
            seen["session_id"] = session_id
            return succ

        monkeypatch.setattr(mgr, "handoff_session", _fake_handoff)
        resp = client.post("/api/v1/worktrees/wt-1/handoff")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-2"
        # The worktree handle resolved to its current session before handoff.
        assert seen["session_id"] == "sess-1"
