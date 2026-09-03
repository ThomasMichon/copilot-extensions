"""Tests for HTTP API routes."""

from __future__ import annotations

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

    async def _slow_crawl_agent(name, config, res):
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

    async def _crawl_agent(name, config, res):
        return []

    async def _run() -> None:
        with patch.object(cache, "_crawl_agent", side_effect=_crawl_agent):
            await asyncio.wait_for(cache.crawl_if_empty(), timeout=5)

    asyncio.run(_run())
    assert cache.get_all() == {"local": []}
