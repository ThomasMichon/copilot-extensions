"""Tests for HTTP API routes."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.db import Database
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    """Prevent auto-discovery from picking up real projects.yaml."""
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
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


class TestHealthEndpoint:
    """Health check route."""

    def test_health(self, app) -> None:
        with TestClient(app) as c:
            resp = c.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


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


class TestSessionRoutes:
    """Session CRUD routes."""

    def test_list_sessions_empty(self, client) -> None:
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200
        assert resp.json()["sessions"] == []

    def test_get_nonexistent_session(self, client) -> None:
        resp = client.get("/api/v1/sessions/nonexistent")
        assert resp.status_code == 404

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

        # Verify it shows in list
        resp2 = client.get("/api/v1/sessions")
        assert len(resp2.json()["sessions"]) == 1

    def test_stop_nonexistent(self, client) -> None:
        resp = client.post("/api/v1/sessions/nonexistent/stop")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client) -> None:
        resp = client.delete("/api/v1/sessions/nonexistent")
        assert resp.status_code == 404

    def test_resume_nonexistent(self, client) -> None:
        resp = client.post("/api/v1/sessions/nonexistent/resume")
        assert resp.status_code == 404

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
                "worktree_id": "lambda-core-wsl-20250101-120000-abc1",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["status"] == "idle"

        # Verify the resolved target got worktree_id set
        spawn_call = mock_spawn.call_args
        target = spawn_call.args[0]
        assert target.worktree_id == "lambda-core-wsl-20250101-120000-abc1"

    @patch("agent_bridge.session_manager.spawn")
    @patch("agent_bridge.session_manager.AcpClient")
    def test_start_session_with_agent_and_target_dir(
        self, mock_acp_cls, mock_spawn, client, app,
    ) -> None:
        """Session roll: start session with agent + target_dir."""
        from agent_bridge.transport import SpawnTarget

        mock_resolver = MagicMock()
        mock_resolver.resolve_async = AsyncMock(return_value=SpawnTarget(
            type="local", cwd="/original/dir",
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
