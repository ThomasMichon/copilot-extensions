"""Tests for HTTP API routes."""

from __future__ import annotations

import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_bridge import __version__
from agent_bridge.app import create_app
from agent_bridge.agent_registry import AgentConfig, AgentResolver
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.protocol import FAILED_ACP_HANDSHAKE_FAULT
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.topology import MachineConfig
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    """Prevent auto-discovery from picking up real projects.yaml."""
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path / "bridge"))


@pytest.fixture(autouse=True)
def _route_local_start_via_classic(monkeypatch):
    """Session Hosts are always on (dotfiles#1478), so a local ``start_session``
    now connects through ``_connect_via_session_host`` (a survivable host over a
    loopback socket) rather than the classic ``spawn`` + ``AcpClient`` path that
    these route tests mock and assert on (the target handed to ``spawn``). That
    real host machinery can't stand up in a unit test, so re-route the host
    connect back through the module-level ``spawn`` + ``AcpClient`` -- both still
    patched per-test -- preserving each test's contract. Never invoked unless a
    test actually starts a local session, so it is inert elsewhere.
    """
    import agent_bridge.session_manager as sm

    async def _fake_connect(
        self, target, *, tracker, session_id, on_acp_event,
        permission_callback=None, mcp_servers=None, spawner=None,
        remote_child_argv=None, remote_cwd=None, model=None, effort=None,
    ):
        proc = await sm.spawn(
            target, tracker=tracker, connect_timeout=0, session_id=session_id,
        )
        client = sm.AcpClient(
            on_event=on_acp_event, on_permission=permission_callback,
            model_override=model, effort_override=effort,
        )
        await client.start(proc.proc)
        acp_sid = await client.new_session(
            cwd=target.cwd or "/", mcp_servers=mcp_servers,
        )
        return client, acp_sid

    monkeypatch.setattr(
        sm.SessionManager, "_connect_via_session_host", _fake_connect
    )


@pytest.fixture
def app(tmp_path):
    """Create a FastAPI test app with real DB but mocked session starts."""
    cfg = ServiceConfig(
        port=0,
        bind="127.0.0.1",
        db_path=str(tmp_path / "test.db"),
    )
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    """TestClient with auth header."""
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer test-token"
        yield c


def test_failed_handshake_fault_requires_harness_caller(client) -> None:
    response = client.post(
        "/api/v1/sessions",
        json={
            "agent": "container:example",
            "force_new": True,
            "parity_fault": FAILED_ACP_HANDSHAKE_FAULT,
        },
    )

    assert response.status_code == 403


def test_failed_handshake_fault_refuses_active_session(client, app) -> None:
    manager = app.state.session_manager
    incumbent = Session(
        "incumbent",
        "active",
        SpawnTarget(type="local", cwd="."),
    )
    incumbent.status = SessionStatus.IDLE
    manager._sessions[incumbent.session_id] = incumbent

    response = client.post(
        "/api/v1/sessions",
        json={
            "agent": "container:example",
            "caller_id": "venue-parity:test",
            "force_new": True,
            "parity_fault": FAILED_ACP_HANDSHAKE_FAULT,
        },
    )

    assert response.status_code == 409
    assert "incumbent" in response.json()["detail"]


class TestHealthEndpoint:
    """Health check route."""

    def test_health(self, app) -> None:
        with TestClient(app) as c:
            resp = c.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert resp.json()["version"] == __version__
            assert resp.json()["topology_error_count"] == 0
            assert resp.json()["topology_warning_count"] == 0
            carrier_health = resp.json()["ssh_carriers"]
            assert {
                "total",
                "healthy",
                "degraded",
                "logical_clients",
                "active_requests",
                "active_subscriptions",
                "queued_frames",
                "buffered_bytes",
            } == set(carrier_health)


class TestAuthMiddleware:
    """Bearer token authentication."""

    def test_missing_auth(self, app) -> None:
        with TestClient(app) as c:
            resp = c.get("/api/v1/sessions")
            assert resp.status_code == 401

    def test_wrong_token(self, app) -> None:
        with TestClient(app) as c:
            resp = c.get(
                "/api/v1/sessions",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert resp.status_code == 403

    def test_valid_token(self, client) -> None:
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200

    def test_list_includes_offline_persisted_elevated_sessions(
        self, client, monkeypatch
    ) -> None:
        from agent_bridge import elevated

        manager = client.app.state.session_manager
        primary = Session(
            "primary-session",
            "outer-session",
            SpawnTarget(
                type="command",
                cwd="/repo",
                spawn_command=[
                    "python",
                    "-m",
                    "agent_bridge",
                    "acp-connect",
                    "ws://127.0.0.1:65000/acp/admin-agent",
                    "--stdio",
                ],
                elevated=True,
            ),
            "admin-agent",
        )
        primary.status = SessionStatus.STOPPED
        primary.acp_session_id = "linked-elevated-session"
        manager._sessions[primary.session_id] = primary

        now = time.time()
        rows = [
            {
                "id": "linked-elevated-session",
                "name": "linked-inner",
                "agent_name": "admin-agent",
                "caller_id": None,
                "target_dir": "/repo",
                "target_type": "local",
                "target_json": None,
                "status": "stopped",
                "pid": None,
                "acp_session_id": "acp-linked",
                "context_size": None,
                "context_used": None,
                "usage_model": None,
                "last_usage_at": None,
                "created_at": now - 20,
                "updated_at": now - 10,
                "turn_count": 1,
            },
            {
                "id": "orphaned-elevated-session",
                "name": "orphaned-inner",
                "agent_name": "admin-agent",
                "caller_id": None,
                "target_dir": "/repo",
                "target_type": "local",
                "target_json": None,
                "status": "running",
                "pid": 4242,
                "acp_session_id": "acp-orphaned",
                "context_size": 1000,
                "context_used": 250,
                "usage_model": "example",
                "last_usage_at": now - 5,
                "created_at": now - 30,
                "updated_at": now,
                "turn_count": 3,
            },
        ]
        monkeypatch.setattr(elevated, "is_subdaemon", lambda: False)
        monkeypatch.setattr(elevated, "persisted_session_rows", lambda: rows)
        monkeypatch.setattr(elevated, "is_up", lambda **_kwargs: False)

        response = client.get("/api/v1/sessions?status=stopped")

        assert response.status_code == 200
        sessions = {
            session["session_id"]: session
            for session in response.json()["sessions"]
        }
        assert set(sessions) == {
            "primary-session",
            "orphaned-elevated-session",
        }
        orphaned = sessions["orphaned-elevated-session"]
        assert orphaned["elevated"] is True
        assert orphaned["read_only"] is True
        assert orphaned["status"] == "stopped"
        assert orphaned["pid"] is None
        assert orphaned["turn_count"] == 3
        assert orphaned["context_pct"] == 25.0
        assert sessions["primary-session"]["elevated"] is True
        assert sessions["primary-session"]["read_only"] is False

    def test_list_status_filter_keeps_normalized_elevated_session(
        self, client, monkeypatch
    ) -> None:
        from agent_bridge import elevated

        manager = client.app.state.session_manager
        primary = Session(
            "primary-session",
            "outer-session",
            SpawnTarget(type="command", cwd="/repo"),
            "admin-agent",
        )
        primary.status = SessionStatus.RUNNING
        primary.acp_session_id = "linked-elevated-session"
        manager._sessions[primary.session_id] = primary

        now = time.time()
        rows = [{
            "id": "linked-elevated-session",
            "name": "linked-inner",
            "agent_name": "admin-agent",
            "caller_id": None,
            "target_dir": "/repo",
            "target_type": "local",
            "target_json": None,
            "status": "running",
            "pid": 4242,
            "acp_session_id": "acp-linked",
            "context_size": None,
            "context_used": None,
            "usage_model": None,
            "last_usage_at": None,
            "created_at": now - 10,
            "updated_at": now,
            "turn_count": 1,
        }]
        monkeypatch.setattr(elevated, "is_subdaemon", lambda: False)
        monkeypatch.setattr(elevated, "persisted_session_rows", lambda: rows)
        monkeypatch.setattr(elevated, "is_up", lambda **_kwargs: False)

        response = client.get("/api/v1/sessions?status=stopped")

        assert response.status_code == 200
        sessions = response.json()["sessions"]
        assert [session["session_id"] for session in sessions] == [
            "linked-elevated-session"
        ]
        assert sessions[0]["status"] == "stopped"
        assert sessions[0]["elevated"] is True


class TestSessionRoutes:
    """Session CRUD routes."""

    def test_list_sessions_empty(self, client) -> None:
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []
        assert client.get("/api/v1/sessions?status=").json()["sessions"] == []

    def test_at_rest_session_projects_idle_on_read_surfaces(self, client) -> None:
        manager = client.app.state.session_manager
        session = Session(
            "at-rest-session",
            "reviewer",
            SpawnTarget(type="local", cwd="/repo"),
        )
        session.status = SessionStatus.RUNNING
        session.event_log = EventLog()
        session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
        manager._sessions[session.session_id] = session

        idle = client.get("/api/v1/sessions?status=idle").json()["sessions"]
        assert [item["session_id"] for item in idle] == [session.session_id]
        assert idle[0]["status"] == "idle"
        assert idle[0]["at_rest"] is True
        assert idle[0]["liveness"] is None
        assert client.get("/api/v1/sessions?status=running").json()["sessions"] == []

        usage = client.get(
            f"/api/v1/sessions/{session.session_id}/usage"
        ).json()
        assert usage["status"] == "idle"
        assert usage["at_rest"] is True

        status = client.get(
            f"/api/v1/sessions/{session.session_id}/status"
        ).json()
        assert status["status"] == "idle"
        assert status["at_rest"] is True

    def test_get_nonexistent_session(self, client) -> None:
        resp = client.get("/api/v1/sessions/nonexistent")
        assert resp.status_code == 404

    def test_get_session_falls_through_to_cold_store_provider(
        self, client, app
    ) -> None:
        """A session absent from the live ledger is answered by a registered
        cold-store provider instead of an immediate 404 (Phase 2b)."""
        from agent_bridge.cold_store import ColdStoreSession

        mgr = app.state.session_manager
        cold = ColdStoreSession(
            session_id="archived-1",
            status="ended",
            cwd="/repo",
            worktree_id="wt-1",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        )
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=cold)
        ):
            resp = client.get("/api/v1/sessions/archived-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "archived-1"
        assert body["worktree_id"] == "wt-1"
        assert body["read_only"] is True
        assert body["at_rest"] is True

    def test_get_session_404s_when_no_cold_store_answer(
        self, client, app
    ) -> None:
        mgr = app.state.session_manager
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=None)
        ):
            resp = client.get("/api/v1/sessions/nowhere")
        assert resp.status_code == 404

    def test_get_session_transcript_returns_cold_store_events(
        self, client, app
    ) -> None:
        """A bare (non-worktree-scoped, `worktree_id=None`) session's
        transcript is answered by the registered cold-store provider --
        letting a solo-session consumer retire its own direct historical
        transcript dependency."""
        from agent_bridge.cold_store import ColdStoreSession

        mgr = app.state.session_manager
        cold = ColdStoreSession(
            session_id="archived-2",
            status="ended",
            worktree_id=None,
            events=({"type": "message", "text": "hi"},),
        )
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=cold)
        ):
            resp = client.get("/api/v1/sessions/archived-2/transcript")
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "archived-2"
        assert body["events"] == [{"type": "message", "text": "hi"}]
        assert body["meta"]["worktree_id"] is None
        assert body["meta"]["read_only"] is True
        assert body["meta"]["at_rest"] is True

    def test_get_session_transcript_404s_when_no_cold_store_answer(
        self, client, app
    ) -> None:
        mgr = app.state.session_manager
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=None)
        ):
            resp = client.get("/api/v1/sessions/nowhere/transcript")
        assert resp.status_code == 404

    def test_get_session_transcript_prefers_a_live_session(
        self, client, app
    ) -> None:
        """A solo session can be LIVE (no owning agent to shell into), so
        the live ledger must be checked before ever falling through to
        cold storage -- otherwise a live bare session would 404 or read
        stale archived content instead of its real transcript."""
        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt")
        session = Session("live-1", "calm-lake", target, "test-agent")
        session.status = SessionStatus.IDLE
        mgr._sessions["live-1"] = session
        mgr.db.create_session(
            "live-1", "calm-lake", "test-agent", "/wt", "local",
            "idle", time.time(),
        )
        mgr.db.append_event(
            "live-1", 1, "agent_message", {"text": "hi"}, time.time()
        )
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=None)
        ) as cold_fetch:
            resp = client.get("/api/v1/sessions/live-1/transcript")
        cold_fetch.assert_not_called()
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "live-1"
        assert body["events"][0]["data"] == {"text": "hi"}
        assert body["meta"]["worktree_id"] is None
        assert body["meta"]["read_only"] is False
        assert body["meta"]["at_rest"] is True

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session(self, mock_acp_cls, mock_spawn, client) -> None:
        # Set up mocks
        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 42
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 42
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-123")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        resp = client.post(
            "/api/v1/sessions",
            json={"target_dir": "/tmp/test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["status"] == "idle"
        # The session-create response advertises the daemon's HTTP contract
        # version so a cross-host caller can gate across skew (dotfiles #632).
        from agent_bridge.protocol import (
            HTTP_PROTOCOL_MIN_SUPPORTED,
            HTTP_PROTOCOL_VERSION,
        )
        assert data["protocol_version"] == HTTP_PROTOCOL_VERSION
        assert data["min_protocol_version"] == HTTP_PROTOCOL_MIN_SUPPORTED

        # Verify it shows in list
        resp2 = client.get("/api/v1/sessions")
        assert len(resp2.json()["sessions"]) == 1

    def test_stop_nonexistent(self, client) -> None:
        resp = client.post("/api/v1/sessions/nonexistent/stop")
        assert resp.status_code == 404

    def test_interrupt_nonexistent(self, client) -> None:
        resp = client.post("/api/v1/sessions/nonexistent/interrupt")
        assert resp.status_code == 404

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_interrupt_idle_session_returns_state(
        self, mock_acp_cls, mock_spawn, client
    ) -> None:
        """Interrupting a session with no live turn is a no-op that returns the
        (idle) session state -- never a 404/500, never a teardown."""
        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 42
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 42
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-123")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        sid = client.post(
            "/api/v1/sessions", json={"target_dir": "/tmp/test"},
        ).json()["session_id"]

        resp = client.post(f"/api/v1/sessions/{sid}/interrupt")
        assert resp.status_code == 200
        assert resp.json()["status"] == "idle"
        mock_client.cancel_prompt.assert_not_called()

    def test_ask_user_nonexistent(self, client) -> None:
        resp = client.post(
            "/api/v1/sessions/nonexistent/ask-user",
            json={"tool_call_id": "tc", "content": {}},
        )
        assert resp.status_code == 404

    def test_ask_user_answers_pending(self, client, app) -> None:
        mgr = app.state.session_manager
        with patch.object(
            mgr, "answer_ask_user", AsyncMock(return_value=True)
        ) as m:
            resp = client.post(
                "/api/v1/sessions/s1/ask-user",
                json={"tool_call_id": "tc-1", "content": {"choice": "a"}},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "answered"
        m.assert_awaited_once_with(
            "s1", "tc-1", {"choice": "a"}, action="accept"
        )

    def test_ask_user_none_pending_returns_409(self, client, app) -> None:
        mgr = app.state.session_manager
        with patch.object(mgr, "answer_ask_user", AsyncMock(return_value=False)):
            resp = client.post(
                "/api/v1/sessions/s1/ask-user",
                json={"tool_call_id": "tc-x", "content": {}},
            )
        assert resp.status_code == 409

    def test_ask_user_no_live_client_returns_409(self, client, app) -> None:
        mgr = app.state.session_manager
        with patch.object(
            mgr, "answer_ask_user", AsyncMock(side_effect=ValueError("no client"))
        ):
            resp = client.post(
                "/api/v1/sessions/s1/ask-user",
                json={"tool_call_id": "tc", "content": {}},
            )
        assert resp.status_code == 409

    def test_delete_nonexistent(self, client) -> None:
        resp = client.delete("/api/v1/sessions/nonexistent")
        assert resp.status_code == 404

    def test_delete_cleanup_pending_returns_conflict(
        self,
        client,
        app,
    ) -> None:
        with patch.object(
            app.state.session_manager,
            "end_session",
            AsyncMock(side_effect=RuntimeError("retained target ownership")),
        ):
            resp = client.delete("/api/v1/sessions/session-1")

        assert resp.status_code == 409
        assert "retained target ownership" in resp.json()["detail"]

    def test_resume_nonexistent(self, client) -> None:
        resp = client.post("/api/v1/sessions/nonexistent/resume")
        assert resp.status_code == 404

    def test_resume_provider_refresh_failure_returns_safe_502(
        self, client, app,
    ) -> None:
        from agent_bridge.session_manager import ProviderTargetRefreshError

        mgr = app.state.session_manager
        with patch.object(
            mgr,
            "resume_session",
            AsyncMock(
                side_effect=ProviderTargetRefreshError("internal provider detail")
            ),
        ):
            resp = client.post("/api/v1/sessions/session-1/resume")

        assert resp.status_code == 502
        assert resp.json()["detail"] == ProviderTargetRefreshError.public_message

    def test_resync_nonexistent(self, client) -> None:
        resp = client.post("/api/v1/sessions/nonexistent/resync")
        assert resp.status_code == 404

    def test_resync_success(self, client, app) -> None:
        """Resync route returns the rebuilt event count and latest id."""
        mgr = app.state.session_manager
        fake = MagicMock()
        fake.status = SessionStatus.IDLE
        fake.event_log = MagicMock()
        fake.event_log.latest_id = 5
        with patch.object(mgr, "resync_session", AsyncMock(return_value=5)), \
             patch.object(mgr, "get_session", MagicMock(return_value=fake)):
            resp = client.post("/api/v1/sessions/s1/resync")

        assert resp.status_code == 200
        body = resp.json()
        assert body["event_count"] == 5
        assert body["latest_id"] == 5
        assert body["status"] == SessionStatus.IDLE.value

    def test_resync_running_returns_409(self, client, app) -> None:
        """Resyncing a session mid-turn is rejected with 409."""
        mgr = app.state.session_manager
        with patch.object(
            mgr, "resync_session",
            AsyncMock(side_effect=ValueError("Session s1 is running a turn -- cannot resync")),
        ):
            resp = client.post("/api/v1/sessions/s1/resync")
        assert resp.status_code == 409

    def test_start_session_conflict_returns_409(self, client, app) -> None:
        """Concurrency guard surfaces as 409 with the existing session id."""
        from agent_bridge.session_manager import SessionConflictError
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="command", cwd="/workspaces/repo",
            spawn_command=["gh", "codespace", "ssh", "-c", "cs-name"],
        ))
        app.state.resolver = mock_resolver

        mgr = app.state.session_manager
        with patch.object(
            mgr, "start_session",
            AsyncMock(side_effect=SessionConflictError(
                agent_name="codespace:cs-name",
                existing_session_id="abc123",
            )),
        ):
            resp = client.post(
                "/api/v1/sessions",
                json={"agent": "codespace:cs-name"},
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "session_conflict"
        assert detail["existing_session_id"] == "abc123"
        assert detail["agent_name"] == "codespace:cs-name"

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_with_agent_and_worktree_id(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """Session roll: start session with agent + worktree_id."""
        # Register a test agent via resolver
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local", cwd="/original/dir", project="test-project",
        ))
        app.state.resolver = mock_resolver

        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 99
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 99
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-456")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        resp = client.post(
            "/api/v1/sessions",
            json={
                "agent": "test-agent",
                "worktree_id": "anomalous-potato-wsl-20250101-120000-abc1",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["status"] == "idle"

        # Verify the resolved target got worktree_id set
        spawn_call = mock_spawn.call_args
        target = spawn_call.args[0]
        assert target.worktree_id == "anomalous-potato-wsl-20250101-120000-abc1"

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_with_agent_and_target_dir(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """Session roll: start session with agent + target_dir."""
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local", cwd="/original/dir", project="test-project",
        ))
        app.state.resolver = mock_resolver

        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 100
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 100
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-789")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        resp = client.post(
            "/api/v1/sessions",
            json={
                "agent": "test-agent",
                "target_dir": "/worktree/path",
            },
        )
        assert resp.status_code == 201

        # Verify the resolved target got cwd overridden
        spawn_call = mock_spawn.call_args
        target = spawn_call.args[0]
        assert target.cwd == "/worktree/path"
        assert target.project == "test-project"
        assert target.explicit_cwd is True

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_appends_per_session_copilot_args(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """Per-session copilot_args are appended to the agent's own args on the
        spawned target (e.g. a run-bound --additional-mcp-config)."""
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local",
            cwd="/d",
            copilot_args=["--allow-all"],
            venue={"kind": "provider"},
        ))
        app.state.resolver = mock_resolver

        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 101
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 101
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-cargs")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        resp = client.post(
            "/api/v1/sessions",
            json={
                "agent": "test-agent",
                "copilot_args": ["--additional-mcp-config", "@/tmp/run.json"],
                "env": {"REQUEST_OVERRIDE": "kept"},
            },
        )
        assert resp.status_code == 201

        target = mock_spawn.call_args.args[0]
        # Agent's own args preserved, per-session args appended after them.
        assert target.copilot_args == [
            "--allow-all", "--additional-mcp-config", "@/tmp/run.json",
        ]
        assert target.env == {"REQUEST_OVERRIDE": "kept"}
        assert target.venue["_agent_bridge_request_overrides"] == {
            "env": {"REQUEST_OVERRIDE": "kept"},
            "copilot_args": ["--additional-mcp-config", "@/tmp/run.json"],
        }

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_without_copilot_args_unchanged(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """Omitting copilot_args leaves the agent's args untouched (back-compat)."""
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local", cwd="/d", copilot_args=["--allow-all"],
        ))
        app.state.resolver = mock_resolver

        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 102
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 102
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-plain")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        resp = client.post(
            "/api/v1/sessions", json={"agent": "test-agent"},
        )
        assert resp.status_code == 201
        target = mock_spawn.call_args.args[0]
        assert target.copilot_args == ["--allow-all"]

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_uses_agent_declared_mcp_servers(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """A resolved agent's own declared MCP servers (AgentConfig.mcp_servers
        -> SpawnTarget.mcp_servers) are used when the request doesn't supply
        its own -- the explicit-injection workaround for Copilot CLI not
        reliably loading a custom agent's own mcp-servers: frontmatter under
        headless/ACP sessions (github/copilot-cli#2630)."""
        from agent_bridge.transport import SpawnTarget

        agent_servers = [
            {"name": "gitea-mcp", "type": "stdio", "command": "agent-mcp",
             "args": ["bridge", "--config", "agents/gitea.mcp.yaml"]},
        ]
        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local", cwd="/d", mcp_servers=agent_servers,
        ))
        app.state.resolver = mock_resolver

        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 103
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 103
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-mcp")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        resp = client.post("/api/v1/sessions", json={"agent": "test-agent"})
        assert resp.status_code == 201
        assert mock_client.new_session.await_args.kwargs["mcp_servers"] == (
            agent_servers
        )

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_request_mcp_servers_override_agent_default(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """An explicit per-request mcp_servers wins over the agent's own
        declared default."""
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local", cwd="/d",
            mcp_servers=[{"name": "agent-default", "type": "stdio",
                          "command": "agent-mcp"}],
        ))
        app.state.resolver = mock_resolver

        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 104
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 104
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-override")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        request_servers = [{"name": "request-scoped", "type": "stdio",
                             "command": "other-tool"}]
        resp = client.post(
            "/api/v1/sessions",
            json={"agent": "test-agent", "mcp_servers": request_servers},
        )
        assert resp.status_code == 201
        assert mock_client.new_session.await_args.kwargs["mcp_servers"] == (
            request_servers
        )

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_reuses_by_caller_id(
        self, mock_acp_cls, mock_spawn, client,
    ) -> None:
        """A second start with the same caller_id reuses the alive session."""
        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 42
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 42
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-reuse")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        first = client.post(
            "/api/v1/sessions",
            json={"target_dir": "/tmp/test", "caller_id": "wt-guid-1"},
        )
        assert first.status_code == 201
        first_id = first.json()["session_id"]

        # Second create with the same caller_id must return the same session
        # and must not spawn a second process.
        spawn_count_after_first = mock_spawn.call_count
        second = client.post(
            "/api/v1/sessions",
            json={"target_dir": "/tmp/test", "caller_id": "wt-guid-1"},
        )
        assert second.status_code == 201
        assert second.json()["session_id"] == first_id
        assert mock_spawn.call_count == spawn_count_after_first

        # Only one session should exist.
        listing = client.get("/api/v1/sessions").json()["sessions"]
        assert len([s for s in listing if s["caller_id"] == "wt-guid-1"]) == 1

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_force_new_bypasses_reuse(
        self, mock_acp_cls, mock_spawn, client,
    ) -> None:
        """force_new creates a fresh session even when caller_id matches."""
        mock_proc = MagicMock()
        mock_proc.proc = MagicMock()
        mock_proc.proc.pid = 42
        mock_proc.proc.returncode = None
        mock_proc.proc.stdin = MagicMock()
        mock_proc.proc.stdout = MagicMock()
        mock_proc.proc.stderr = MagicMock()
        mock_proc.proc.stderr.readline = AsyncMock(return_value=b"")
        mock_spawn.return_value = mock_proc

        mock_client = MagicMock()
        mock_client.is_running = True
        mock_client.pid = 42
        mock_client.start = AsyncMock()
        mock_client.new_session = AsyncMock(return_value="acp-force")
        mock_client.shutdown = AsyncMock()
        mock_client.cancel_prompt = AsyncMock()
        mock_acp_cls.return_value = mock_client

        first = client.post(
            "/api/v1/sessions",
            json={"target_dir": "/tmp/test", "caller_id": "wt-guid-2"},
        )
        assert first.status_code == 201
        first_id = first.json()["session_id"]

        second = client.post(
            "/api/v1/sessions",
            json={
                "target_dir": "/tmp/test",
                "caller_id": "wt-guid-2",
                "force_new": True,
            },
        )
        assert second.status_code == 201
        assert second.json()["session_id"] != first_id

        listing = client.get("/api/v1/sessions").json()["sessions"]
        assert len([s for s in listing if s["caller_id"] == "wt-guid-2"]) == 2


class TestAgentRoutes:
    """Agent registry routes."""

    def test_list_agents_empty(self, client) -> None:
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert resp.json()["agents"] == []
        assert resp.json()["topology_errors"] == []
        assert resp.json()["topology_warnings"] == []

    def test_machine_routes_include_static_metadata(self, client, app) -> None:
        machine = MachineConfig(
            key="host-a",
            display_name="Host A",
            role="worker",
            description="General-purpose worker.",
            capabilities=["builds", "tests"],
        )
        app.state.resolver = AgentResolver({}, {"host-a": machine})

        listing = client.get("/api/v1/machines")
        detail = client.get("/api/v1/machines/host-a")

        assert listing.status_code == 200
        assert listing.json()["machines"][0]["description"] == (
            "General-purpose worker."
        )
        assert listing.json()["machines"][0]["capabilities"] == ["builds", "tests"]
        assert detail.status_code == 200
        assert detail.json()["description"] == "General-purpose worker."
        assert detail.json()["capabilities"] == ["builds", "tests"]

    def test_get_agent_hides_unaddressable_profile_by_default(self, client, app) -> None:
        app.state.resolver = AgentResolver(
            {
                "task-worker": AgentConfig(
                    name="task-worker",
                    project="aperture-labs",
                    worktree_discovery=False,
                    spawnable_as_target=False,
                )
            },
            {},
        )

        hidden = client.get("/api/v1/agents/task-worker")
        visible = client.get(
            "/api/v1/agents/task-worker?include_unaddressable=true"
        )

        assert hidden.status_code == 404
        assert visible.status_code == 200
        assert visible.json()["spawnable_as_target"] is False
        assert visible.json()["spawnable"] is False

    def test_machine_routes_include_metadata_defaults(self, client, app) -> None:
        machine = MachineConfig(key="host-a", display_name="Host A")
        app.state.resolver = AgentResolver({}, {"host-a": machine})

        detail = client.get("/api/v1/machines/host-a")

        assert detail.status_code == 200
        assert detail.json()["description"] == ""
        assert detail.json()["capabilities"] == []

    def test_session_alias_reuses_canonical_identity(
        self, client, app,
    ) -> None:
        app.state.resolver = AgentResolver(
            {
                "Pretty Name": AgentConfig(
                    name="Pretty Name",
                    aliases=["stable-name"],
                    project="project-a",
                ),
            },
            {},
        )
        manager: SessionManager = app.state.session_manager
        session = Session(
            "session-alias", "alias-session",
            SpawnTarget(type="local", cwd="/repo"),
            "Pretty Name", caller_id="caller-a",
        )
        session.status = SessionStatus.IDLE
        manager._sessions[session.session_id] = session

        resp = client.post(
            "/api/v1/sessions",
            json={"agent": "STABLE-NAME", "caller_id": "caller-a"},
        )
        assert resp.status_code == 201
        assert resp.json()["session_id"] == "session-alias"
        assert len(manager._sessions) == 1


class TestWorktreeRoutes:
    """Worktree discovery + session linkage."""

    def _seed_worktree(self, agent_name: str, wt_id: str) -> None:
        """Seed the discovery cache singleton with one worktree entry."""
        from agent_bridge.routes import worktrees as wt_routes

        entry = wt_routes._WorktreeEntry(
            id=wt_id,
            agent_name=agent_name,
            machine=agent_name,
            path=f"/wt/{wt_id}",
            branch=f"worktree/{wt_id}",
            status="active",
        )
        wt_routes.get_cache()._cache = {agent_name: [entry]}

    def teardown_method(self) -> None:
        """Reset the module-singleton cache between tests."""
        from agent_bridge.routes import worktrees as wt_routes

        wt_routes.get_cache()._cache = {}

    def test_worktree_links_to_latest_session(self, client, app) -> None:
        wt_id = "anomalous-potato-wsl-20250101-120000-link"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        session = Session("sess-link-1", "calm-river", target, "test-agent")
        session.status = SessionStatus.IDLE
        session.turn_count = 3
        mgr._sessions[session.session_id] = session

        resp = client.get("/api/v1/worktrees")
        assert resp.status_code == 200
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["session_id"] == "sess-link-1"
        assert entry["session_status"] == "idle"
        assert entry["session_turn_count"] == 3
        assert entry["session_live"] is True

    def test_worktree_without_session_has_null_linkage(self, client) -> None:
        wt_id = "anomalous-potato-wsl-20250101-130000-nolink"
        self._seed_worktree("test-agent", wt_id)

        resp = client.get("/api/v1/worktrees")
        assert resp.status_code == 200
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["session_id"] is None
        assert entry["session_status"] is None
        assert entry["session_turn_count"] == 0
        assert entry["session_live"] is False

    def test_stopped_session_is_not_live(self, client, app) -> None:
        wt_id = "anomalous-potato-wsl-20250101-140000-stopped"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        session = Session("sess-stopped-1", "old-pine", target, "test-agent")
        session.status = SessionStatus.STOPPED
        session.turn_count = 5
        mgr._sessions[session.session_id] = session

        resp = client.get("/api/v1/worktrees")
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["session_id"] == "sess-stopped-1"
        assert entry["session_status"] == "stopped"
        assert entry["session_live"] is False

    def test_worktree_linkage_includes_acp_session_id(self, client, app) -> None:
        wt_id = "anomalous-potato-wsl-20250101-150000-acp"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        session = Session("sess-acp-1", "lone-mesa", target, "test-agent")
        session.status = SessionStatus.IDLE
        session.acp_session_id = "acp-uuid-abcdef"
        mgr._sessions[session.session_id] = session

        resp = client.get("/api/v1/worktrees")
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["session_id"] == "sess-acp-1"
        assert entry["acp_session_id"] == "acp-uuid-abcdef"
        assert entry["durable_session_id"] == "acp-uuid-abcdef"

    def test_worktree_linkage_durable_session_id_falls_back_when_acp_unknown(
        self, client, app,
    ) -> None:
        """A brand-new session with no ACP id captured yet -- durable_session_id
        degrades to the bridge's own (non-durable) session_id rather than
        going unresolvable."""
        wt_id = "anomalous-potato-wsl-20250101-150000-noacp"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        session = Session("sess-no-acp-1", "lone-mesa", target, "test-agent")
        session.status = SessionStatus.IDLE
        mgr._sessions[session.session_id] = session

        resp = client.get("/api/v1/worktrees")
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["acp_session_id"] is None
        assert entry["durable_session_id"] == "sess-no-acp-1"

    def test_worktree_without_mux_reports_no_interactive_cli(self, client) -> None:
        """Default (no mux session) decorates as interactive_cli=none (#1883)."""
        wt_id = "anomalous-potato-wsl-20250101-150500-nomux"
        self._seed_worktree("test-agent", wt_id)

        resp = client.get("/api/v1/worktrees")
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["mux_session"] is False
        assert entry["interactive_cli"] == "none"

    def test_mux_held_worktree_decorates_interactive_cli(self, client) -> None:
        """An attached wt-<id> mux session -> interactive_cli=held (#1883)."""
        from agent_bridge.routes import worktrees as wt_routes

        wt_id = "anomalous-potato-wsl-20250101-150600-held"
        entry = wt_routes._WorktreeEntry(
            id=wt_id, agent_name="test-agent", machine="test-agent",
            path=f"/wt/{wt_id}", branch=f"worktree/{wt_id}", status="active",
            mux_session=True, mux_clients=1, mux_attached=True,
        )
        wt_routes.get_cache()._cache = {"test-agent": [entry]}

        resp = client.get("/api/v1/worktrees")
        got = resp.json()["groups"]["test-agent"][0]
        assert got["mux_session"] is True
        assert got["mux_attached"] is True
        assert got["interactive_cli"] == "held"

    def test_mux_detached_worktree_is_at_rest(self) -> None:
        """A detached mux session -> interactive_cli=at-rest (running, unwatched)."""
        from agent_bridge.routes import worktrees as wt_routes

        entry = wt_routes._WorktreeEntry(
            id="wt", agent_name="a", machine="a", path="/wt", branch="b",
            status="active", mux_session=True, mux_clients=0, mux_attached=False,
        )
        assert entry.interactive_cli_state() == "at-rest"
        assert entry.to_dict()["interactive_cli"] == "at-rest"

    def test_mux_unknown_attachment_defaults_to_held(self) -> None:
        """Unknown attachment (psmux fallback) is treated as held (safest)."""
        from agent_bridge.routes import worktrees as wt_routes

        entry = wt_routes._WorktreeEntry(
            id="wt", agent_name="a", machine="a", path="/wt", branch="b",
            status="active", mux_session=True, mux_clients=None, mux_attached=None,
        )
        assert entry.interactive_cli_state() == "held"

    def test_parse_worktree_list_reads_mux_details(self) -> None:
        """_parse_worktree_list threads mux_details fields from list --json."""
        from agent_bridge.routes import worktrees as wt_routes

        raw = (
            '{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
            ' "branch": "b1", "status": "active", "mux_session": true,'
            ' "mux_clients": 2, "mux_attached": true}]}'
        )
        entries = wt_routes._parse_worktree_list(raw, "test-agent")
        assert len(entries) == 1
        assert entries[0].mux_session is True
        assert entries[0].mux_clients == 2
        assert entries[0].mux_attached is True
        assert entries[0].interactive_cli_state() == "held"

    def test_parse_worktree_list_reads_taxonomy_marks(self) -> None:
        """#2668: _parse_worktree_list threads interface/origin/picker_hidden
        from ``agent-worktrees list --json`` and to_dict re-exposes them."""
        from agent_bridge.routes import worktrees as wt_routes

        raw = (
            '{"version": 1, "worktrees": ['
            '{"id": "u1", "path": "/u1", "branch": "b", "status": "active",'
            ' "interface": "acp", "origin": "user", "picker_hidden": false},'
            '{"id": "d1", "path": "/d1", "branch": "b", "status": "active",'
            ' "interface": "acp", "origin": "delegate", "picker_hidden": true}]}'
        )
        entries = wt_routes._parse_worktree_list(raw, "test-agent")
        by_id = {e.id: e for e in entries}
        # Operator-owned ACP session: shown.
        assert by_id["u1"].origin == "user"
        assert by_id["u1"].interface == "acp"
        assert by_id["u1"].picker_hidden is False
        assert by_id["u1"].to_dict()["origin"] == "user"
        assert by_id["u1"].to_dict()["picker_hidden"] is False
        # Agent-spawned (delegate): hidden.
        assert by_id["d1"].origin == "delegate"
        assert by_id["d1"].picker_hidden is True

    def test_parse_worktree_list_taxonomy_defaults_when_absent(self) -> None:
        """An older agent-worktrees runtime omits the marks -> degrade to
        None/shown so the cockpit shows everything (today's behavior)."""
        from agent_bridge.routes import worktrees as wt_routes

        raw = ('{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
               ' "branch": "b", "status": "active"}]}')
        entries = wt_routes._parse_worktree_list(raw, "test-agent")
        assert entries[0].origin is None
        assert entries[0].interface is None
        assert entries[0].picker_hidden is False
        assert entries[0].to_dict()["picker_hidden"] is False

    def test_parse_worktree_list_reads_status_core(self) -> None:
        """#2956: _parse_worktree_list threads the status-core disposition
        (follow_up/summary) + derived live pulse from ``agent-worktrees list
        --json`` and to_dict re-exposes them for the cockpit."""
        from agent_bridge.routes import worktrees as wt_routes

        raw = (
            '{"version": 1, "worktrees": ['
            '{"id": "f1", "path": "/f1", "branch": "b", "status": "active",'
            ' "follow_up": true, "summary": "Phase 8 left; PR open",'
            ' "status_note_at": "2026-07-16 09:00:00",'
            ' "live_intent": "Wiring the cockpit render",'
            ' "live_intent_at": "2026-07-16T16:00:00Z", "live_intent_idle": false}]}'
        )
        e = wt_routes._parse_worktree_list(raw, "test-agent")[0]
        assert e.follow_up is True
        assert e.summary == "Phase 8 left; PR open"
        assert e.live_intent == "Wiring the cockpit render"
        assert e.live_intent_at == "2026-07-16T16:00:00Z"
        assert e.live_intent_idle is False
        d = e.to_dict()
        assert d["follow_up"] is True
        assert d["summary"] == "Phase 8 left; PR open"
        assert d["status_note_at"] == "2026-07-16 09:00:00"
        assert d["live_intent"] == "Wiring the cockpit render"
        assert d["live_intent_at"] == "2026-07-16T16:00:00Z"
        assert d["live_intent_idle"] is False

    def test_parse_worktree_list_status_core_defaults_when_absent(self) -> None:
        """Older runtime omits the status-core fields -> disposition unset,
        pulse absent (the cockpit simply shows no overlay)."""
        from agent_bridge.routes import worktrees as wt_routes

        raw = ('{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
               ' "branch": "b", "status": "active"}]}')
        e = wt_routes._parse_worktree_list(raw, "test-agent")[0]
        assert e.follow_up is False
        assert e.summary is None
        assert e.live_intent is None
        assert e.live_intent_idle is False
        d = e.to_dict()
        assert d["follow_up"] is False
        assert d["summary"] is None
        assert d["live_intent"] is None

    def test_parse_worktree_list_reads_closure_descriptor(self) -> None:
        """worktree-finality-and-obligations (Phase 5): _parse_worktree_list
        threads the raw ``closure`` descriptor from ``list --json --classify``
        opaquely (this route does not interpret version/final-ness -- that's
        the cockpit consumer's job via ``prune.interpret_descriptor_payload``,
        since a cross-machine crawl may reach a different agent-worktrees
        version)."""
        from agent_bridge.routes import worktrees as wt_routes

        closure = {
            "version": 1,
            "label": "FINAL",
            "closure": {"final": True},
            "action": {"disposition": "safe", "bucket": "clean"},
        }
        raw = json.dumps({
            "version": 1,
            "worktrees": [{
                "id": "w1", "path": "/w1", "branch": "b", "status": "active",
                "closure": closure,
            }],
        })
        e = wt_routes._parse_worktree_list(raw, "test-agent")[0]
        assert e.closure == closure
        assert e.to_dict()["closure"] == closure

    def test_parse_worktree_list_closure_absent_when_not_classified(self) -> None:
        """Absent when the crawl didn't run --classify (or hit an older
        runtime) -- the cockpit falls back to legacy fields, never guesses."""
        from agent_bridge.routes import worktrees as wt_routes

        raw = ('{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
               ' "branch": "b", "status": "active"}]}')
        e = wt_routes._parse_worktree_list(raw, "test-agent")[0]
        assert e.closure is None
        assert e.to_dict()["closure"] is None

    def test_resume_worktree_with_no_session_404s(self, client) -> None:
        self._seed_worktree("test-agent", "anomalous-potato-wsl-20250101-160000-empty")
        resp = client.post(
            "/api/v1/worktrees/anomalous-potato-wsl-20250101-160000-empty/resume",
        )
        assert resp.status_code == 404

    def test_resume_worktree_returns_already_live_session(
        self, client, app,
    ) -> None:
        wt_id = "anomalous-potato-wsl-20250101-170000-live"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        session = Session("sess-live-1", "warm-bay", target, "test-agent")
        session.status = SessionStatus.IDLE
        session.acp_session_id = "acp-live-1"
        mgr._sessions[session.session_id] = session

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-live-1"
        assert data["acp_session_id"] == "acp-live-1"

    def test_resume_worktree_local_host_alive_trusts_idle_status(
        self, client, app,
    ) -> None:
        """(Phase 2, #6744) A local Session Host record whose pid is
        genuinely alive confirms the RUNNING/IDLE status -- the same happy
        path as the plain (no host record) case above, now with an explicit
        live check instead of an implicit "no record, so trust it"."""
        import os

        from agent_bridge.session_host.host_index import HostRecord

        wt_id = "anomalous-potato-wsl-20250101-170100-hostlive"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        session = Session("sess-hostlive-1", "sunny-cove", target, "test-agent")
        session.status = SessionStatus.IDLE
        session.acp_session_id = "acp-hostlive-1"
        mgr._sessions[session.session_id] = session
        mgr._host_index.register(HostRecord(
            session_id=session.session_id, port=1,
            host_pid=os.getpid(), child_pid=os.getpid(), boundary="local",
        ))

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-hostlive-1"

    def test_resume_worktree_reclassifies_dead_local_host_to_cold(
        self, client, app,
    ) -> None:
        """(Phase 2, #6744) A session stuck at IDLE/RUNNING whose local
        Session Host child is confirmed dead (pid 0 -- never alive) must
        NOT be trusted as live -- it is reclassified to stopped and the
        route falls through to the normal resume path (here: the stopped
        session has no ACP id, so it starts a fresh session instead,
        exactly like the existing no-prior-session fresh-start case)."""
        from unittest.mock import AsyncMock, MagicMock
        import time

        from agent_bridge.session_host.host_index import HostRecord
        from agent_bridge.transport import SpawnTarget as _SpawnTarget

        wt_id = "anomalous-potato-wsl-20250101-170200-deadhost"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")
        app.state.resolver.resolve = MagicMock(
            return_value=_SpawnTarget(type="local")
        )

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        stale = Session("sess-deadhost-1", "faded-elm", target, "test-agent")
        stale.status = SessionStatus.IDLE
        # Deliberately no acp_session_id -- resume_session would refuse it
        # anyway, so the reclassify-to-stopped path must go through the
        # fresh-session fallback, not mgr.resume_session.
        mgr._sessions[stale.session_id] = stale
        mgr._db.create_session(
            stale.session_id, stale.name, "test-agent", "/wt", "local",
            SessionStatus.IDLE.value, time.time(),
        )
        mgr._host_index.register(HostRecord(
            session_id=stale.session_id, port=1,
            host_pid=0, child_pid=0, boundary="local",
        ))

        fresh_target = SpawnTarget(type="local", cwd=f"/wt/{wt_id}", worktree_id=wt_id)
        fresh = Session("fresh-sess-deadhost", "brisk-tide", fresh_target, "test-agent")
        fresh.status = SessionStatus.IDLE
        mgr.start_session = AsyncMock(return_value=fresh)

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "fresh-sess-deadhost"
        # The stale session's in-memory status was reclassified...
        assert stale.status == SessionStatus.STOPPED
        # ...and so was the persisted row (review #3142: asserting only the
        # in-memory object wouldn't catch a regression dropping the DB write).
        row = mgr._db.execute_read(
            "SELECT status FROM sessions WHERE id=?", (stale.session_id,)
        )
        assert row[0]["status"] == SessionStatus.STOPPED.value
        # The stale host record is reaped, not left to linger.
        assert mgr._host_index.get(stale.session_id) is None
        # The worktree-ownership reservation must follow the replacement
        # session, not still name the reclassified-stale one (review #3142).
        owner = mgr._db.get_worktree_ownership(wt_id)
        assert owner is not None
        assert owner["session_id"] == "fresh-sess-deadhost"

    def test_resume_worktree_falls_back_to_fresh_session(
        self, client, app,
    ) -> None:
        """If the stopped session can't be resumed, start a fresh one."""
        from unittest.mock import AsyncMock

        wt_id = "anomalous-potato-wsl-20250101-180000-fallback"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        stopped = Session("sess-dead-1", "cold-fern", target, "test-agent")
        stopped.status = SessionStatus.STOPPED
        stopped.acp_session_id = "acp-dead-1"
        mgr._sessions[stopped.session_id] = stopped

        fresh = Session("sess-fresh-1", "new-dawn", target, "test-agent")
        fresh.status = SessionStatus.IDLE
        fresh.acp_session_id = "acp-fresh-1"

        # resume_session blows up (ACP session gone); start_session succeeds.
        mgr.resume_session = AsyncMock(side_effect=RuntimeError("acp session gone"))
        mgr.start_session = AsyncMock(return_value=fresh)

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-fresh-1"
        assert data["acp_session_id"] == "acp-fresh-1"
        mgr.start_session.assert_awaited_once()

    def test_reservation_race_409_names_the_live_holder_not_resuming_session(
        self, client, app,
    ) -> None:
        """When ``reserve_worktree_ownership`` refuses (a live CLI raced the
        reservation), the 409's session_id must be the ACTUAL live CLI
        holder -- never the resuming session's own id. A reclaim caller
        fences on this id to confirm a stopped holder is really gone before
        forcing; the resuming session's id would trivially "match" every
        time and silently defeat that safety check."""
        wt_id = "anomalous-potato-wsl-20250101-190000-racedholder"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        stopped = Session("sess-resuming-1", "quiet-marsh", target, "test-agent")
        stopped.status = SessionStatus.STOPPED
        mgr._sessions[stopped.session_id] = stopped

        db = app.state.db
        db.reserve_worktree_ownership = lambda *a, **k: False
        db.register_live_session(
            "cli-real-holder", machine="test-agent", cwd=None,
            worktree_id=wt_id, repo=None, branch=None, pid=None, role=None,
            now=time.time(),
        )

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["reason"] == "live_cli_holds_worktree"
        assert detail["session_id"] == "cli-real-holder"
        assert detail["session_id"] != stopped.session_id

    def test_reservation_conflict_without_live_cli_reports_distinct_reason(
        self, client, app,
    ) -> None:
        """``reserve_worktree_ownership`` also refuses when another active
        ACP reservation owns the worktree, entirely without a live CLI. That
        must NOT be reported as ``live_cli_holds_worktree`` -- a reclaim
        caller treats that reason as "stop-and-force through the interactive
        CLI", which does nothing for an ACP ownership conflict and would
        otherwise run a pointless (and unfenced, since there is no CLI
        holder id) restart."""
        wt_id = "anomalous-potato-wsl-20250101-190100-acpowned"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt", worktree_id=wt_id)
        stopped = Session("sess-resuming-2", "still-pond", target, "test-agent")
        stopped.status = SessionStatus.STOPPED
        mgr._sessions[stopped.session_id] = stopped

        db = app.state.db
        db.reserve_worktree_ownership = lambda *a, **k: False
        # No live_session registered at all -- the refusal is purely an ACP
        # ownership conflict. Seed the reservation row directly.
        db.execute_write(
            "INSERT INTO worktree_ownership (worktree_id, session_id, "
            "reserved_at, updated_at) VALUES (?, ?, ?, ?)",
            (wt_id, "acp-other-owner", time.time(), time.time()),
        )

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["reason"] == "worktree_owned_by_another_session"
        assert detail["session_id"] == "acp-other-owner"

    def test_resume_worktree_does_not_bypass_provider_refresh_failure(
        self, client, app,
    ) -> None:
        """An unsafe provider target is never reused by fresh-start fallback."""
        from unittest.mock import AsyncMock

        from agent_bridge.session_manager import ProviderTargetRefreshError

        wt_id = "anomalous-potato-wsl-20250101-180000-provider"
        self._seed_worktree("test-agent", wt_id)

        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="command", worktree_id=wt_id)
        stopped = Session("sess-provider-1", "stale-bay", target, "test-agent")
        stopped.status = SessionStatus.STOPPED
        stopped.acp_session_id = "acp-provider-1"
        mgr._sessions[stopped.session_id] = stopped
        mgr.resume_session = AsyncMock(
            side_effect=ProviderTargetRefreshError("provider unavailable")
        )
        mgr.start_session = AsyncMock()

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")

        assert resp.status_code == 502
        assert resp.json()["detail"] == ProviderTargetRefreshError.public_message
        mgr.start_session.assert_not_awaited()

    # -- Worktree-scoped session reading (proxied to agent-worktrees) ------

    def _register_agent(self, app, agent_name: str) -> None:
        """Give the resolver a config for the seeded agent."""
        from unittest.mock import MagicMock

        from agent_bridge.agent_registry import AgentConfig

        if getattr(app.state, "resolver", None) is None:
            app.state.resolver = MagicMock(agents={})
        app.state.resolver.agents[agent_name] = AgentConfig(
            name=agent_name, project="test-chamber",
        )

    def test_list_worktree_sessions_proxies_to_agent(self, client, app) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-190000-sess"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = '{"sessions": [{"session_id": "s1", "worktree_id": "%s", "turn_count": 4}]}' % wt_id
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ) as mock_run:
            resp = client.get(f"/api/v1/worktrees/{wt_id}/sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["worktree_id"] == wt_id
        assert data["agent_name"] == "test-agent"
        assert data["sessions"][0]["session_id"] == "s1"
        # Verify it shelled out to the right subcommand.
        args = mock_run.call_args.args[-1]
        assert args == ["list-sessions", "--worktree", wt_id, "--json"]

    def test_list_worktree_sessions_forwards_head_session(self, client, app) -> None:
        # session-lifecycle Phase 4: the ground-layer envelope's asserted head is
        # forwarded so Neuron Forge resolves the current session head-first and
        # badges the rest "no longer current" (derive-dont-duplicate -- the bridge
        # keeps no head of its own).
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-190500-head"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = (
            '{"head_session": "s2", "sessions": ['
            '{"id": "s1", "is_head": false, "state": "handed-off"}, '
            '{"id": "s2", "is_head": true, "state": "active"}]}'
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.get(f"/api/v1/worktrees/{wt_id}/sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["head_session"] == "s2"
        by_id = {s["id"]: s for s in data["sessions"]}
        assert by_id["s2"]["is_head"] is True
        assert by_id["s1"]["state"] == "handed-off"

    def test_get_worktree_lineage_proxies(self, client, app) -> None:
        """The bridge shells to ``worktree-lineage`` (the already-bounded,
        fork-aware ground-layer surface) and forwards its graph verbatim,
        not the plain session list -- fork detection stays owned by
        agent-worktrees, not re-derived here."""
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-191500-fork"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = json.dumps({
            "surface": "worktree-lineage",
            "surface_version": 1,
            "worktree_id": wt_id,
            "head_session": "s3",
            "revisions": {"lifecycle": 5, "head": 4, "controller": 1},
            "sessions": [
                {"session_id": "s3", "is_head": True, "predecessor": "s2"},
                {"session_id": "s2", "is_head": False, "predecessor": "s1"},
            ],
            "handoffs": [
                {
                    "ordinal": 1,
                    "predecessor": "s1",
                    "state": "linked",
                    "candidate": "s2",
                },
                {
                    "ordinal": 2,
                    "predecessor": "s2",
                    "state": "pending",
                    "candidate": "s3",
                },
                {
                    "ordinal": 3,
                    "predecessor": "s2",
                    "state": "pending",
                    "candidate": "s4",
                },
            ],
            "controllers": [{"controller_session_id": "s0"}],
        })
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ) as mock_run:
            resp = client.get(f"/api/v1/worktrees/{wt_id}/lineage")

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert data["surface"] == "worktree-lineage"
        assert data["head_session"] == "s3"
        # The fork signal lives in `state`, not raw handoff count -- two
        # simultaneously-`pending` handoffs off the same predecessor.
        pending = [h for h in data["handoffs"] if h["state"] == "pending"]
        assert len(pending) == 2
        assert {h["candidate"] for h in pending} == {"s3", "s4"}
        args = mock_run.call_args.args[-1]
        assert args == ["worktree-lineage", "--worktree", wt_id, "--json"]

    def test_get_worktree_lineage_unknown_worktree_404s(self, client) -> None:
        resp = client.get("/api/v1/worktrees/does-not-exist/lineage")
        assert resp.status_code == 404

    def test_get_worktree_lineage_502_on_command_failure(
        self, client, app,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-191700-fail"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(f"/api/v1/worktrees/{wt_id}/lineage")
        assert resp.status_code == 502

    def test_list_worktree_sessions_head_absent_is_none(self, client, app) -> None:
        # A legacy ground-layer envelope without head_session -> null, not a KeyError.
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-190600-nohead"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = '{"sessions": [{"id": "s1"}]}'
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.get(f"/api/v1/worktrees/{wt_id}/sessions")
        assert resp.status_code == 200
        assert resp.json()["head_session"] is None
        resp = client.get("/api/v1/worktrees/does-not-exist/sessions")
        assert resp.status_code == 404

    def test_list_worktree_sessions_502_on_command_failure(
        self, client, app,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-191000-fail"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(f"/api/v1/worktrees/{wt_id}/sessions")
        assert resp.status_code == 502

    def test_list_worktree_sessions_resolves_archived_worktree(
        self, client, app,
    ) -> None:
        # session-worktree-archive-linkout: a worktree agent-worktrees has
        # tombstoned as "archived" (retire_record, #3015) never appears in
        # the live discovery cache (it only crawls `list --json`'s on-disk,
        # non-archived default). The owner-resolution fallback must still
        # find it via an explicit archived-record probe instead of 404ing.
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193000-archived"
        self._register_agent(app, "test-agent")
        # Deliberately NOT seeded into the live discovery cache.

        archive_probe_payload = (
            '{"worktrees": [{"id": "%s", "status": "archived"}]}' % wt_id
        )
        sessions_payload = '{"sessions": [{"id": "s1"}]}'

        async def _fake_run_for_agent(agent_name, config, resolver, args):
            if "--tracking-status" in args:
                assert args == [
                    "list", "--json", "--tracking-status", "archived",
                    "--all", "--worktree-id", wt_id,
                ]
                return archive_probe_payload
            assert args == ["list-sessions", "--worktree", wt_id, "--json"]
            return sessions_payload

        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(side_effect=_fake_run_for_agent),
        ):
            resp = client.get(f"/api/v1/worktrees/{wt_id}/sessions")

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert data["sessions"][0]["id"] == "s1"

    @pytest.mark.asyncio
    async def test_archive_probe_single_flight_coalesces_concurrent_lookups(
        self,
    ) -> None:
        # Concurrent callers for the same archived worktree_id must share
        # one in-flight probe (an N-agent subprocess/SSH fan-out), not each
        # trigger their own -- the same stampede _crawl_lock prevents for
        # the main discovery crawl.
        import asyncio as _asyncio

        from agent_bridge.agent_registry import AgentConfig, AgentResolver
        from agent_bridge.routes import worktrees as wt_routes

        wt_id = "anomalous-potato-wsl-20250101-194500-coalesce"
        resolver = AgentResolver(
            agents={"test-agent": AgentConfig(name="test-agent", project="test-chamber")},
            machines={},
        )
        call_count = 0

        async def _slow_run_for_agent(agent_name, config, resolver, args):
            nonlocal call_count
            call_count += 1
            await _asyncio.sleep(0.05)
            return '{"worktrees": [{"id": "%s", "status": "archived"}]}' % wt_id

        cache = wt_routes.WorktreeDiscoveryCache()
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=_slow_run_for_agent,
        ):
            results = await _asyncio.gather(
                cache.probe_archived(wt_id, resolver),
                cache.probe_archived(wt_id, resolver),
                cache.probe_archived(wt_id, resolver),
            )

        assert call_count == 1, "concurrent probes for the same id must coalesce"
        assert all(r == ("test-agent", resolver.agents["test-agent"]) for r in results)
        # Eviction runs via the task's done-callback, which is only guaranteed
        # to have fired -- not necessarily before gather() returns -- once the
        # event loop gets a further tick.
        await _asyncio.sleep(0)
        assert wt_id not in cache._archive_probe_inflight

    @pytest.mark.asyncio
    async def test_archive_probe_survives_a_cancelled_concurrent_caller(
        self,
    ) -> None:
        # A cancelled caller (client disconnect/timeout) must not cancel
        # the shared in-flight task out from under a concurrent caller
        # still waiting on the same worktree_id.
        import asyncio as _asyncio

        from agent_bridge.agent_registry import AgentConfig, AgentResolver
        from agent_bridge.routes import worktrees as wt_routes

        wt_id = "anomalous-potato-wsl-20250101-194600-shielded"
        resolver = AgentResolver(
            agents={"test-agent": AgentConfig(name="test-agent", project="test-chamber")},
            machines={},
        )

        async def _slow_run_for_agent(agent_name, config, resolver, args):
            await _asyncio.sleep(0.1)
            return '{"worktrees": [{"id": "%s", "status": "archived"}]}' % wt_id

        cache = wt_routes.WorktreeDiscoveryCache()
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=_slow_run_for_agent,
        ):
            doomed = _asyncio.ensure_future(cache.probe_archived(wt_id, resolver))
            survivor = _asyncio.ensure_future(cache.probe_archived(wt_id, resolver))
            await _asyncio.sleep(0.02)  # let both attach to the shared task
            doomed.cancel()
            with pytest.raises(_asyncio.CancelledError):
                await doomed
            result = await survivor

        assert result == ("test-agent", resolver.agents["test-agent"])

    @pytest.mark.asyncio
    async def test_archive_probe_returns_early_on_first_match(self) -> None:
        # A match from a fast agent must return without waiting for a
        # slow/unreachable agent to finish or time out.
        import asyncio as _asyncio

        from agent_bridge.agent_registry import AgentConfig, AgentResolver
        from agent_bridge.routes import worktrees as wt_routes

        wt_id = "anomalous-potato-wsl-20250101-194700-early-exit"
        resolver = AgentResolver(
            agents={
                "fast-agent": AgentConfig(name="fast-agent", project="test-chamber"),
                "slow-agent": AgentConfig(name="slow-agent", project="test-chamber"),
            },
            machines={},
        )
        slow_agent_awaited = _asyncio.Event()

        async def _fake_run_for_agent(agent_name, config, resolver, args):
            if agent_name == "fast-agent":
                return '{"worktrees": [{"id": "%s", "status": "archived"}]}' % wt_id
            try:
                await _asyncio.sleep(30)
            except _asyncio.CancelledError:
                slow_agent_awaited.set()
                raise
            return '{"worktrees": []}'

        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=_fake_run_for_agent,
        ):
            result = await _asyncio.wait_for(
                wt_routes._probe_archived_owner(wt_id, resolver), timeout=1,
            )
            # The slow agent's task should have been cancelled, not awaited
            # to completion, once the fast match returned.
            await _asyncio.wait_for(slow_agent_awaited.wait(), timeout=1)

        assert result == ("fast-agent", resolver.agents["fast-agent"])

    def test_get_worktree_session_transcript_proxies(self, client, app) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-192000-tx"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = (
            '{"session_id": "s9", "events": '
            '[{"type": "user.message", "text": "hi"}]}'
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ) as mock_run:
            resp = client.get(
                f"/api/v1/worktrees/{wt_id}/sessions/s9/transcript",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "s9"
        assert data["events"][0]["type"] == "user.message"
        args = mock_run.call_args.args[-1]
        assert args == ["session-transcript", "s9", "--json"]

    def test_get_transcript_unknown_worktree_404s(self, client) -> None:
        resp = client.get(
            "/api/v1/worktrees/does-not-exist/sessions/s1/transcript",
        )
        assert resp.status_code == 404

    def test_get_transcript_falls_through_to_cold_store_when_worktree_unknown(
        self, client, app,
    ) -> None:
        """No owning agent at all for this worktree_id -- a registered
        cold-store provider still answers instead of an immediate 404
        (Phase 2b, mirroring ``get_session``'s fallback)."""
        from agent_bridge.cold_store import ColdStoreSession

        mgr = app.state.session_manager
        cold = ColdStoreSession(
            session_id="archived-tx",
            status="ended",
            worktree_id="some-other-worktree",
            events=({"type": "user.message", "text": "hi from the archive"},),
        )
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=cold)
        ):
            resp = client.get(
                "/api/v1/worktrees/some-other-worktree/sessions/archived-tx/transcript",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "archived-tx"
        assert data["events"][0]["text"] == "hi from the archive"
        assert data["meta"]["read_only"] is True
        assert data["meta"]["at_rest"] is True
        assert data["meta"]["worktree_id"] == "some-other-worktree"

    def test_get_transcript_cold_store_empty_archive_is_not_a_404(
        self, client, app,
    ) -> None:
        """A cold-store hit with zero events is a legitimate found-but-empty
        archive, not a miss -- ``cold is not None`` is the identity match,
        not ``cold.events`` truthiness (a real empty transcript must not be
        treated the same as no provider answer at all)."""
        from agent_bridge.cold_store import ColdStoreSession

        mgr = app.state.session_manager
        cold = ColdStoreSession(
            session_id="archived-empty",
            status="ended",
            worktree_id="wt-empty-archive",
            events=(),
        )
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=cold)
        ):
            resp = client.get(
                "/api/v1/worktrees/wt-empty-archive/sessions/archived-empty/transcript",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == []
        assert data["meta"]["read_only"] is True
        assert data["meta"]["at_rest"] is True

    def test_get_transcript_cold_store_worktree_mismatch_is_rejected(
        self, client, app,
    ) -> None:
        """The cold-store contract is session-ID-keyed, not worktree-keyed --
        a provider answer for a *different* worktree_id than the URL asked
        for must not be accepted as this worktree's transcript."""
        from agent_bridge.cold_store import ColdStoreSession

        mgr = app.state.session_manager
        cold = ColdStoreSession(
            session_id="cross-worktree-session",
            status="ended",
            worktree_id="the-real-worktree",
            events=({"type": "user.message", "text": "wrong worktree"},),
        )
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=cold)
        ):
            resp = client.get(
                "/api/v1/worktrees/a-different-worktree"
                "/sessions/cross-worktree-session/transcript",
            )

        assert resp.status_code == 404

    def test_get_transcript_falls_through_to_cold_store_when_local_events_empty(
        self, client, app,
    ) -> None:
        """The owning agent is known and reachable, but its local
        session-state has nothing for this session (``session-transcript``
        answers an absent session with an empty list, not an error) -- the
        cold-store provider still gets a chance before we settle for empty."""
        from agent_bridge.cold_store import ColdStoreSession

        wt_id = "anomalous-potato-wsl-20250101-192500-tx-empty"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        mgr = app.state.session_manager
        cold = ColdStoreSession(
            session_id="s10",
            status="ended",
            worktree_id=wt_id,
            events=({"type": "user.message", "text": "from cold store"},),
        )
        with (
            patch(
                "agent_bridge.routes.worktrees._run_for_agent",
                new=AsyncMock(return_value='{"session_id": "s10", "events": []}'),
            ),
            patch.object(
                mgr, "fetch_cold_store_session", AsyncMock(return_value=cold)
            ),
        ):
            resp = client.get(
                f"/api/v1/worktrees/{wt_id}/sessions/s10/transcript",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_name"] == "test-agent"
        assert data["events"][0]["text"] == "from cold store"
        assert data["meta"]["at_rest"] is True

    def test_get_transcript_404s_when_no_cold_store_answer_either(
        self, client, app,
    ) -> None:
        mgr = app.state.session_manager
        with patch.object(
            mgr, "fetch_cold_store_session", AsyncMock(return_value=None)
        ):
            resp = client.get(
                "/api/v1/worktrees/does-not-exist/sessions/nowhere/transcript",
            )
        assert resp.status_code == 404

    def test_restart_worktree_copilot_proxies(self, client, app) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193000-restart"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = (
            '{"worktree_id": "%s", "had_session": true, '
            '"method": "graceful", "ok": true}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ) as mock_run:
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart")

        assert resp.status_code == 200
        data = resp.json()
        assert data["worktree_id"] == wt_id
        assert data["agent_name"] == "test-agent"
        assert data["had_session"] is True
        assert data["method"] == "graceful"
        assert data["ok"] is True
        # Graceful default -> no --no-graceful flag.
        args = mock_run.call_args.args[-1]
        assert args == ["restart", wt_id, "--json"]

    def test_restart_worktree_copilot_force_passes_no_graceful(
        self, client, app,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193100-force"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        payload = (
            '{"worktree_id": "%s", "had_session": true, '
            '"method": "hard", "ok": true}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ) as mock_run:
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart?force=true")

        assert resp.status_code == 200
        assert resp.json()["method"] == "hard"
        args = mock_run.call_args.args[-1]
        assert args == ["restart", wt_id, "--json", "--no-graceful"]

    def test_restart_worktree_unknown_worktree_404s(self, client) -> None:
        resp = client.post("/api/v1/worktrees/does-not-exist/restart")
        assert resp.status_code == 404

    def test_restart_worktree_502_on_command_failure(
        self, client, app,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193200-fail"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart")
        assert resp.status_code == 502

    def test_restart_invalidates_live_session_registration(
        self, client, app,
    ) -> None:
        """A successful take-over demotes any live registration for the worktree
        and drops its queued inbox messages (#2906 invalidate-on-take-over)."""
        import time
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193300-takeover"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        db = app.state.db
        now = time.time()
        db.register_live_session(
            "cli-live", machine="test-agent", cwd=None, worktree_id=wt_id,
            repo=None, branch=None, pid=None, role=None, now=now,
        )
        db.enqueue_live_message("cli-live", "op", "steer", now)

        payload = (
            '{"worktree_id": "%s", "had_session": true, '
            '"method": "graceful", "ok": true}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart")

        assert resp.status_code == 200
        # Take-over demotes the live registration to the terminal `taken-over`
        # state (#2912) so a killed predecessor cannot revive it.
        assert db.get_live_session("cli-live")["status"] == "taken-over"
        assert db.list_pending_live_messages("cli-live") == []
        assert db.list_fresh_live_sessions(wt_id, now=now) == []

    def test_restart_reports_failure_when_invalidation_raises(
        self, client, app,
    ) -> None:
        """The CLI was actually stopped (ok:true, had_session:true), but if
        the server-side live-session invalidation itself raises, the
        response must report failure (ok:false) rather than silently
        swallow it -- a caller trusting ok:true would force-resume past a
        registration that is still 'live'."""
        import time
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193310-invalidatefail"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        db = app.state.db
        now = time.time()
        db.register_live_session(
            "cli-live", machine="test-agent", cwd=None, worktree_id=wt_id,
            repo=None, branch=None, pid=None, role=None, now=now,
        )
        db.expire_live_sessions_for_worktree = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("db write failed")
        )

        payload = (
            '{"worktree_id": "%s", "had_session": true, '
            '"method": "graceful", "ok": true}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart")

        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_restart_failure_keeps_live_session(
        self, client, app,
    ) -> None:
        """A restart that reports ``ok:false`` did NOT terminate the CLI, so the
        live registration must be left intact (no premature invalidation)."""
        import time
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193400-noop"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        db = app.state.db
        now = time.time()
        db.register_live_session(
            "cli-live", machine="test-agent", cwd=None, worktree_id=wt_id,
            repo=None, branch=None, pid=None, role=None, now=now,
        )

        payload = (
            '{"worktree_id": "%s", "had_session": false, '
            '"method": "none", "ok": false}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart")

        assert resp.status_code == 200
        assert db.get_live_session("cli-live")["status"] == "live"

    def test_restart_ok_no_session_keeps_live_registration(
        self, client, app,
    ) -> None:
        """``ok:true, had_session:false`` is a no-op (no mux session existed
        to stop) -- it is NOT proof that whatever registered
        live_cli_holds_worktree (possibly a bare, un-muxed CLI) was actually
        terminated, so the live registration must be left intact."""
        import time
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193420-bare"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        db = app.state.db
        now = time.time()
        db.register_live_session(
            "cli-bare", machine="test-agent", cwd=None, worktree_id=wt_id,
            repo=None, branch=None, pid=None, role=None, now=now,
        )

        payload = (
            '{"worktree_id": "%s", "had_session": false, '
            '"method": "none", "ok": true}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/restart")

        assert resp.status_code == 200
        assert db.get_live_session("cli-bare")["status"] == "live"

    def test_restart_expected_holder_fences_invalidation(
        self, client, app,
    ) -> None:
        """``?expected_holder=`` scopes the invalidate-on-take-over to only
        that session id -- a different, genuinely live registration for the
        same worktree (a claimant that raced in) is left untouched (#2906
        race hardening)."""
        import time
        from unittest.mock import AsyncMock, patch

        wt_id = "anomalous-potato-wsl-20250101-193450-fenced"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")

        db = app.state.db
        now = time.time()
        db.register_live_session(
            "cli-original", machine="test-agent", cwd=None, worktree_id=wt_id,
            repo=None, branch=None, pid=None, role=None, now=now,
        )
        db.register_live_session(
            "cli-new-claimant", machine="test-agent", cwd=None,
            worktree_id=wt_id, repo=None, branch=None, pid=None, role=None,
            now=now + 0.5,
        )

        payload = (
            '{"worktree_id": "%s", "had_session": true, '
            '"method": "graceful", "ok": true}' % wt_id
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=payload),
        ):
            resp = client.post(
                f"/api/v1/worktrees/{wt_id}/restart?expected_holder=cli-original"
            )

        assert resp.status_code == 200
        assert db.get_live_session("cli-original")["status"] == "taken-over"
        assert db.get_live_session("cli-new-claimant")["status"] == "live"

    def test_resume_worktree_no_session_starts_fresh(self, client, app) -> None:
        """A worktree with no prior bridge session (e.g. just taken over, its
        interactive Copilot never persisted a session) starts a *fresh* owned
        session instead of 404-ing (#1683)."""
        from unittest.mock import AsyncMock, MagicMock

        from agent_bridge.transport import SpawnTarget

        wt_id = "anomalous-potato-wsl-20250101-193500-fresh"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")
        # resolver.resolve must yield a real SpawnTarget (replace() needs a
        # dataclass), not a bare MagicMock.
        app.state.resolver.resolve = MagicMock(
            return_value=SpawnTarget(type="local")
        )

        mgr = app.state.session_manager
        target = SpawnTarget(type="local", cwd=f"/wt/{wt_id}", worktree_id=wt_id)
        fresh = Session("fresh-sess-1", "brisk-vale", target, "test-agent")
        fresh.status = SessionStatus.IDLE
        mgr.start_session = AsyncMock(return_value=fresh)

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "fresh-sess-1"
        # spawned scoped to the worktree dir + id
        spawned_target = mgr.start_session.call_args.args[0]
        assert spawned_target.worktree_id == wt_id
        assert spawned_target.cwd == f"/wt/{wt_id}"
        assert mgr.start_session.call_args.kwargs["caller_id"] == wt_id

    def test_resume_worktree_fresh_session_reports_conflict_when_reservation_fails(
        self, client, app,
    ) -> None:
        """The pre-spawn live-holder recheck does not cover the spawn itself
        (mgr.start_session is the slow, awaited step) -- a genuinely
        different claimant can register during it. The post-spawn
        reservation attempt's result must be checked: a False result must
        surface as a 409 (with the raced-in fresh session cleaned up, not
        leaked), never silently return the fresh session as if it were
        safely owned (that would recreate the duplicate-controller
        race)."""
        from unittest.mock import AsyncMock, MagicMock

        from agent_bridge.transport import SpawnTarget

        wt_id = "anomalous-potato-wsl-20250101-193510-freshraced"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")
        app.state.resolver.resolve = MagicMock(
            return_value=SpawnTarget(type="local")
        )

        mgr = app.state.session_manager
        target = SpawnTarget(type="local", cwd=f"/wt/{wt_id}", worktree_id=wt_id)
        fresh = Session("fresh-sess-raced", "amber-loop", target, "test-agent")
        fresh.status = SessionStatus.IDLE

        db = app.state.db

        async def _start_session_races_in_a_claimant(*a, **k):
            # Simulate a different interactive CLI registering WHILE the
            # spawn is in flight (after the pre-spawn holder recheck, before
            # this returns) -- the exact window the fixed post-spawn
            # reservation check exists to catch.
            db.register_live_session(
                "cli-raced-in", machine="test-agent", cwd=None,
                worktree_id=wt_id, repo=None, branch=None, pid=None,
                role=None, now=time.time(),
            )
            return fresh

        mgr.start_session = AsyncMock(side_effect=_start_session_races_in_a_claimant)
        mgr.end_session = AsyncMock()
        db.reserve_worktree_ownership = lambda *a, **k: False

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["reason"] == "live_cli_holds_worktree"
        assert detail["session_id"] == "cli-raced-in"
        # The raced-in fresh session must not be leaked -- it's cleaned up
        # rather than left running unreserved alongside the real holder.
        mgr.end_session.assert_awaited_once_with("fresh-sess-raced", force=True)

    def test_resume_worktree_unknown_still_404s(self, client, app) -> None:
        """A worktree that is not discoverable at all (no session, not on disk)
        still 404s -- the fresh-start only rescues a *known* worktree (#1683)."""
        from unittest.mock import AsyncMock

        mgr = app.state.session_manager
        mgr.start_session = AsyncMock(
            side_effect=AssertionError("must not start a session for an "
                                       "unknown worktree")
        )
        resp = client.post("/api/v1/worktrees/does-not-exist-anywhere/resume")
        assert resp.status_code == 404

    def test_resume_worktree_cache_blind_still_succeeds(self, client, app) -> None:
        """(#6744) A worktree the fleet-wide discovery cache has never crawled
        (cache empty/stale, never seeded via ``_seed_worktree``) must still
        resume successfully via a targeted, live, single-worktree query --
        the cache is only ever a performance shortcut for "does this exist,"
        never the authority. Before the fix, ``_start_fresh_worktree_session``
        only consulted ``cache.get_all()`` and 404'd here identically to a
        genuinely-unknown worktree."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent_bridge.transport import SpawnTarget

        from agent_bridge.routes import worktrees as wt_routes

        wt_id = "anomalous-potato-wsl-20250101-193700-cacheblind"
        self._register_agent(app, "test-agent")
        # Deliberately do NOT seed the cache -- simulates a fresh daemon
        # start, or a worktree created after the last crawl.
        wt_routes.get_cache()._cache = {"test-agent": []}

        app.state.resolver.resolve = MagicMock(
            return_value=SpawnTarget(type="local")
        )
        mgr = app.state.session_manager
        target = SpawnTarget(type="local", cwd=f"/wt/{wt_id}", worktree_id=wt_id)
        fresh = Session("fresh-sess-cb", "quiet-mesa", target, "test-agent")
        fresh.status = SessionStatus.IDLE
        mgr.start_session = AsyncMock(return_value=fresh)

        live_payload = (
            '{"worktrees": [{"id": "%s", "path": "/wt/%s", '
            '"branch": "worktree/%s", "status": "active"}]}'
            % (wt_id, wt_id, wt_id)
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=live_payload),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "fresh-sess-cb"
        # The fresh session must be spawned using the *probed* entry's
        # path/id, not an empty/incorrect cwd (review #3121) -- mirrors the
        # cache-backed fresh-start assertion above.
        spawned_target = mgr.start_session.call_args.args[0]
        assert spawned_target.worktree_id == wt_id
        assert spawned_target.cwd == f"/wt/{wt_id}"
        assert mgr.start_session.call_args.kwargs["agent_name"] == "test-agent"

    def test_resume_worktree_cache_blind_probe_rejects_missing_path(
        self, client, app
    ) -> None:
        """(review #3121) A live probe match with no on-disk path (e.g. a
        reaped/tombstoned record the probe shouldn't have matched at all)
        must never be used to spawn a fresh session -- refuse (404), not
        proceed with a stale/empty ``cwd``."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent_bridge.transport import SpawnTarget
        from agent_bridge.routes import worktrees as wt_routes

        wt_id = "anomalous-potato-wsl-20250101-193800-nopath"
        self._register_agent(app, "test-agent")
        wt_routes.get_cache()._cache = {"test-agent": []}

        app.state.resolver.resolve = MagicMock(
            return_value=SpawnTarget(type="local")
        )
        mgr = app.state.session_manager
        mgr.start_session = AsyncMock(
            side_effect=AssertionError(
                "must not start a session for an entry with no path"
            )
        )

        # No "path" key at all -- parses to the empty-string default.
        live_payload = (
            '{"worktrees": [{"id": "%s", "branch": "worktree/%s", '
            '"status": "active"}]}' % (wt_id, wt_id)
        )
        with patch(
            "agent_bridge.routes.worktrees._run_for_agent",
            new=AsyncMock(return_value=live_payload),
        ):
            resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")

        assert resp.status_code == 404

    def test_resume_worktree_fresh_start_failed_status_502(
        self, client, app
    ) -> None:
        """A fresh start that connects-fails (SessionManager returns a FAILED
        session rather than raising) surfaces as 502, not a healthy 200 (#1683
        hardening)."""
        from unittest.mock import AsyncMock, MagicMock

        from agent_bridge.transport import SpawnTarget

        wt_id = "anomalous-potato-wsl-20250101-193600-failstart"
        self._seed_worktree("test-agent", wt_id)
        self._register_agent(app, "test-agent")
        app.state.resolver.resolve = MagicMock(
            return_value=SpawnTarget(type="local")
        )

        mgr = app.state.session_manager
        target = SpawnTarget(type="local", cwd=f"/wt/{wt_id}", worktree_id=wt_id)
        failed = Session("failed-sess-1", "dim-fen", target, "test-agent")
        failed.status = SessionStatus.FAILED
        mgr.start_session = AsyncMock(return_value=failed)

        resp = client.post(f"/api/v1/worktrees/{wt_id}/resume")
        assert resp.status_code == 502


class TestAcpAliasResolution:
    """Session routes accept the ACP session id as an alias key."""

    def test_get_session_by_acp_id(self, client, app) -> None:
        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt")
        session = Session("bridge-uuid-1", "swift-pine", target, "test-agent")
        session.status = SessionStatus.IDLE
        session.acp_session_id = "acp-alias-xyz"
        mgr._sessions[session.session_id] = session

        # Address the session by its ACP id rather than the bridge uuid.
        resp = client.get("/api/v1/sessions/acp-alias-xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "bridge-uuid-1"
        assert data["acp_session_id"] == "acp-alias-xyz"

    def test_get_session_by_bridge_uuid_still_works(self, client, app) -> None:
        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt")
        session = Session("bridge-uuid-2", "tall-oak", target, "test-agent")
        session.status = SessionStatus.IDLE
        session.acp_session_id = "acp-other"
        mgr._sessions[session.session_id] = session

        resp = client.get("/api/v1/sessions/bridge-uuid-2")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == "bridge-uuid-2"

    def test_unknown_ref_404s(self, client) -> None:
        resp = client.get("/api/v1/sessions/no-such-ref")
        assert resp.status_code == 404


class TestBackgroundTaskTeardownGate:
    """stop/end refuse (409) while a session hosts active background tasks,
    unless force=true; status surfaces active_background_tasks."""

    @staticmethod
    def _inject_busy_session(app, *, sid: str = "bg-sess-1"):
        mgr: SessionManager = app.state.session_manager
        target = SpawnTarget(type="local", cwd="/wt")
        session = Session(sid, "busy-bee", target, "test-agent")
        session.status = SessionStatus.IDLE
        client_mock = MagicMock()
        client_mock.is_running = True
        client_mock.cancel_prompt = AsyncMock()
        client_mock.shutdown = AsyncMock()
        client_mock.has_active_background_tasks = True
        client_mock.active_background_tasks = ["pr-daemon"]
        session.client = client_mock
        mgr._sessions[sid] = session
        return session

    def test_stop_returns_409_when_busy(self, client, app) -> None:
        self._inject_busy_session(app)
        resp = client.post("/api/v1/sessions/bg-sess-1/stop")
        assert resp.status_code == 409
        assert "background" in resp.json()["detail"].lower()

    def test_delete_returns_409_when_busy(self, client, app) -> None:
        self._inject_busy_session(app)
        resp = client.delete("/api/v1/sessions/bg-sess-1")
        assert resp.status_code == 409

    def test_force_stop_succeeds_when_busy(self, client, app) -> None:
        session = self._inject_busy_session(app)
        resp = client.post("/api/v1/sessions/bg-sess-1/stop?force=true")
        assert resp.status_code == 204
        assert session.status == SessionStatus.STOPPED

    def test_stop_reap_host_param_plumbs_through(self, client, app) -> None:
        # #2960: ?reap_host=true must reach mgr.stop_session(reap_host=True) so
        # a non-reattaching caller (the AI reviewer) frees the child on the spot
        # rather than after the idle-reaper TTL. Default stays reap_host=False.
        mgr: SessionManager = app.state.session_manager
        mgr.stop_session = AsyncMock()
        resp = client.post("/api/v1/sessions/whatever/stop?reap_host=true")
        assert resp.status_code == 204
        mgr.stop_session.assert_awaited_once_with(
            "whatever", force=False, reap_host=True
        )

    def test_stop_reap_host_defaults_false(self, client, app) -> None:
        mgr: SessionManager = app.state.session_manager
        mgr.stop_session = AsyncMock()
        resp = client.post("/api/v1/sessions/whatever/stop")
        assert resp.status_code == 204
        mgr.stop_session.assert_awaited_once_with(
            "whatever", force=False, reap_host=False
        )

    def test_stop_reap_host_composes_with_force(self, client, app) -> None:
        mgr: SessionManager = app.state.session_manager
        mgr.stop_session = AsyncMock()
        resp = client.post(
            "/api/v1/sessions/whatever/stop?force=true&reap_host=true"
        )
        assert resp.status_code == 204
        mgr.stop_session.assert_awaited_once_with(
            "whatever", force=True, reap_host=True
        )

    def test_force_delete_succeeds_when_busy(self, client, app) -> None:
        self._inject_busy_session(app)
        resp = client.delete("/api/v1/sessions/bg-sess-1?force=true")
        assert resp.status_code == 204
        assert app.state.session_manager.get_session("bg-sess-1") is None

    def test_status_surfaces_active_background_tasks(self, client, app) -> None:
        self._inject_busy_session(app)
        resp = client.get("/api/v1/sessions/bg-sess-1/status")
        assert resp.status_code == 200
        assert resp.json()["active_background_tasks"] == ["pr-daemon"]


class TestPendingQueue:
    """Durable send-or-queue routes (POST turns queue=true + queue CRUD, #4114)."""

    def _seed_running(self, app, session_id: str = "s1") -> SessionManager:
        import time as _t
        mgr: SessionManager = app.state.session_manager
        now = _t.time()
        mgr.db.create_session(
            session_id, "test", None, ".", "local", "running", now
        )
        target = SpawnTarget(type="local", cwd="/wt")
        session = Session(session_id, "busy-brook", target, "test-agent")
        session.status = SessionStatus.RUNNING
        mgr._sessions[session_id] = session
        return mgr

    def test_queue_true_on_busy_returns_202_and_persists(self, client, app) -> None:
        mgr = self._seed_running(app)
        resp = client.post(
            "/api/v1/sessions/s1/turns",
            json={"prompt": "follow-up", "queue": True, "caller_id": "op"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["queued"] is True
        assert body["queue_id"] is not None
        assert body["position"] == 1
        assert body["turn_index"] is None
        # durably persisted
        assert mgr.db.count_pending_prompts("s1") == 1

    def test_default_busy_still_409(self, client, app) -> None:
        """Without queue=true the legacy 409-on-busy contract is preserved."""
        self._seed_running(app)
        resp = client.post(
            "/api/v1/sessions/s1/turns", json={"prompt": "follow-up"},
        )
        assert resp.status_code == 409

    def test_get_queue_snapshot(self, client, app) -> None:
        self._seed_running(app)
        client.post(
            "/api/v1/sessions/s1/turns",
            json={"prompt": "one", "queue": True},
        )
        client.post(
            "/api/v1/sessions/s1/turns",
            json={"prompt": "two", "queue": True},
        )
        resp = client.get("/api/v1/sessions/s1/queue")
        assert resp.status_code == 200
        pending = resp.json()["pending"]
        assert [p["prompt"] for p in pending] == ["one", "two"]

    def test_delete_one_queued_prompt(self, client, app) -> None:
        mgr = self._seed_running(app)
        r = client.post(
            "/api/v1/sessions/s1/turns",
            json={"prompt": "drop me", "queue": True},
        )
        qid = r.json()["queue_id"]
        resp = client.delete(f"/api/v1/sessions/s1/queue/{qid}")
        assert resp.status_code == 204
        assert mgr.db.count_pending_prompts("s1") == 0
        # deleting again is a 404 miss
        assert client.delete(f"/api/v1/sessions/s1/queue/{qid}").status_code == 404

    def test_clear_whole_queue(self, client, app) -> None:
        mgr = self._seed_running(app)
        client.post(
            "/api/v1/sessions/s1/turns", json={"prompt": "a", "queue": True},
        )
        client.post(
            "/api/v1/sessions/s1/turns", json={"prompt": "b", "queue": True},
        )
        resp = client.delete("/api/v1/sessions/s1/queue")
        assert resp.status_code == 204
        assert mgr.db.count_pending_prompts("s1") == 0

    def test_queue_routes_on_unknown_session_404(self, client) -> None:
        assert client.get("/api/v1/sessions/nope/queue").status_code == 404
        assert client.delete("/api/v1/sessions/nope/queue").status_code == 404
        assert client.delete("/api/v1/sessions/nope/queue/1").status_code == 404


def test_worktree_discovery_crawl_is_single_flight() -> None:
    """Two overlapping ``crawl()`` calls spawn only ONE crawl's worth of work.

    Single-owner-slot / debounce guard (process-slot-ownership effort): a slow or
    stuck ``agent-worktrees list`` must never be answered by kicking a second,
    parallel crawl -- the exact repeat-spawn this fixes. The second concurrent
    crawl must coalesce onto the in-flight one, not re-run the per-agent work.
    """
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)

    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.worktree_discovery = True
    agent_cfg.host = None
    resolver = MagicMock()
    resolver.agents = {"local": agent_cfg}
    cache._resolver = resolver

    calls = {"n": 0}

    async def _slow_crawl_agent(name, config, res, *, classify=True):
        calls["n"] += 1
        await asyncio.sleep(0.2)  # simulate a slow / stuck `list`
        return []

    async def _run() -> None:
        with patch.object(cache, "_crawl_agent", side_effect=_slow_crawl_agent):
            # Fire two crawls concurrently; the second finds the lock held and
            # coalesces onto the first instead of starting a duplicate crawl.
            await asyncio.gather(cache.crawl(resolver), cache.crawl(resolver))

    asyncio.run(_run())

    assert calls["n"] == 1, f"expected single-flight crawl, got {calls['n']} crawls"
    assert cache.get_all() == {"local": []}


def test_worktree_discovery_crawl_if_empty_no_reentrant_deadlock() -> None:
    """``crawl_if_empty`` must populate the cache without deadlocking.

    It holds ``_crawl_lock`` and calls the unlocked ``_do_crawl`` body (not the
    re-locking ``crawl``); a regression that pointed it back at ``crawl`` would
    deadlock on the non-reentrant asyncio lock.
    """
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.worktree_discovery = True
    agent_cfg.host = None
    resolver = MagicMock()
    resolver.agents = {"local": agent_cfg}
    cache._resolver = resolver

    async def _crawl_agent(name, config, res, *, classify=True):
        return []

    async def _run() -> None:
        with patch.object(cache, "_crawl_agent", side_effect=_crawl_agent):
            await asyncio.wait_for(cache.crawl_if_empty(), timeout=5)

    asyncio.run(_run())
    assert cache.get_all() == {"local": []}


def test_crawl_if_empty_never_blocks_on_classify_budget() -> None:
    """worktree-finality-and-obligations (Phase 5): the FIRST, blocking crawl
    (``crawl_if_empty``) must never expose a synchronous caller to the longer
    classify timeout budget -- it always calls ``_crawl_agent`` with
    ``classify=False`` and backfills the descriptor via a separate
    fire-and-forget task, so a slow/old target can only stall the first
    response by the base (legacy) timeout, never classify's extended one."""
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.worktree_discovery = True
    agent_cfg.host = None
    resolver = MagicMock()
    resolver.agents = {"local": agent_cfg}
    cache._resolver = resolver

    seen_classify: list[bool] = []

    async def _fake_crawl_agent(name, config, res, *, classify=True):
        seen_classify.append(classify)
        return []

    async def _run() -> None:
        with patch.object(cache, "_crawl_agent", side_effect=_fake_crawl_agent):
            await asyncio.wait_for(cache.crawl_if_empty(), timeout=5)
            # Let the fire-and-forget backfill task get scheduled/run too.
            await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert seen_classify[0] is False, "first-paint crawl must skip --classify"
    assert True in seen_classify, "backfill task must still request --classify"


def test_stop_cancels_pending_classify_backfill() -> None:
    """worktree-finality-and-obligations (Phase 5): ``stop()`` must cancel and
    await any in-flight classify-backfill task (not just the periodic
    ``_task``) -- otherwise a backfill scheduled by an on-demand request can
    keep running (and mutating the cache) past application shutdown
    (#discussion_r4004943069)."""
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.worktree_discovery = True
    agent_cfg.host = None
    resolver = MagicMock()
    resolver.agents = {"local": agent_cfg}
    cache._resolver = resolver

    started = asyncio.Event()

    async def _hanging_crawl_agent(name, config, res, *, classify=True):
        if classify:
            started.set()
            await asyncio.sleep(10)  # would outlive the test if not cancelled
        return []

    async def _run() -> None:
        with patch.object(cache, "_crawl_agent", side_effect=_hanging_crawl_agent):
            await asyncio.wait_for(cache.crawl_if_empty(), timeout=5)
            assert len(cache._backfill_tasks) == 1
            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.wait_for(cache.stop(), timeout=5)
            assert len(cache._backfill_tasks) == 0

    asyncio.run(_run())


def test_crawl_agent_falls_back_when_classify_unsupported() -> None:
    """worktree-finality-and-obligations (Phase 5): an older agent-worktrees
    runtime that rejects ``--classify`` must not lose discovery entirely --
    ``_crawl_agent`` detects the specific "unrecognized arguments" stderr and
    retries with the legacy (unclassified) args instead of returning []."""
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.host = None
    resolver = MagicMock()

    calls: list[list[str]] = []

    async def _fake_run_local_ex(project, args=None, *, timeout=None):
        calls.append(args)
        if args and "--classify" in args:
            return None, "aw: error: unrecognized arguments: --classify"
        return (
            '{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
            ' "branch": "b", "status": "active"}]}',
            "",
        )

    async def _run() -> None:
        with patch(
            "agent_bridge.routes.worktrees._run_local_ex",
            side_effect=_fake_run_local_ex,
        ):
            return await cache._crawl_agent("local", agent_cfg, resolver)

    entries = asyncio.run(_run())
    assert len(calls) == 2  # classify attempt, then the legacy fallback
    assert "--classify" in calls[0]
    assert "--classify" not in calls[1]
    assert len(entries) == 1
    assert entries[0].id == "w1"
    assert entries[0].closure is None  # legacy list never carried one
    assert "local" in cache._classify_unsupported


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="PATHEXT-based .cmd/.ps1 shim resolution is a Windows-only "
    "concern; shutil.which() only tries PATHEXT suffixes on win32. On "
    "POSIX the real shim is the bare, executable filename, already covered "
    "by test_resolve_local_binstub_falls_back_to_path_when_no_local_shim.",
)
def test_resolve_local_binstub_uses_pathext_aware_resolution(tmp_path, monkeypatch) -> None:
    """Regression: ``asyncio.create_subprocess_exec`` never consults
    Windows' PATHEXT the way
    a shell does, so an extensionless ``Path(...).exists()`` check silently
    fell through to a bare project name that could never actually spawn on
    Windows (``FileNotFoundError: [WinError 2]``) even though the installed
    ``<project>.cmd``/``.ps1`` shim was right there -- this zeroed every local
    Windows worktree-discovery crawl while WSL/Linux crawls (no extension
    needed) kept working. ``_resolve_local_binstub`` must resolve through
    :func:`shutil.which`, which performs the same PATHEXT-aware lookup a shell
    would, on every platform."""
    import os
    from pathlib import Path

    from agent_bridge.routes.worktrees import _resolve_local_binstub

    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    shim = bin_dir / "aperture-labs.cmd"
    shim.write_text("@echo off\n")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    resolved = _resolve_local_binstub("aperture-labs")
    assert os.path.normcase(resolved) == os.path.normcase(str(shim))


def test_resolve_local_binstub_falls_back_to_path_when_no_local_shim(
    tmp_path, monkeypatch,
) -> None:
    """No ``~/.local/bin/<project>`` shim -> fall back to whatever ``PATH``
    resolves (still via the PATHEXT-aware :func:`shutil.which`), never the
    bare, unresolved project name."""
    import shutil
    from pathlib import Path

    from agent_bridge.routes.worktrees import _resolve_local_binstub

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.setattr(shutil, "which", lambda name: f"/resolved/{name}" if name == "aperture-labs" else None)
    resolved = _resolve_local_binstub("aperture-labs")
    assert resolved == "/resolved/aperture-labs"


def test_apply_bound_charter_layers_charter_spawn_shape() -> None:
    """agent-bridge-worktree-native-agents (Phase 3): a worktree's bound
    charter borrows its own launch shape (copilot_path/mcp_servers/env, and
    its own copilot_args APPENDED after the venue's) onto the
    venue-resolved target -- host/cwd/project stay the venue's, and the
    venue's own args (e.g. --plugin-dir staging, --allow-all) survive."""
    from agent_bridge.routes.worktrees import _WorktreeEntry, _apply_bound_charter
    from agent_bridge.transport import SpawnTarget

    venue_target = SpawnTarget(
        type="local", cwd="/wt/path", project="aperture-labs",
        copilot_args=["--plugin-dir", "/staged/plugin"],
        env={"BASE": "1"},
    )
    charter_config = MagicMock()
    charter_config.managed = False
    charter_config.copilot_path = "/opt/special/copilot"
    charter_config.copilot_args = ["--agent", "board-sweep-worker"]
    charter_config.mcp_servers = [{"name": "gitea"}]
    charter_config.env = {"CHARTER": "1"}
    resolver = MagicMock()
    resolver.canonical_agent_name.return_value = "board-sweep-worker"
    resolver.agents = {"board-sweep-worker": charter_config}
    entry = _WorktreeEntry(
        id="wt1", agent_name="lambda-core-wsl", machine="lambda-core",
        path="/wt/path", branch="b", status="active",
        bound_agent="board-sweep-worker",
    )

    result = _apply_bound_charter(venue_target, resolver, entry, "wt1")

    assert result.copilot_path == "/opt/special/copilot"
    assert result.copilot_args == [
        "--plugin-dir", "/staged/plugin", "--agent", "board-sweep-worker",
    ]
    assert result.mcp_servers == [{"name": "gitea"}]
    assert result.env == {"BASE": "1", "CHARTER": "1"}
    assert result.cwd == "/wt/path"
    assert result.project == "aperture-labs"


def test_apply_bound_charter_unresolvable_charter_degrades_to_venue_default() -> None:
    """An unbound worktree, or a bound_agent that no longer resolves in the
    registry, must never fail the spawn -- it just uses the venue default."""
    from agent_bridge.routes.worktrees import _WorktreeEntry, _apply_bound_charter
    from agent_bridge.transport import SpawnTarget

    venue_target = SpawnTarget(type="local", cwd="/wt/path")
    resolver = MagicMock()
    resolver.canonical_agent_name.return_value = None

    unbound = _WorktreeEntry(
        id="wt1", agent_name="lambda-core-wsl", machine="lambda-core",
        path="/wt/path", branch="b", status="active",
    )
    assert _apply_bound_charter(venue_target, resolver, unbound, "wt1") is venue_target

    stale = _WorktreeEntry(
        id="wt2", agent_name="lambda-core-wsl", machine="lambda-core",
        path="/wt/path", branch="b", status="active",
        bound_agent="retired-charter",
    )
    resolver.agents = {}
    result = _apply_bound_charter(venue_target, resolver, stale, "wt2")
    assert result is venue_target


def test_apply_bound_charter_managed_charter_degrades_to_venue_default(
    caplog,
) -> None:
    """A managed=true charter is explicitly non-spawnable (mirrors
    AgentResolver._resolve_static's own guard) -- binding a worktree to one
    must never smuggle its launch shape into a spawn anyway. Exactly one
    accurate warning is logged, not a second misleading "not found" one."""
    import logging

    from agent_bridge.routes.worktrees import _WorktreeEntry, _apply_bound_charter
    from agent_bridge.transport import SpawnTarget

    venue_target = SpawnTarget(type="local", cwd="/wt/path")
    managed_charter = MagicMock()
    managed_charter.managed = True
    resolver = MagicMock()
    resolver.canonical_agent_name.return_value = "intelligence-dampener-reviewer"
    resolver.agents = {"intelligence-dampener-reviewer": managed_charter}
    entry = _WorktreeEntry(
        id="wt3", agent_name="lambda-core-wsl", machine="lambda-core",
        path="/wt/path", branch="b", status="active",
        bound_agent="intelligence-dampener-reviewer",
    )

    with caplog.at_level(logging.WARNING, logger="agent-bridge"):
        result = _apply_bound_charter(venue_target, resolver, entry, "wt3")
    assert result is venue_target
    assert len(caplog.records) == 1
    assert "managed" in caplog.records[0].message
    assert "not found" not in caplog.records[0].message


def test_crawl_agent_skips_classify_probe_once_cached_unsupported() -> None:
    """Follow-up (review): once an agent is known to reject --classify, a
    later classify=True crawl (periodic sweep) must not repeat the failed
    probe -- it goes straight to the legacy args, so a long-lived bridge
    daemon doesn't double its discovery work for a permanently old
    runtime."""
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    cache._classify_unsupported.add("local")
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.host = None
    resolver = MagicMock()

    calls: list[list[str]] = []

    async def _fake_run_local_ex(project, args=None, *, timeout=None):
        calls.append(args)
        return (
            '{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
            ' "branch": "b", "status": "active"}]}',
            "",
        )

    async def _run() -> None:
        with patch(
            "agent_bridge.routes.worktrees._run_local_ex",
            side_effect=_fake_run_local_ex,
        ):
            return await cache._crawl_agent("local", agent_cfg, resolver, classify=True)

    entries = asyncio.run(_run())
    assert len(calls) == 1, "must not attempt --classify for a cached-unsupported agent"
    assert "--classify" not in calls[0]
    assert len(entries) == 1


def test_crawl_agent_falls_back_when_classify_times_out() -> None:
    """A classify pass that fails/times out for any OTHER reason (not the
    specific unsupported-flag stderr) also falls back to the legacy args
    rather than losing the agent's rows -- e.g. a large/slow target
    genuinely exceeding the classify budget."""
    import asyncio

    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.host = None
    resolver = MagicMock()

    calls: list[list[str]] = []

    async def _fake_run_local_ex(project, args=None, *, timeout=None):
        calls.append(args)
        if args and "--classify" in args:
            return None, ""  # timeout: no stderr, just None
        return (
            '{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
            ' "branch": "b", "status": "active"}]}',
            "",
        )

    async def _run() -> None:
        with patch(
            "agent_bridge.routes.worktrees._run_local_ex",
            side_effect=_fake_run_local_ex,
        ):
            return await cache._crawl_agent("local", agent_cfg, resolver)

    entries = asyncio.run(_run())
    assert len(calls) == 2
    assert len(entries) == 1
    assert entries[0].id == "w1"


def test_crawl_agent_uses_classify_budget_and_keeps_closure() -> None:
    """The happy path: --classify succeeds on the first try (using the longer
    _CLASSIFY_CMD_TIMEOUT budget) and its closure descriptor survives."""
    import asyncio

    from agent_bridge.routes import worktrees as wt_routes
    from agent_bridge.routes.worktrees import WorktreeDiscoveryCache

    cache = WorktreeDiscoveryCache(interval=0)
    agent_cfg = MagicMock()
    agent_cfg.project = "aw"
    agent_cfg.host = None
    resolver = MagicMock()

    seen_timeouts: list[float | None] = []

    async def _fake_run_local_ex(project, args=None, *, timeout=None):
        seen_timeouts.append(timeout)
        return (
            '{"version": 1, "worktrees": [{"id": "w1", "path": "/w1",'
            ' "branch": "b", "status": "active",'
            ' "closure": {"version": 1, "label": "FINAL"}}]}',
            "",
        )

    async def _run() -> None:
        with patch(
            "agent_bridge.routes.worktrees._run_local_ex",
            side_effect=_fake_run_local_ex,
        ):
            return await cache._crawl_agent("local", agent_cfg, resolver)

    entries = asyncio.run(_run())
    assert seen_timeouts == [wt_routes._CLASSIFY_CMD_TIMEOUT]
    assert entries[0].closure == {"version": 1, "label": "FINAL"}
