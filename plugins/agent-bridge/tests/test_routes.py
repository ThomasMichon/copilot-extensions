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
        wt_id = "lambda-core-wsl-20250101-120000-link"
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
        wt_id = "lambda-core-wsl-20250101-130000-nolink"
        self._seed_worktree("test-agent", wt_id)

        resp = client.get("/api/v1/worktrees")
        assert resp.status_code == 200
        entry = resp.json()["groups"]["test-agent"][0]
        assert entry["session_id"] is None
        assert entry["session_status"] is None
        assert entry["session_turn_count"] == 0
        assert entry["session_live"] is False

    def test_stopped_session_is_not_live(self, client, app) -> None:
        wt_id = "lambda-core-wsl-20250101-140000-stopped"
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
        wt_id = "lambda-core-wsl-20250101-150000-acp"
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

    def test_resume_worktree_with_no_session_404s(self, client) -> None:
        self._seed_worktree("test-agent", "lambda-core-wsl-20250101-160000-empty")
        resp = client.post(
            "/api/v1/worktrees/lambda-core-wsl-20250101-160000-empty/resume",
        )
        assert resp.status_code == 404

    def test_resume_worktree_returns_already_live_session(
        self, client, app,
    ) -> None:
        wt_id = "lambda-core-wsl-20250101-170000-live"
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

    def test_resume_worktree_falls_back_to_fresh_session(
        self, client, app,
    ) -> None:
        """If the stopped session can't be resumed, start a fresh one."""
        from unittest.mock import AsyncMock

        wt_id = "lambda-core-wsl-20250101-180000-fallback"
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
