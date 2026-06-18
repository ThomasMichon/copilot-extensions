"""Tests for the ACP-over-WebSocket transport and the built-in status UX.

Covers:
* ``GET /ui`` is served as auth-exempt HTML.
* ``WS /acp/{agent}`` rejects connections without a valid bearer token, accepts
  with a ``bearer.<token>`` subprotocol, and completes an ACP ``initialize``
  handshake.
* Unknown agent / unknown session targets are rejected.
* ``BridgeAgent`` adopt mode reuses an existing session and never stops it on
  cleanup.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge.acp_agent import BridgeAgent
from agent_bridge.app import create_app
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    """Prevent auto-discovery from picking up a real projects.yaml."""
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(
        port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"),
    )
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def sm(tmp_path):
    from agent_bridge.db import Database

    return SessionManager(Database(tmp_path / "adopt.db"))


def _make_idle_session(sm: SessionManager, sid: str = "s1") -> Session:
    target = SpawnTarget(type="local", cwd="/tmp")
    session = Session(sid, sid, target)
    session.status = SessionStatus.IDLE
    session.event_log = EventLog()
    sm._sessions[sid] = session
    sm._db.create_session(sid, sid, None, "/tmp", "local", "idle", time.time())
    return session


# ---------------------------------------------------------------------------
# Status UX
# ---------------------------------------------------------------------------


class TestStatusUi:
    def test_ui_served_without_auth(self, app):
        with TestClient(app) as c:
            resp = c.get("/ui")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "Agent Bridge" in resp.text
            # No Authorization header was sent, yet the page loads.
            assert "/acp/" in resp.text


# ---------------------------------------------------------------------------
# WebSocket ACP transport
# ---------------------------------------------------------------------------


class TestAcpWebSocket:
    def test_rejects_without_token(self, app):
        with TestClient(app) as c:
            with pytest.raises(Exception):
                with c.websocket_connect(
                    "/acp/test-agent", subprotocols=["acp.v1"]
                ) as ws:
                    ws.receive_text()

    def test_rejects_bad_token(self, app):
        with TestClient(app) as c:
            with pytest.raises(Exception):
                with c.websocket_connect(
                    "/acp/test-agent",
                    subprotocols=["acp.v1", "bearer.wrong"],
                ) as ws:
                    ws.receive_text()

    def test_rejects_unknown_session(self, app):
        with TestClient(app) as c:
            with pytest.raises(Exception):
                with c.websocket_connect(
                    "/acp/session/does-not-exist",
                    subprotocols=["acp.v1", "bearer.test-token"],
                ) as ws:
                    ws.receive_text()

    def test_initialize_handshake(self, app):
        with TestClient(app) as c:
            # Drop the resolver so agent-existence validation is skipped;
            # `initialize` does not spawn a downstream agent.
            app.state.resolver = None
            with c.websocket_connect(
                "/acp/test-agent",
                subprotocols=["acp.v1", "bearer.test-token"],
            ) as ws:
                ws.send_text(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": 1},
                }))
                raw = ws.receive_text()
                resp = json.loads(raw)
                assert resp["id"] == 1
                assert "error" not in resp
                # agent_info.name should identify the bridge (alias-agnostic).
                assert "agent-bridge" in raw

    def test_negotiates_acp_subprotocol(self, app):
        with TestClient(app) as c:
            app.state.resolver = None
            with c.websocket_connect(
                "/acp/test-agent",
                subprotocols=["acp.v1", "bearer.test-token"],
            ) as ws:
                assert ws.accepted_subprotocol == "acp.v1"


# ---------------------------------------------------------------------------
# BridgeAgent adopt mode
# ---------------------------------------------------------------------------


class TestAdoptMode:
    @pytest.mark.asyncio
    async def test_new_session_adopts_existing(self, sm):
        session = _make_idle_session(sm, "s1")
        agent = BridgeAgent(sm, adopt_session_id="s1")

        resp = await agent.new_session(cwd="/tmp")

        assert resp.session_id == "s1"
        assert "s1" in agent._adopted_sessions
        assert "s1" not in agent._owned_sessions
        assert session.status == SessionStatus.IDLE  # not re-spawned

    @pytest.mark.asyncio
    async def test_adopt_unknown_session_raises(self, sm):
        agent = BridgeAgent(sm, adopt_session_id="missing")
        with pytest.raises(Exception):
            await agent.new_session(cwd="/tmp")

    @pytest.mark.asyncio
    async def test_cleanup_does_not_stop_adopted(self, sm):
        session = _make_idle_session(sm, "s1")
        session.client = AsyncMock()
        session.client.is_running = True
        agent = BridgeAgent(sm, adopt_session_id="s1")
        await agent.new_session(cwd="/tmp")

        await agent.cleanup()

        # Adopted session belongs to its original owner -- left running.
        assert session.status == SessionStatus.IDLE
        assert "s1" in agent._adopted_sessions

    @pytest.mark.asyncio
    async def test_list_sessions_includes_adopted(self, sm):
        _make_idle_session(sm, "s1")
        agent = BridgeAgent(sm, adopt_session_id="s1")
        await agent.new_session(cwd="/tmp")

        resp = await agent.list_sessions()
        assert any(s.session_id == "s1" for s in resp.sessions)
