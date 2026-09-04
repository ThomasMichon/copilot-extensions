"""Tests for SessionManager lifecycle operations."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.protocol import FAILED_ACP_HANDSHAKE_FAULT
from agent_bridge.routes.sessions import _session_info
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import (
    _STALL_AFTER_S,
    RemoteHostRecoveryPendingError,
    Session,
    SessionManager,
    _default_cwd,
    _venue_workspace_cwd,
)
from agent_bridge.transport import SpawnTarget


def test_venue_workspace_cwd():
    """A venue's workspace_folder becomes the ACP session cwd; absent -> None."""
    assert _venue_workspace_cwd(
        SpawnTarget(type="command", venue={"workspace_folder": "/workspaces/odsp-web"})
    ) == "/workspaces/odsp-web"
    # No venue / no workspace / blank -> None (falls back to target.cwd/default).
    assert _venue_workspace_cwd(SpawnTarget(type="command")) is None
    assert _venue_workspace_cwd(
        SpawnTarget(type="command", venue={"security_profile": "trusted"})
    ) is None
    assert _venue_workspace_cwd(
        SpawnTarget(type="command", venue={"workspace_folder": "  "})
    ) is None


def test_running_session_projects_at_rest_from_terminal_event_tail() -> None:
    session = Session(
        "sid",
        "name",
        SpawnTarget(type="local", cwd="/tmp/x"),
    )
    session.status = SessionStatus.RUNNING
    session.event_log = EventLog()
    session.event_log.append("user_message", {"content": "work"})
    assert session.is_at_rest() is False
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    assert session.is_at_rest() is True
    info = _session_info(session)
    assert info.status == SessionStatus.IDLE
    assert info.at_rest is True
    assert info.liveness is None
    session.event_log.append("user_message", {"content": "more work"})
    assert session.is_at_rest() is False
    info = _session_info(session)
    assert info.status == SessionStatus.RUNNING
    assert info.at_rest is False


def test_active_tool_prevents_at_rest_projection() -> None:
    session = Session(
        "sid",
        "name",
        SpawnTarget(type="local", cwd="/tmp/x"),
    )
    session.status = SessionStatus.RUNNING
    session.event_log = EventLog()
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    session.event_log.append(
        "tool_call_start", {"tool_call_id": "tool-1", "name": "shell"}
    )
    assert session.is_at_rest() is False


def test_nested_tool_does_not_prevent_at_rest_projection() -> None:
    session = Session(
        "sid",
        "name",
        SpawnTarget(type="local", cwd="/tmp/x"),
    )
    session.status = SessionStatus.RUNNING
    session.event_log = EventLog()
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    session.event_log.append(
        "tool_call_start",
        {
            "tool_call_id": "tool-1",
            "name": "nested",
            "agent_id": "sub-1",
        },
    )
    assert session.is_at_rest() is True


def _mock_agent_proc():
    """Create a mock AgentProcess with a mock subprocess."""
    proc = MagicMock()
    proc.proc = MagicMock()
    proc.proc.pid = 12345
    proc.proc.returncode = None
    proc.proc.stdin = MagicMock()
    proc.proc.stdout = MagicMock()
    proc.proc.stderr = MagicMock()
    proc.proc.stderr.readline = AsyncMock(return_value=b"")
    return proc


@pytest.mark.asyncio
async def test_failed_process_launch_reaps_wrapper_tree(session_manager, monkeypatch):
    """A stage-7 failure must not leave a provider/SSH holder alive."""

    class FakeAgentProcess:
        def __init__(self):
            self.proc = MagicMock()
            self.proc.pid = 4242
            self.alive = True
            self.kill = AsyncMock(side_effect=self._mark_dead)

        async def _mark_dead(self):
            self.alive = False

        @property
        def pid(self):
            return self.proc.pid

    agent_proc = FakeAgentProcess()
    client = MagicMock()
    client.auto_approve = True
    client.start = AsyncMock(side_effect=TimeoutError)
    client.shutdown = AsyncMock()
    client.stderr_tail.return_value = "provider launch stalled"

    monkeypatch.setattr(
        "agent_bridge.session_manager.spawn",
        AsyncMock(return_value=agent_proc),
    )
    monkeypatch.setattr(
        "agent_bridge.session_manager.AcpClient",
        MagicMock(return_value=client),
    )

    session = await session_manager.start_session(
        SpawnTarget(
            type="command",
            spawn_command=["provider", "exec", "--stdio"],
            venue={
                "kind": "container",
                "workspace_folder": "/workspaces/repo",
            },
        ),
        agent_name="container:repo-1",
    )

    assert session.status is SessionStatus.FAILED
    client.shutdown.assert_awaited_once()
    agent_proc.kill.assert_awaited_once()
    assert agent_proc.alive is False


class TestDefaultCwd:
    """Fallback cwd derivation for ACP session creation."""

    def test_windows_without_user_uses_drive_root(self) -> None:
        target = SpawnTarget(type="ssh", host="host-a", ssh_shell="pwsh")
        assert _default_cwd(target) == "C:\\"
        assert _default_cwd(target) != "C:\\Users\\root"

    def test_windows_root_user_uses_drive_root(self) -> None:
        target = SpawnTarget(type="ssh", host="host-a", user="root", ssh_shell="pwsh")
        assert _default_cwd(target) == "C:\\"

    def test_posix_root_user_uses_root_home(self) -> None:
        target = SpawnTarget(type="ssh", host="host-a", user="root", ssh_shell="bash")
        assert _default_cwd(target) == "/root"

    def test_posix_without_user_uses_filesystem_root(self) -> None:
        target = SpawnTarget(type="ssh", host="host-a", ssh_shell="bash")
        assert _default_cwd(target) == "/"


class TestUsageModelMerge:
    """`_handle_usage_update` must PRESERVE the applied model across the normal
    (model-less) usage updates copilot emits, so a dispatched agent's model stays
    verifiable in ``status`` (dotfiles#790/#1274 WS1-model)."""

    def _sm_session(self, tmp_path):
        sm = SessionManager(Database(tmp_path / "s.db"))
        s = Session("sid", "name", SpawnTarget(type="local", cwd="/tmp/x"))
        return sm, s

    def test_model_none_update_does_not_wipe_applied_model(self, tmp_path) -> None:
        sm, s = self._sm_session(tmp_path)
        # The client emits the applied model as a model-only usage_update.
        sm._handle_usage_update(s, {"model": "claude-opus-4.8"})
        assert s.usage_model == "claude-opus-4.8"
        # copilot's per-turn UsageUpdate carries model=None -- must NOT overwrite.
        sm._handle_usage_update(s, {"context_size": 100, "context_used": 10, "model": None})
        assert s.usage_model == "claude-opus-4.8"
        assert s.context_size == 100
        assert s.context_used == 10

    def test_model_only_update_preserves_context(self, tmp_path) -> None:
        sm, s = self._sm_session(tmp_path)
        sm._handle_usage_update(s, {"context_size": 200, "context_used": 50, "model": None})
        # A later model-only update (no context keys) must not wipe context.
        sm._handle_usage_update(s, {"model": "gpt-5.6-sol"})
        assert s.context_size == 200
        assert s.context_used == 50
        assert s.usage_model == "gpt-5.6-sol"


@pytest.fixture
def _patch_spawn():
    """Patch spawn to return a mock AgentProcess."""
    with patch("agent_bridge.session_manager.spawn") as mock_spawn:
        mock_spawn.return_value = _mock_agent_proc()
        yield mock_spawn


@pytest.fixture
def _patch_acp(mock_acp_client):
    """Patch AcpClient construction to return a mock.

    Session Hosts are always on (dotfiles#1478), so a local ``start_session``
    now connects through ``_connect_via_session_host`` (a survivable host +
    loopback socket) rather than the classic ``spawn`` + ``AcpClient`` path.
    That machinery can't stand up in a unit test, so we also stub the host
    connect to hand back the mock client + a stable acp session id. (``resume``
    still uses the classic ``spawn``/``load_session`` path -- ``_patch_spawn``
    covers that -- so resume tests keep asserting ``load_session``.)
    """
    async def _fake_host_connect(self, target, **kwargs):
        return mock_acp_client, mock_acp_client.acp_session_id

    with patch("agent_bridge.session_manager.AcpClient") as mock_cls, \
            patch.object(
                SessionManager, "_connect_via_session_host", _fake_host_connect
            ):
        mock_cls.return_value = mock_acp_client
        yield mock_cls


class TestStartSession:
    """Session start lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_session(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        assert session.status == SessionStatus.IDLE
        assert session.acp_session_id == "acp-test-123"
        assert session.client is not None

    @pytest.mark.asyncio
    async def test_start_persists_to_db(
        self, session_manager, tmp_db, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        row = tmp_db.get_session(session.session_id)
        assert row is not None
        assert row["status"] == "idle"
        assert row["acp_session_id"] == "acp-test-123"

    @pytest.mark.asyncio
    async def test_start_failure_marks_failed(
        self, session_manager, spawn_target, _patch_spawn
    ) -> None:
        with patch("agent_bridge.session_manager.AcpClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.start = AsyncMock(side_effect=RuntimeError("Connection refused"))
            mock_cls.return_value = mock_client

            session = await session_manager.start_session(spawn_target)
            assert session.status == SessionStatus.FAILED
            assert any(
                event.event == "session_state_changed"
                and event.data["status"] == SessionStatus.FAILED.value
                for event in session.event_log.get_events()
            )


class TestConcurrencyGuard:
    """Single-session-per-CodeSpace concurrency guard."""

    @staticmethod
    def _command_target() -> SpawnTarget:
        """A command-type (CodeSpace/provider) target."""
        return SpawnTarget(
            type="command",
            cwd="/workspaces/repo",
            spawn_command=["gh", "codespace", "ssh", "-c", "cs-name"],
        )

    @pytest.mark.asyncio
    async def test_command_agent_blocks_second_session(
        self, session_manager, _patch_spawn, _patch_acp
    ) -> None:
        from agent_bridge.session_manager import SessionConflictError

        first = await session_manager.start_session(
            self._command_target(), agent_name="codespace:cs-name",
        )
        assert first.status == SessionStatus.IDLE

        with pytest.raises(SessionConflictError) as excinfo:
            await session_manager.start_session(
                self._command_target(), agent_name="codespace:cs-name",
            )
        assert excinfo.value.existing_session_id == first.session_id
        assert excinfo.value.agent_name == "codespace:cs-name"

    @pytest.mark.asyncio
    async def test_command_agent_blocks_across_callers(
        self, session_manager, _patch_spawn, _patch_acp
    ) -> None:
        """Different local callers still map to one CodeSpace session."""
        from agent_bridge.session_manager import SessionConflictError

        first = await session_manager.start_session(
            self._command_target(), agent_name="codespace:cs-name",
            caller_id="worktree-A",
        )
        with pytest.raises(SessionConflictError):
            await session_manager.start_session(
                self._command_target(), agent_name="codespace:cs-name",
                caller_id="worktree-B",
            )
        assert first.caller_id == "worktree-A"

    @pytest.mark.asyncio
    async def test_stopped_session_still_blocks(
        self, session_manager, _patch_spawn, _patch_acp
    ) -> None:
        """A STOPPED (resumable) session still owns the CodeSpace."""
        from agent_bridge.session_manager import SessionConflictError

        first = await session_manager.start_session(
            self._command_target(), agent_name="codespace:cs-name",
        )
        await session_manager.stop_session(first.session_id)
        assert first.status == SessionStatus.STOPPED

        with pytest.raises(SessionConflictError) as excinfo:
            await session_manager.start_session(
                self._command_target(), agent_name="codespace:cs-name",
            )
        assert excinfo.value.existing_session_id == first.session_id

    @pytest.mark.asyncio
    async def test_stopped_session_without_acp_is_replaced(
        self, session_manager, _patch_spawn, _patch_acp
    ) -> None:
        """A zero-turn failed start is replaced with freshly resolved target data."""
        first = await session_manager.start_session(
            self._command_target(),
            agent_name="codespace:cs-name",
        )
        await session_manager.stop_session(first.session_id)
        first.acp_session_id = None
        session_manager._db.update_session_acp_id(first.session_id, None)
        refreshed = self._command_target()
        refreshed.cwd = "/workspaces/current"

        second = await session_manager.start_session(
            refreshed,
            agent_name="codespace:cs-name",
        )

        assert second.status == SessionStatus.IDLE
        assert second.session_id != first.session_id
        assert second.target.cwd == "/workspaces/current"
        assert first.session_id not in {
            session.session_id for session in session_manager.list_sessions()
        }

    @pytest.mark.asyncio
    async def test_ended_session_does_not_block(
        self, session_manager, _patch_spawn, _patch_acp
    ) -> None:
        """Once ended, a new session for the same CodeSpace is allowed."""
        first = await session_manager.start_session(
            self._command_target(), agent_name="codespace:cs-name",
        )
        await session_manager.end_session(first.session_id)

        second = await session_manager.start_session(
            self._command_target(), agent_name="codespace:cs-name",
        )
        assert second.status == SessionStatus.IDLE
        assert second.session_id != first.session_id

    @pytest.mark.asyncio
    async def test_local_agents_not_guarded(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """Local/SSH agents allow concurrent sessions (separate checkouts)."""
        first = await session_manager.start_session(
            spawn_target, agent_name="local-agent",
        )
        second = await session_manager.start_session(
            spawn_target, agent_name="local-agent",
        )
        assert first.session_id != second.session_id
        assert first.status == SessionStatus.IDLE
        assert second.status == SessionStatus.IDLE


async def _start_codespace_session(tmp_db, monkeypatch):
    # Isolate the acp_command from the relay prelude: the real
    # ``_resolve_relay_launch_env`` prepends an auth-scrub/relay setup string to
    # the remote command, which is orthogonal to what these tests assert. Pin it
    # to an empty prelude (its own tests cover the prelude itself).
    monkeypatch.setattr(
        "agent_bridge.session_manager._resolve_relay_launch_env",
        lambda *args, **kwargs: ("", None),
    )
    transport = object()
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        lambda *args, **kwargs: SimpleNamespace(transport=transport),
    )
    monkeypatch.setattr(
        "agent_bridge.session_manager._resolve_remote_ai_plugin_dirs",
        AsyncMock(return_value=[]),
    )
    manager = SessionManager(tmp_db)
    captured: dict[str, list[str] | None] = {}

    async def fake_connect(self, target, **kwargs):
        captured["remote_child_argv"] = kwargs.get("remote_child_argv")
        client = MagicMock()
        client.is_running = True
        client.pid = 12345
        return client, "acp-test-123"

    monkeypatch.setattr(SessionManager, "_connect_via_session_host", fake_connect)
    acp_command = "cd /workspaces/example && copilot --acp --stdio --allow-all-tools"
    target = SpawnTarget(
        type="command",
        cwd="/workspaces/example",
        codespace={
            "name": "example-codespace",
            "repo": "example/repo",
            "acp_command": acp_command,
            "workspace_folder": "/workspaces/example",
        },
    )

    await manager.start_session(target, agent_name="codespace:example")
    return acp_command, captured["remote_child_argv"]


class TestCodespaceSessionHostAcpCommand:
    """The Session-Host CodeSpace dispatch passes copilot's acp_command through
    unchanged (no ``--model`` launch flags).

    Model/effort are NOT set via CLI flags -- copilot ignores them in ``--acp``
    mode. The dispatched agent's model is set after the session exists, via ACP
    ``session/set_config_option`` in ``AcpClient._apply_model_config``
    (dotfiles#790). See ``tests/test_acp_client.py`` for that behavior.
    """

    @pytest.mark.asyncio
    async def test_acp_command_passed_through_unchanged(self, tmp_db, monkeypatch) -> None:
        acp_command, remote_argv = await _start_codespace_session(tmp_db, monkeypatch)

        assert remote_argv == ["bash", "-lc", acp_command]


@pytest.mark.asyncio
async def test_trusted_container_selects_remote_session_host(
    tmp_db,
    monkeypatch,
) -> None:
    prepared = {
        "name": "odsp-web-1",
        "workspace_folder": "/workspaces/odsp-web",
        "ssh": {"host_alias": "agent-container-odsp-web-1"},
        "remote_command": (
            "source /tmp/agent-containers/env-123 && "
            "exec bash -lc 'copilot --acp --stdio'"
        ),
        "remote_env": "/tmp/agent-containers/env-123",
        "acp_command": (
            "cd /workspaces/odsp-web && copilot --acp --stdio"
        ),
        "reverse_forwards": ["9857:127.0.0.1:61234"],
        "state_command": ["agent-containers", "session-host-state", "odsp-web-1"],
    }
    captured = {}

    async def fake_prepare(target, relay_port):
        captured["prepare"] = (target, relay_port)
        return prepared

    async def fake_ensure(target):
        captured["ensure"] = target

    async def fake_cleanup(target, result):
        captured["cleanup"] = (target, result)

    transport = object()

    def fake_build(target, **kwargs):
        captured["build"] = (target, kwargs)
        return SimpleNamespace(transport=transport)

    async def fake_plugin_dirs(selected_transport, venue_name, repo_dir):
        captured["plugin_resolve"] = (
            selected_transport,
            venue_name,
            repo_dir,
        )
        return ["/workspaces/odsp-web/.ai/odsp-web-agent"]

    async def fake_connect(self, target, **kwargs):
        captured["connect"] = kwargs
        client = MagicMock()
        client.is_running = True
        client.pid = 12345
        return client, "acp-container-123"

    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "prepare_container_session_host",
        fake_prepare,
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.ensure_container_ready",
        fake_ensure,
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "cleanup_container_session_host",
        fake_cleanup,
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.build_container_spawner",
        fake_build,
    )
    monkeypatch.setattr(
        SessionManager, "_connect_via_session_host", fake_connect,
    )
    monkeypatch.setattr(
        "agent_bridge.session_manager._resolve_remote_ai_plugin_dirs",
        fake_plugin_dirs,
    )
    monkeypatch.setattr(
        SessionManager, "_acquire_container_lock", lambda *args: None,
    )
    monkeypatch.setattr(
        SessionManager, "_release_container_lock", lambda *args: None,
    )
    monkeypatch.setattr(
        "agent_bridge.relay_state.get_live_relay_port", lambda: 61234,
    )
    target = SpawnTarget(
        type="command",
        spawn_command=["agent-containers", "exec", "--stdio", "odsp-web-1"],
        container={
            "name": "odsp-web-1",
            "workspace_folder": "/workspaces/odsp-web",
            "security_profile": "trusted",
            "ssh": {"host_alias": "agent-container-odsp-web-1"},
            "provider_command": ["agent-containers"],
            "acp_command": (
                "cd /workspaces/odsp-web && copilot --acp --stdio"
            ),
        },
    )
    manager = SessionManager(tmp_db)

    session = await manager.start_session(
        target,
        agent_name="container:odsp-web-1",
    )

    assert session.status == SessionStatus.IDLE
    assert captured["ensure"]["name"] == "odsp-web-1"
    assert captured["prepare"][1] == 61234
    assert captured["build"][1]["prepared"] is prepared
    assert captured["connect"]["remote_child_argv"] == [
        "bash",
        "-lc",
        ". /tmp/agent-containers/env-123; "
        "rm -f /tmp/agent-containers/env-123; "
        "cd /workspaces/odsp-web && copilot --acp --stdio "
        "--plugin-dir=/workspaces/odsp-web/.ai/odsp-web-agent",
    ]
    assert captured["connect"]["remote_cwd"] == "/workspaces/odsp-web"
    assert captured["plugin_resolve"] == (
        transport,
        "container:odsp-web-1",
        "/workspaces/odsp-web",
    )
    assert captured["cleanup"][1] is prepared


@pytest.mark.asyncio
async def test_container_cleanup_exception_does_not_fail_successful_start(
    tmp_db,
    monkeypatch,
) -> None:
    prepared = {
        "name": "example-1",
        "workspace_folder": "/workspaces/example",
        "ssh": {"host_alias": "agent-container-example-1"},
        "acp_command": "copilot --acp --stdio",
        "state_command": ["agent-containers", "session-host-state", "example-1"],
    }

    async def fake_connect(self, target, **kwargs):
        client = MagicMock()
        client.is_running = True
        client.pid = 12345
        return client, "acp-container-123"

    async def fail_cleanup(target, result):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "prepare_container_session_host",
        AsyncMock(return_value=prepared),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.ensure_container_ready",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "cleanup_container_session_host",
        fail_cleanup,
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.build_container_spawner",
        lambda *args, **kwargs: SimpleNamespace(transport=object()),
    )
    monkeypatch.setattr(
        "agent_bridge.session_manager._resolve_remote_ai_plugin_dirs",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        SessionManager,
        "_connect_via_session_host",
        fake_connect,
    )
    monkeypatch.setattr(
        SessionManager, "_acquire_container_lock", lambda *args: None,
    )
    monkeypatch.setattr(
        SessionManager, "_release_container_lock", lambda *args: None,
    )
    monkeypatch.setattr(
        "agent_bridge.relay_state.get_live_relay_port",
        lambda: 61234,
    )
    target = SpawnTarget(
        type="command",
        container={
            "name": "example-1",
            "workspace_folder": "/workspaces/example",
            "provider_command": ["agent-containers"],
            "acp_command": "copilot --acp --stdio",
        },
    )

    session = await SessionManager(tmp_db).start_session(
        target,
        agent_name="container:example-1",
    )

    assert session.status == SessionStatus.IDLE
    assert session.acp_session_id == "acp-container-123"


@pytest.mark.asyncio
async def test_codespace_handshake_failure_releases_claim(
    tmp_db,
    monkeypatch,
) -> None:
    released = []
    monkeypatch.setattr(
        "agent_bridge.session_manager._claim_codespace",
        lambda *_args, **_kwargs: ("ok", ""),
    )
    monkeypatch.setattr(
        "agent_bridge.session_manager._release_codespace_claim",
        lambda *args: released.append(args) or True,
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        lambda *args, **kwargs: SimpleNamespace(transport=object()),
    )
    monkeypatch.setattr(
        "agent_bridge.session_manager._resolve_remote_ai_plugin_dirs",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        SessionManager,
        "_connect_via_session_host",
        AsyncMock(side_effect=RuntimeError("handshake rejected")),
    )
    target = SpawnTarget(
        type="command",
        caller_worktree="caller-1",
        codespace={
            "name": "example-codespace",
            "repo": "example/repo",
            "acp_command": "copilot --acp --stdio",
            "workspace_folder": "/workspaces/example",
        },
    )

    session = await SessionManager(tmp_db).start_session(
        target,
        agent_name="codespace:example-codespace",
        caller_id="caller-1",
    )

    assert session.status == SessionStatus.FAILED
    assert released == [("example-codespace", "caller-1")]


@pytest.mark.asyncio
async def test_stopped_container_is_authoritative_remote_host_death(
    tmp_db,
    monkeypatch,
) -> None:
    class FakeSpawner:
        boundary = "container"

        async def can_inspect_without_wake(self):
            return False

        async def recover_record(self, session_id):
            raise AssertionError("stopped container must not be opened over SSH")

    class FakeIndex:
        def __init__(self):
            self.removed = []
            self.existing = SimpleNamespace(
                extra={"remote_authority_v2": True},
                resume_on_reattach=False,
            )

        def get(self, session_id):
            return self.existing

        def remove(self, session_id):
            self.removed.append(session_id)

        def register(self, record):
            raise AssertionError("dead container record must not be registered")

    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "build_container_spawner",
        lambda *args, **kwargs: FakeSpawner(),
    )
    manager = SessionManager(tmp_db)
    manager._host_index = FakeIndex()
    target = SpawnTarget(
        type="command",
        container={
            "name": "odsp-web-1",
            "ssh": {"host_alias": "agent-container-odsp-web-1-a1b2c3d4e5f6"},
            "provider_command": ["agent-containers"],
        },
    )
    session = Session("session-1", "container", target)
    session.acp_session_id = "acp-1"
    manager._sessions[session.session_id] = session

    recovered = await manager._recover_remote_host_records(allow_wake=True)

    assert recovered == 0
    assert manager._host_index.removed == ["session-1"]
    assert session.session_id not in manager._remote_recovery_inconclusive


@pytest.mark.asyncio
async def test_container_state_probe_failure_retains_remote_authority(
    tmp_db,
    monkeypatch,
) -> None:
    class UnknownSpawner:
        boundary = "container"

        async def can_inspect_without_wake(self):
            raise RuntimeError("Docker control plane unavailable")

    class FakeIndex:
        def __init__(self):
            self.removed = []
            self.existing = SimpleNamespace(
                extra={"remote_authority_v2": True},
                resume_on_reattach=False,
            )

        def get(self, session_id):
            return self.existing

        def remove(self, session_id):
            self.removed.append(session_id)

        def register(self, record):
            raise AssertionError("unknown authority must not be replaced")

    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "build_container_spawner",
        lambda *args, **kwargs: UnknownSpawner(),
    )
    manager = SessionManager(tmp_db)
    manager._host_index = FakeIndex()
    target = SpawnTarget(
        type="command",
        container={
            "name": "odsp-web-1",
            "ssh": {"host_alias": "agent-container-odsp-web-1-a1b2c3d4e5f6"},
            "provider_command": ["agent-containers"],
        },
    )
    session = Session("session-1", "container", target)
    session.acp_session_id = "acp-1"
    manager._sessions[session.session_id] = session

    recovered = await manager._recover_remote_host_records(allow_wake=True)

    assert recovered == 0
    assert manager._host_index.removed == []
    assert session.session_id in manager._remote_recovery_inconclusive


@pytest.mark.asyncio
async def test_failed_handshake_rollback_removes_remote_holders(
    tmp_db,
) -> None:
    calls = []

    class FakeResource:
        async def terminate(self):
            calls.append("terminate")

        async def aclose(self):
            calls.append("streams-close")

        async def close(self):
            calls.append("socket-close")

    class FakeSpawned:
        boundary = "container"

        async def aclose(self):
            calls.append("spawn-close")

    class FakeSpawner:
        async def abort_spawned(self, spawned, session_id):
            assert isinstance(spawned, FakeSpawned)
            assert session_id == "session-1"
            calls.append("remote-abort")
            return True

    manager = SessionManager(tmp_db)
    manager._forwards["session-1"] = object()
    manager._relays["session-1"] = [object()]
    result = {}

    confirmed = await manager._rollback_failed_host_launch(
        FakeSpawner(),
        FakeSpawned(),
        FakeResource(),
        FakeResource(),
        "session-1",
        result,
    )

    assert confirmed is True
    assert calls == [
        "terminate",
        "streams-close",
        "socket-close",
        "remote-abort",
        "spawn-close",
    ]
    assert result == {
        "host_process_removed": True,
        "child_process_removed": True,
        "remote_authority_removed": True,
        "forward_removed": True,
        "relay_removed": True,
    }


@pytest.mark.asyncio
async def test_finalize_failed_handshake_removes_session_and_target_lock(
    tmp_db,
) -> None:
    class FakeLock:
        released = False

        def release(self):
            self.released = True

    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={"name": "example-1"},
    )
    session = Session(
        "session-1",
        "failed",
        target,
        agent_name="container:example-1",
        caller_id="venue-parity:test",
    )
    session.status = SessionStatus.FAILED
    session.parity_fault_result = {
        "host_process_removed": True,
        "child_process_removed": True,
        "remote_authority_removed": True,
        "provider_cleanup": True,
        "forward_removed": True,
        "relay_removed": True,
    }
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    lock = FakeLock()
    manager._container_locks["example-1"] = (lock, session.session_id)
    manager._container_lock_sessions[session.session_id] = "example-1"
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=session.agent_name,
        caller_id=session.caller_id,
        target_dir=None,
        target_type="command",
        status=SessionStatus.FAILED.value,
        now=1,
        target_json=target.to_json(),
    )

    result = await manager.finalize_parity_fault_start(
        session,
        FAILED_ACP_HANDSHAKE_FAULT,
    )

    assert result["cleanup_confirmed"] is True
    assert result["session_row_removed"] is True
    assert result["session_memory_removed"] is True
    assert result["target_lock_removed"] is True
    assert result["ownership_retained"] is False
    assert lock.released is True


@pytest.mark.asyncio
async def test_failed_handshake_provider_cleanup_retains_container_lock(
    tmp_db,
) -> None:
    class FakeLock:
        def release(self):
            raise AssertionError("inconclusive cleanup must retain the lock")

    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={"name": "example-1"},
    )
    session = Session(
        "session-1",
        "failed",
        target,
        agent_name="container:example-1",
        caller_id="venue-parity:test",
    )
    session.status = SessionStatus.FAILED
    session.parity_fault_result = {
        "host_process_removed": True,
        "child_process_removed": True,
        "remote_authority_removed": True,
        "provider_cleanup": False,
        "forward_removed": True,
        "relay_removed": True,
    }
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    manager._container_locks["example-1"] = (
        FakeLock(),
        session.session_id,
    )
    manager._container_lock_sessions[session.session_id] = "example-1"
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=session.agent_name,
        caller_id=session.caller_id,
        target_dir=None,
        target_type="command",
        status=SessionStatus.FAILED.value,
        now=1,
        target_json=target.to_json(),
    )

    result = await manager.finalize_parity_fault_start(
        session,
        FAILED_ACP_HANDSHAKE_FAULT,
    )

    assert result["cleanup_confirmed"] is False
    assert result["ownership_retained"] is True
    assert result["durable_session_retained"] is True
    with pytest.raises(RuntimeError, match="already owned"):
        manager._acquire_container_lock("session-2", "example-1")


@pytest.mark.asyncio
async def test_failed_handshake_claim_release_failure_retains_session(
    tmp_db,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agent_bridge.session_manager._release_codespace_claim",
        lambda *_args: False,
    )
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        caller_worktree="venue-parity:test",
        codespace={
            "name": "example-codespace",
            "repo": "example/repo",
            "acp_command": "copilot --acp --stdio",
        },
    )
    session = Session(
        "session-1",
        "failed",
        target,
        agent_name="codespace:example-codespace",
        caller_id="venue-parity:test",
    )
    session.status = SessionStatus.FAILED
    session.parity_fault_result = {
        "host_process_removed": True,
        "child_process_removed": True,
        "remote_authority_removed": True,
        "provider_cleanup": True,
        "forward_removed": True,
        "relay_removed": True,
    }
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=session.agent_name,
        caller_id=session.caller_id,
        target_dir=None,
        target_type="command",
        status=SessionStatus.FAILED.value,
        now=1,
        target_json=target.to_json(),
    )

    result = await manager.finalize_parity_fault_start(
        session,
        FAILED_ACP_HANDSHAKE_FAULT,
    )

    assert result["cleanup_confirmed"] is False
    assert result["codespace_claim_removed"] is False
    assert result["ownership_retained"] is True
    assert result["durable_session_retained"] is True
    assert manager.get_session(session.session_id) is session


@pytest.mark.asyncio
async def test_container_recreate_retires_old_session_and_transfers_lock(
    tmp_db,
    monkeypatch,
) -> None:
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={
            "name": "example-1",
            "security_profile": "trusted",
            "provider_command": ["agent-containers"],
            "state_command": [
                "agent-containers",
                "session-host-state",
                "example-1",
            ],
            "acp_command": "copilot --acp --stdio",
        },
    )
    old = Session(
        "session-1",
        "old",
        target,
        agent_name="container:example-1",
        caller_id="venue-parity:test",
    )
    old.status = SessionStatus.IDLE
    old.acp_session_id = "acp-1"
    old.client = MagicMock(is_running=True, pid=123)
    old.mcp_servers = [{"name": "example", "command": "example-mcp"}]
    old.event_log = EventLog(db=tmp_db, session_id=old.session_id)
    manager._sessions[old.session_id] = old
    tmp_db.create_session(
        session_id=old.session_id,
        name=old.name,
        agent_name=old.agent_name,
        caller_id=old.caller_id,
        target_dir=None,
        target_type="command",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )
    manager._host_index.register(HostRecord(
        session_id=old.session_id,
        port=5000,
        host_pid=300,
        child_pid=123,
        boundary="container",
    ))
    manager._container_lock_sessions[old.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), old.session_id)

    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.container_state",
        AsyncMock(return_value={
            "name": "example-1",
            "running": True,
            "container_id": "a" * 64,
        }),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "recreate_container_for_parity",
        AsyncMock(return_value={
            "name": "example-1",
            "old_container_id": "a" * 64,
            "new_container_id": "b" * 64,
            "running": True,
            "identity_changed": True,
        }),
    )
    monkeypatch.setattr(manager, "_stop_relays", AsyncMock())

    async def fake_end(session_id, *, force=False):
        assert session_id == old.session_id
        manager._sessions.pop(session_id)
        tmp_db.delete_session(session_id)

    async def fake_start(
        replacement_target,
        agent_name=None,
        caller_id=None,
        **kwargs,
    ):
        assert kwargs["mcp_servers"] == [
            {"name": "example", "command": "example-mcp"}
        ]
        new = Session(
            "session-2",
            "new",
            replacement_target,
            agent_name=agent_name,
            caller_id=caller_id,
        )
        new.status = SessionStatus.IDLE
        new.acp_session_id = "acp-2"
        new.client = MagicMock(is_running=True, pid=456)
        manager._sessions[new.session_id] = new
        manager._transfer_container_lock(
            old.session_id,
            new.session_id,
            "example-1",
        )
        manager._host_index.register(HostRecord(
            session_id=new.session_id,
            port=5001,
            host_pid=400,
            child_pid=456,
            boundary="container",
        ))
        return new

    monkeypatch.setattr(manager, "end_session", fake_end)
    monkeypatch.setattr(manager, "start_session", fake_start)

    result = await manager.recreate_container_for_parity(
        old.session_id,
        timeout=1,
    )

    assert result["container_identity_changed"] is True
    assert result["old_session_removed"] is True
    assert result["old_host_index_removed"] is True
    assert result["target_lock_transferred"] is True
    assert result["old_host_pid"] == 300
    assert result["replacement_host_pid"] == 400
    assert result["old_child_pid"] == 123
    assert result["replacement_child_pid"] == 456


@pytest.mark.asyncio
async def test_container_recreate_failed_replacement_keeps_target_owned(
    tmp_db,
    monkeypatch,
) -> None:
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={
            "name": "example-1",
            "security_profile": "trusted",
            "provider_command": ["agent-containers"],
            "state_command": [
                "agent-containers",
                "session-host-state",
                "example-1",
            ],
            "acp_command": "copilot --acp --stdio",
        },
    )
    old = Session(
        "session-1",
        "old",
        target,
        agent_name="container:example-1",
        caller_id="venue-parity:test",
    )
    old.status = SessionStatus.IDLE
    old.acp_session_id = "acp-1"
    old.client = MagicMock(is_running=True, pid=123)
    manager._sessions[old.session_id] = old
    tmp_db.create_session(
        session_id=old.session_id,
        name=old.name,
        agent_name=old.agent_name,
        caller_id=old.caller_id,
        target_dir=None,
        target_type="command",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )
    manager._host_index.register(HostRecord(
        session_id=old.session_id,
        port=5000,
        host_pid=300,
        child_pid=123,
        boundary="container",
    ))
    manager._container_lock_sessions[old.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), old.session_id)
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.container_state",
        AsyncMock(return_value={
            "name": "example-1",
            "running": True,
            "container_id": "a" * 64,
        }),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "recreate_container_for_parity",
        AsyncMock(return_value={
            "name": "example-1",
            "old_container_id": "a" * 64,
            "new_container_id": "b" * 64,
            "running": True,
            "identity_changed": True,
        }),
    )

    async def failed_start(
        replacement_target,
        agent_name=None,
        caller_id=None,
        **kwargs,
    ):
        failed = Session(
            "session-2",
            "failed",
            replacement_target,
            agent_name=agent_name,
            caller_id=caller_id,
        )
        failed.status = SessionStatus.FAILED
        manager._sessions[failed.session_id] = failed
        manager._transfer_container_lock(
            old.session_id,
            failed.session_id,
            "example-1",
        )
        return failed

    monkeypatch.setattr(manager, "start_session", failed_start)

    with pytest.raises(
        RuntimeError,
        match="session-2 failed to reach idle and retains target ownership",
    ):
        await manager.recreate_container_for_parity(old.session_id, timeout=1)

    assert manager.get_session(old.session_id) is old
    assert old.status == SessionStatus.FAILED
    assert manager._host_index.get(old.session_id) is not None
    assert tmp_db.get_session(old.session_id)["status"] == SessionStatus.FAILED.value
    assert manager._container_lock_sessions["session-2"] == "example-1"
    assert (
        manager.get_session("session-2").target.container[
            "recreate_failed_without_host"
        ]
        is True
    )
    with pytest.raises(RuntimeError, match="already owned"):
        manager._acquire_container_lock("session-3", "example-1")

    await manager.end_session("session-2", force=True)
    assert "session-2" not in manager._container_lock_sessions
    assert "example-1" not in manager._container_locks


@pytest.mark.asyncio
async def test_end_with_inconclusive_missing_host_retains_session_and_lock(
    tmp_db,
    monkeypatch,
) -> None:
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={"name": "example-1"},
    )
    session = Session("session-1", "container", target)
    session.status = SessionStatus.IDLE
    manager._sessions[session.session_id] = session
    manager._container_lock_sessions[session.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), session.session_id)
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=None,
        caller_id=None,
        target_dir=None,
        target_type="command",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )

    async def inconclusive(*args, **kwargs):
        manager._remote_recovery_inconclusive.add(session.session_id)
        return 0

    monkeypatch.setattr(
        manager,
        "_recover_remote_host_records",
        inconclusive,
    )

    with pytest.raises(RemoteHostRecoveryPendingError, match="inconclusive"):
        await manager.end_session(session.session_id, force=True)

    assert manager.get_session(session.session_id) is session
    assert tmp_db.get_session(session.session_id) is not None
    assert manager._container_lock_sessions[session.session_id] == "example-1"
    assert manager._container_locks["example-1"][1] == session.session_id


@pytest.mark.asyncio
async def test_end_with_confirmed_missing_host_releases_container_lock(
    tmp_db,
    monkeypatch,
) -> None:
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={"name": "example-1"},
    )
    session = Session("session-1", "container", target)
    session.status = SessionStatus.FAILED
    manager._sessions[session.session_id] = session
    manager._container_lock_sessions[session.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), session.session_id)
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=None,
        caller_id=None,
        target_dir=None,
        target_type="command",
        status=SessionStatus.FAILED.value,
        now=1,
        target_json=target.to_json(),
    )
    monkeypatch.setattr(
        manager,
        "_recover_remote_host_records",
        AsyncMock(return_value=0),
    )

    await manager.end_session(session.session_id, force=True)

    assert manager.get_session(session.session_id) is None
    assert session.session_id not in manager._container_lock_sessions
    assert "example-1" not in manager._container_locks


@pytest.mark.asyncio
async def test_end_with_recovered_live_host_retains_owner_when_reap_fails(
    tmp_db,
    monkeypatch,
) -> None:
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={"name": "example-1"},
    )
    session = Session("session-1", "container", target)
    session.status = SessionStatus.IDLE
    manager._sessions[session.session_id] = session
    manager._container_lock_sessions[session.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), session.session_id)
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=None,
        caller_id=None,
        target_dir=None,
        target_type="command",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )

    async def recover(*args, **kwargs):
        manager._host_index.register(HostRecord(
            session_id=session.session_id,
            port=5000,
            host_pid=300,
            child_pid=123,
            boundary="container",
            endpoint={"kind": "container"},
        ))
        return 1

    monkeypatch.setattr(manager, "_recover_remote_host_records", recover)
    monkeypatch.setattr(
        manager,
        "_remote_reap",
        AsyncMock(return_value=False),
    )

    with pytest.raises(RemoteHostRecoveryPendingError, match="reap is inconclusive"):
        await manager.end_session(session.session_id, force=True)

    assert manager.get_session(session.session_id) is session
    assert session.status == SessionStatus.FAILED
    assert tmp_db.get_session(session.session_id) is not None
    assert manager._host_index.get(session.session_id) is not None
    assert manager._container_lock_sessions[session.session_id] == "example-1"


@pytest.mark.asyncio
async def test_end_with_recovered_live_host_deletes_after_confirmed_reap(
    tmp_db,
    monkeypatch,
) -> None:
    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={"name": "example-1"},
    )
    session = Session("session-1", "container", target)
    session.status = SessionStatus.IDLE
    manager._sessions[session.session_id] = session
    manager._container_lock_sessions[session.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), session.session_id)
    tmp_db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=None,
        caller_id=None,
        target_dir=None,
        target_type="command",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )
    manager._host_index.register(HostRecord(
        session_id=session.session_id,
        port=5000,
        host_pid=300,
        child_pid=123,
        boundary="container",
        endpoint={"kind": "container"},
    ))

    async def confirmed_reap(rec, endpoint):
        manager._release_container_lock(rec.session_id)
        return True

    monkeypatch.setattr(manager, "_remote_reap", confirmed_reap)

    await manager.end_session(session.session_id, force=True)

    assert manager.get_session(session.session_id) is None
    assert manager._host_index.get(session.session_id) is None
    assert session.session_id not in manager._container_lock_sessions
    assert "example-1" not in manager._container_locks


@pytest.mark.asyncio
async def test_container_recreate_post_removal_failure_marks_predecessor_failed(
    tmp_db,
    monkeypatch,
) -> None:
    from agent_bridge.session_host.container_transport import (
        ContainerRecreateAfterRemovalError,
    )

    manager = SessionManager(tmp_db)
    target = SpawnTarget(
        type="command",
        container={
            "name": "example-1",
            "security_profile": "trusted",
            "provider_command": ["agent-containers"],
            "state_command": [
                "agent-containers",
                "session-host-state",
                "example-1",
            ],
        },
    )
    old = Session(
        "session-1",
        "old",
        target,
        agent_name="container:example-1",
        caller_id="venue-parity:test",
    )
    old.status = SessionStatus.IDLE
    old.client = MagicMock(is_running=True, pid=123)
    manager._sessions[old.session_id] = old
    tmp_db.create_session(
        session_id=old.session_id,
        name=old.name,
        agent_name=old.agent_name,
        caller_id=old.caller_id,
        target_dir=None,
        target_type="command",
        status=SessionStatus.IDLE.value,
        now=1,
        target_json=target.to_json(),
    )
    manager._host_index.register(HostRecord(
        session_id=old.session_id,
        port=5000,
        host_pid=300,
        child_pid=123,
        boundary="container",
    ))
    manager._container_lock_sessions[old.session_id] = "example-1"
    manager._container_locks["example-1"] = (MagicMock(), old.session_id)
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.container_state",
        AsyncMock(return_value={
            "name": "example-1",
            "running": True,
            "container_id": "a" * 64,
        }),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport."
        "recreate_container_for_parity",
        AsyncMock(side_effect=ContainerRecreateAfterRemovalError(
            "replacement failed"
        )),
    )

    with pytest.raises(
        ContainerRecreateAfterRemovalError,
        match="replacement failed",
    ):
        await manager.recreate_container_for_parity(old.session_id, timeout=1)

    assert old.status == SessionStatus.FAILED
    assert tmp_db.get_session(old.session_id)["status"] == SessionStatus.FAILED.value
    assert manager._host_index.get(old.session_id) is not None
    assert manager._container_lock_sessions[old.session_id] == "example-1"

    old.client = None
    await manager.end_session(old.session_id, force=True)
    assert manager._host_index.get(old.session_id) is None
    assert old.session_id not in manager._container_lock_sessions
    assert "example-1" not in manager._container_locks


class _ParityRelayProcess:
    def __init__(self, owner, pid, *, replacement_pid=None):
        self._owner = owner
        self.pid = pid
        self.returncode = None
        self._replacement_pid = replacement_pid

    def kill(self):
        self.returncode = -9

    async def wait(self):
        if self._replacement_pid is not None:
            self._owner._proc = _ParityRelayProcess(
                self._owner,
                self._replacement_pid,
            )
        return self.returncode


class _ParityRelay:
    def __init__(self, pid, replacement_pid):
        self._proc = _ParityRelayProcess(
            self,
            pid,
            replacement_pid=replacement_pid,
        )

    @property
    def is_alive(self):
        return self._proc is not None and self._proc.returncode is None


@pytest.mark.asyncio
async def test_parity_relay_interrupt_preserves_single_owner(tmp_db, tmp_path):
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path))
    manager._sessions["s1"] = SimpleNamespace(
        session_id="s1",
        caller_id="venue-parity:test",
        status=SessionStatus.IDLE,
    )
    relay = _ParityRelay(100, 200)
    manager._relays["s1"] = [relay]

    result = await manager.interrupt_relays_for_parity("s1", timeout=1)

    assert result == {
        "owner_count_before": 1,
        "owner_count_after": 1,
        "interrupted_count": 1,
        "all_recovered": True,
        "owner_identity_preserved": True,
        "processes_replaced": True,
    }
    assert manager._relays["s1"] == [relay]
    assert relay.is_alive is True
    assert relay._proc.pid == 200


@pytest.mark.asyncio
async def test_parity_relay_interrupt_requires_harness_owned_idle_session(
    tmp_db,
    tmp_path,
):
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path))
    manager._sessions["s1"] = SimpleNamespace(
        session_id="s1",
        caller_id="ordinary-caller",
        status=SessionStatus.IDLE,
    )

    with pytest.raises(PermissionError, match="venue-parity"):
        await manager.interrupt_relays_for_parity("s1", timeout=1)

    manager._sessions["s1"].caller_id = "venue-parity:test"
    manager._sessions["s1"].status = SessionStatus.RUNNING
    with pytest.raises(RuntimeError, match="idle session"):
        await manager.interrupt_relays_for_parity("s1", timeout=1)


class TestRemoteForwardRelaySupervision:
    """Remote Session-Host reattach owns relay supervisors separately from -L."""

    class _Forward:
        instances: list["TestRemoteForwardRelaySupervision._Forward"] = []

        def __init__(self, config, remote_port, *, local_port=None, **kw):
            self.config = config
            self.remote_port = remote_port
            self.local_port = local_port or 49555
            self.kw = kw
            self.established = 0
            self.refreshed = 0
            self.cancelled = 0
            self._proc = None
            self.__class__.instances.append(self)

        async def establish(self):
            self.established += 1
            return self.local_port

        async def refresh(self):
            self.refreshed += 1
            return self.local_port

        async def cancel(self):
            self.cancelled += 1

    class _Relay:
        instances: list["TestRemoteForwardRelaySupervision._Relay"] = []

        def __init__(self, config, relay_port, *, serving_probe=None, **kw):
            self.config = config
            self.relay_port = relay_port
            self.serving_probe = serving_probe
            self.kw = kw
            self.started = 0
            self.stopped = 0
            self.is_alive = False
            self._proc = None
            self.__class__.instances.append(self)

        async def start(self):
            self.started += 1
            self.is_alive = True

        async def stop(self):
            self.stopped += 1
            self.is_alive = False

    @pytest.fixture(autouse=True)
    def _patch_endpoint_forwards(self, monkeypatch):
        from agent_bridge.session_host import endpoints as endpoints_mod

        self._Forward.instances.clear()
        self._Relay.instances.clear()
        monkeypatch.setattr(endpoints_mod, "LocalForward", self._Forward)
        monkeypatch.setattr(endpoints_mod, "SupervisedRelayForward", self._Relay)
        yield
        self._Forward.instances.clear()
        self._Relay.instances.clear()

    @staticmethod
    def _endpoint(reverse_forwards=None):
        return {
            "kind": "codespace",
            "remote_port": 51000,
            "local_port": 49555,
            "reverse_forwards": list(reverse_forwards or []),
            "ssh": {
                "host_alias": "cs.box",
                "hostname": None,
                "user": "vscode",
                "port": None,
                "identity_file": None,
                "proxy_command": None,
                "config_file": "cs.config",
                "extra_options": {},
            },
        }

    @pytest.mark.asyncio
    async def test_ensure_forward_rebuilds_l_only_and_starts_relay(self, tmp_db, tmp_path):
        manager = SessionManager(
            tmp_db, session_host_state_dir=str(tmp_path),
        )
        rec = SimpleNamespace(
            session_id="s1",
            boundary="codespace",
            endpoint=self._endpoint(["9857:127.0.0.1:9857"]),
        )

        await manager._ensure_forward(rec)

        assert len(self._Forward.instances) == 1
        assert self._Forward.instances[0].kw.get("reverse_forwards") is None
        assert self._Forward.instances[0].established == 1
        assert len(self._Relay.instances) == 1
        assert self._Relay.instances[0].relay_port == 9857
        assert self._Relay.instances[0].started == 1
        assert manager._relays["s1"] == [self._Relay.instances[0]]

    @pytest.mark.asyncio
    async def test_ensure_forward_leaves_live_relay_on_front_reattach(
        self, tmp_db, tmp_path,
    ):
        manager = SessionManager(
            tmp_db, session_host_state_dir=str(tmp_path),
        )
        rec = SimpleNamespace(
            session_id="s1",
            boundary="codespace",
            endpoint=self._endpoint(["9857:127.0.0.1:9857"]),
        )
        forward = self._Forward(None, 51000)
        live_relay = self._Relay(None, 9857)
        live_relay.is_alive = True
        manager._forwards["s1"] = forward
        manager._relays["s1"] = [live_relay]

        await manager._ensure_forward(rec)

        assert forward.refreshed == 1
        assert live_relay.stopped == 0
        assert len(self._Relay.instances) == 1

    @pytest.mark.asyncio
    async def test_ensure_forward_replaces_dead_prior_relay(self, tmp_db, tmp_path):
        manager = SessionManager(
            tmp_db, session_host_state_dir=str(tmp_path),
        )
        rec = SimpleNamespace(
            session_id="s1",
            boundary="codespace",
            endpoint=self._endpoint(["9857:127.0.0.1:9857"]),
        )
        dead_relay = self._Relay(None, 9857)
        dead_relay.is_alive = False
        manager._relays["s1"] = [dead_relay]

        await manager._ensure_forward(rec)

        assert dead_relay.stopped == 1
        assert len(self._Relay.instances) == 2
        assert self._Relay.instances[-1].started == 1
        assert manager._relays["s1"] == [self._Relay.instances[-1]]

    @pytest.mark.asyncio
    async def test_drop_forward_stops_relay_and_l_forward(self, tmp_db, tmp_path):
        manager = SessionManager(
            tmp_db, session_host_state_dir=str(tmp_path),
        )
        forward = self._Forward(None, 51000)
        relay = self._Relay(None, 9857)
        relay.is_alive = True
        manager._forwards["s1"] = forward
        manager._relays["s1"] = [relay]

        await manager._drop_forward("s1")

        assert relay.stopped == 1
        assert forward.cancelled == 1
        assert "s1" not in manager._forwards
        assert "s1" not in manager._relays


@pytest.mark.asyncio
async def test_startup_reattach_resumes_session_stopped_while_starting(
    tmp_db, tmp_path, monkeypatch,
) -> None:
    """A surviving Session Host that was STARTING at daemon restart is re-driven."""
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path))
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    session.restart_status = SessionStatus.STARTING.value
    manager._sessions[session.session_id] = session
    rec = SimpleNamespace(
        session_id=session.session_id,
        protocol_version=1,
        host_version="test",
        host_pid=123,
        child_pid=456,
        created_at=time.time(),
        resume_on_reattach=False,
        boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(manager, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(manager, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(manager, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(manager, "_reattach_one", attach)

    assert await manager.reattach_session_hosts(remote_recovery_timeout=1.0) == 1
    assert attach.await_args.kwargs["send_resume"] is True


@pytest.mark.asyncio
async def test_startup_reattach_leaves_prior_idle_session_idle(
    tmp_db, tmp_path, monkeypatch,
) -> None:
    """Already-idle sessions are adopted without manufacturing an extra turn."""
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path))
    session = Session("session-1", "agent", SpawnTarget(type="local", cwd=str(tmp_path)))
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "acp-1"
    session.restart_status = SessionStatus.IDLE.value
    manager._sessions[session.session_id] = session
    rec = SimpleNamespace(
        session_id=session.session_id,
        protocol_version=1,
        host_version="test",
        host_pid=123,
        child_pid=456,
        created_at=time.time(),
        resume_on_reattach=False,
        boundary="local",
    )
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(manager, "_prune_dead_hosts", lambda: None)
    monkeypatch.setattr(manager, "_live_host_records", lambda: [rec])
    monkeypatch.setattr(manager, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(manager, "_reattach_one", attach)

    assert await manager.reattach_session_hosts(remote_recovery_timeout=1.0) == 1
    assert attach.await_args.kwargs["send_resume"] is False


class TestSubmitPrompt:
    """Prompt submission."""

    @pytest.mark.asyncio
    async def test_submit_returns_turn_index(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        turn_idx = await session_manager.submit_prompt(session.session_id, "Hello")
        assert turn_idx == 0

    @pytest.mark.asyncio
    async def test_submit_rejects_running(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING
        with pytest.raises(ValueError, match="not idle"):
            await session_manager.submit_prompt(session.session_id, "Hello")

    @pytest.mark.asyncio
    async def test_submit_auto_resumes_stopped(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """STOPPED sessions with an ACP session ID auto-resume on submit."""
        session = await session_manager.start_session(spawn_target)
        session.acp_session_id = "test-acp-id"
        session.status = SessionStatus.STOPPED
        session.client = None
        # submit_prompt should auto-resume then deliver the prompt
        turn = await session_manager.submit_prompt(session.session_id, "Hello")
        assert isinstance(turn, int)
        assert session.status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_submit_cancels_out_of_turn_bracket(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A new turn cancels any pending out-of-turn content bracket up front
        (#2835), so a due settle timer can't inject a spurious ``idle`` into the
        turn. The cancel must run synchronously during submit, before ``running``
        is written and the prompt task is scheduled."""
        session = await session_manager.start_session(spawn_target)
        cancel = MagicMock()
        session.client._cancel_out_of_turn = cancel
        await session_manager.submit_prompt(session.session_id, "Hello")
        cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_submit_persists_user_message_event(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """The prompt is persisted as a durable ``user_message`` event (ahead
        of the running state change) so it replays on resume/open -- not just a
        row in the turns table that the chat history never sees (issue #912)."""
        session = await session_manager.start_session(spawn_target)
        await session_manager.submit_prompt(session.session_id, "Hello there")

        events = session.event_log.get_events()
        user_events = [e for e in events if e.event == "user_message"]
        assert len(user_events) == 1
        assert user_events[0].data.get("content") == "Hello there"
        # The user bubble is logged immediately before the turn goes "running".
        types = [e.event for e in events]
        ui = types.index("user_message")
        assert events[ui + 1].event == "session_state_changed"
        assert events[ui + 1].data.get("status") == "running"

    @pytest.mark.asyncio
    async def test_submit_unknown_session(self, session_manager) -> None:
        with pytest.raises(KeyError):
            await session_manager.submit_prompt("nonexistent", "Hello")

    @pytest.mark.asyncio
    async def test_run_prompt_emits_terminal_idle_on_success(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A completed turn drives the event log to a terminal idle so no
        consumer is left mirroring a turn that never ends (issue #22)."""
        session = await session_manager.start_session(spawn_target)
        await session_manager.submit_prompt(session.session_id, "Hello")
        await session._prompt_task

        events = session.event_log.get_events()
        state_changes = [
            e for e in events if e.event == "session_state_changed"
        ]
        assert state_changes[-1].data.get("status") == "idle"
        assert session.status == SessionStatus.IDLE

    @pytest.mark.asyncio
    async def test_run_prompt_emits_terminal_idle_on_failure(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A turn whose ACP prompt fails (e.g. transport lost mid-turn) still
        reaches a terminal idle in the event log instead of wedging the session
        in 'running' forever (issue #22)."""
        session = await session_manager.start_session(spawn_target)
        session.client.send_prompt = AsyncMock(
            side_effect=ConnectionResetError("transport lost")
        )
        await session_manager.submit_prompt(session.session_id, "Hello")
        await session._prompt_task

        events = session.event_log.get_events()
        state_changes = [
            e for e in events if e.event == "session_state_changed"
        ]
        assert state_changes[-1].data.get("status") == "idle"
        assert session.status == SessionStatus.IDLE


class TestStopSession:
    """Session stop."""

    @pytest.mark.asyncio
    async def test_stop_sets_status(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        await session_manager.stop_session(session.session_id)
        assert session.status == SessionStatus.STOPPED
        assert session.client is None


class TestEndSession:
    """Session end."""

    @pytest.mark.asyncio
    async def test_end_removes_session(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        await session_manager.end_session(sid)
        assert session_manager.get_session(sid) is None

    @pytest.mark.asyncio
    async def test_end_if_idle_removes_idle_session(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)

        await session_manager.end_session_if_idle(session.session_id)

        assert session_manager.get_session(session.session_id) is None

    @pytest.mark.asyncio
    async def test_end_if_idle_preserves_running_session(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING

        with pytest.raises(ValueError, match="is not idle"):
            await session_manager.end_session_if_idle(session.session_id)

        assert session_manager.get_session(session.session_id) is session

    @pytest.mark.asyncio
    async def test_end_succeeds_when_shutdown_raises(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        # Report 4.4(a): ending a mid-turn session raised out of shutdown ->
        # HTTP 500. Teardown must be best-effort so the session is always ended.
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        mock_acp_client.shutdown = AsyncMock(side_effect=RuntimeError("busy mid-turn"))
        # Must not raise, and must remove the session.
        await session_manager.end_session(sid)
        assert session_manager.get_session(sid) is None

    @pytest.mark.asyncio
    async def test_end_succeeds_when_db_delete_raises(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        # #48: a transient DB error during teardown (e.g. a locked SQLite file)
        # must not surface as HTTP 500. The session is still removed from memory,
        # and the row is marked ENDED *before* the delete so a later restart
        # rehydrate purges it instead of resurrecting it as active.
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        session_manager._db.delete_session = MagicMock(
            side_effect=RuntimeError("database is locked")
        )
        await session_manager.end_session(sid)
        assert session_manager.get_session(sid) is None
        rows = {r["id"]: r["status"] for r in session_manager._db.list_sessions()}
        assert rows.get(sid) == SessionStatus.ENDED.value

    @pytest.mark.asyncio
    async def test_stop_succeeds_when_shutdown_raises(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        mock_acp_client.shutdown = AsyncMock(side_effect=RuntimeError("busy mid-turn"))
        await session_manager.stop_session(session.session_id)
        assert session.status == SessionStatus.STOPPED
        assert session.client is None


class TestBackgroundTaskGate:
    """Teardown is refused while a session hosts active background sub-agents."""

    @pytest.mark.asyncio
    async def test_stop_refused_when_background_tasks_active(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        from agent_bridge.session_manager import SessionBusyError

        session = await session_manager.start_session(spawn_target)
        mock_acp_client.has_active_background_tasks = True
        mock_acp_client.active_background_tasks = ["pr-daemon"]

        with pytest.raises(SessionBusyError):
            await session_manager.stop_session(session.session_id)

        # Session is left intact -- the background work keeps running.
        assert session.status == SessionStatus.IDLE
        assert session.client is mock_acp_client

    @pytest.mark.asyncio
    async def test_end_refused_when_background_tasks_active(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        from agent_bridge.session_manager import SessionBusyError

        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        mock_acp_client.has_active_background_tasks = True
        mock_acp_client.active_background_tasks = ["pr-daemon"]

        with pytest.raises(SessionBusyError):
            await session_manager.end_session(sid)

        assert session_manager.get_session(sid) is session

    @pytest.mark.asyncio
    async def test_force_stop_overrides_background_tasks(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        mock_acp_client.has_active_background_tasks = True
        mock_acp_client.active_background_tasks = ["pr-daemon"]

        await session_manager.stop_session(session.session_id, force=True)
        assert session.status == SessionStatus.STOPPED
        assert session.client is None

    @pytest.mark.asyncio
    async def test_force_end_overrides_background_tasks(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        mock_acp_client.has_active_background_tasks = True
        mock_acp_client.active_background_tasks = ["pr-daemon"]

        await session_manager.end_session(sid, force=True)
        assert session_manager.get_session(sid) is None


class TestResumeSession:
    """Session resume from STOPPED state."""

    @pytest.mark.asyncio
    async def test_start_session_preserves_existing_provider_override_provenance(
        self, session_manager, _patch_spawn, _patch_acp
    ) -> None:
        target = SpawnTarget(
            type="local",
            cwd="/workspaces/example",
            env={"REQUEST_OVERRIDE": "kept"},
            copilot_args=["--request-flag"],
            venue={
                "kind": "provider",
                "_agent_bridge_request_overrides": {
                    "env": {"REQUEST_OVERRIDE": "kept"},
                    "copilot_args": ["--request-flag"],
                },
            },
        )

        session = await session_manager.start_session(
            target,
            agent_name="provider:example",
        )

        assert session.target.venue["_agent_bridge_request_overrides"] == {
            "env": {"REQUEST_OVERRIDE": "kept"},
            "copilot_args": ["--request-flag"],
        }

    @pytest.mark.asyncio
    async def test_resume_stopped_session(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        await session_manager.stop_session(session.session_id)
        assert session.status == SessionStatus.STOPPED

        # Resume spawns new process + loads session
        session = await session_manager.resume_session(session.session_id)
        assert session.status == SessionStatus.IDLE
        assert session.client is not None
        mock_acp_client.load_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_resume_prefers_reattach_over_respawn(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        """A resume adopts a surviving Session Host (no fresh child /
        load_session) when one is still alive for the session (#145)."""
        session = await session_manager.start_session(spawn_target)
        await session_manager.stop_session(session.session_id)
        assert session.status == SessionStatus.STOPPED

        async def _fake_reattach(sess):
            sess.client = mock_acp_client
            sess.status = SessionStatus.IDLE
            return True

        mock_acp_client.load_session.reset_mock()
        with patch.object(
            session_manager, "_try_reattach_live_host",
            AsyncMock(side_effect=_fake_reattach),
        ):
            resumed = await session_manager.resume_session(session.session_id)

        assert resumed.status == SessionStatus.IDLE
        # Reattach path: the running child is adopted, so no fresh-child replay.
        mock_acp_client.load_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_refreshes_stopped_provider_target(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(
            spawn_target,
            agent_name="codespace:example",
        )
        await session_manager.stop_session(session.session_id)
        session.target = SpawnTarget(
            type="command",
            cwd="/workspaces/example",
            project="example",
            worktree_id="wt-1",
            caller_worktree="caller-wt",
            caller_owner_ref="owner/ref",
            env={
                "PROVIDER_DEFAULT": "old",
                "REQUEST_OVERRIDE": "kept",
            },
            copilot_args=[
                "--additional-mcp-config",
                "@old-provider.json",
                "--additional-mcp-config",
                "@request.json",
            ],
            codespace={"name": "old", "repo": "example"},
            venue={
                "kind": "codespace",
                "revision": "old",
                "_agent_bridge_request_overrides": {
                    "env": {"REQUEST_OVERRIDE": "kept"},
                    "copilot_args": [
                        "--additional-mcp-config",
                        "@request.json",
                    ],
                },
            },
        )
        session_manager._db.update_session_target(
            session.session_id,
            session.target.to_json(),
            session.target.cwd,
        )
        refreshed = SpawnTarget(
            type="ssh",
            host="new-host",
            cwd="/provider/default",
            project="provider-project",
            env={"PROVIDER_DEFAULT": "new"},
            copilot_args=[
                "--additional-mcp-config",
                "@new-provider.json",
            ],
            venue={"kind": "codespace", "revision": "new"},
        )
        resolver = MagicMock()
        resolver.resolve_async = AsyncMock(return_value=refreshed)
        session_manager.set_resolver(resolver)

        with patch.object(
            session_manager,
            "_try_reattach_live_host",
            AsyncMock(return_value=False),
        ):
            resumed = await session_manager.resume_session(session.session_id)

        resolver.resolve_async.assert_awaited_once_with("codespace:example")
        assert resumed.target.type == "ssh"
        assert resumed.target.host == "new-host"
        assert resumed.target.venue == {
            "kind": "codespace",
            "revision": "new",
            "_agent_bridge_request_overrides": {
                "env": {"REQUEST_OVERRIDE": "kept"},
                "copilot_args": [
                    "--additional-mcp-config",
                    "@request.json",
                ],
            },
        }
        assert resumed.target.cwd == "/workspaces/example"
        assert resumed.target.project == "example"
        assert resumed.target.worktree_id == "wt-1"
        assert resumed.target.caller_worktree == "caller-wt"
        assert resumed.target.caller_owner_ref == "owner/ref"
        assert resumed.target.env == {
            "PROVIDER_DEFAULT": "new",
            "REQUEST_OVERRIDE": "kept",
        }
        assert resumed.target.copilot_args == [
            "--additional-mcp-config",
            "@new-provider.json",
            "--additional-mcp-config",
            "@request.json",
        ]
        persisted = SpawnTarget.from_json(
            session_manager._db.get_session(session.session_id)["target_json"]
        )
        assert persisted.host == "new-host"
        assert persisted.venue == resumed.target.venue

    @pytest.mark.asyncio
    async def test_resume_does_not_refresh_surviving_provider_host(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        session = await session_manager.start_session(
            spawn_target,
            agent_name="codespace:example",
        )
        await session_manager.stop_session(session.session_id)
        session.target = SpawnTarget(
            type="command",
            codespace={"name": "existing", "repo": "example"},
        )
        resolver = MagicMock()
        resolver.resolve_async = AsyncMock()
        session_manager.set_resolver(resolver)

        async def _fake_reattach(sess):
            sess.client = mock_acp_client
            sess.status = SessionStatus.IDLE
            return True

        with patch.object(
            session_manager,
            "_try_reattach_live_host",
            AsyncMock(side_effect=_fake_reattach),
        ):
            await session_manager.resume_session(session.session_id)

        resolver.resolve_async.assert_not_awaited()
        assert session.target.codespace["name"] == "existing"

    @pytest.mark.asyncio
    async def test_resume_provider_refresh_failure_keeps_session_stopped(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(
            spawn_target,
            agent_name="codespace:example",
        )
        await session_manager.stop_session(session.session_id)
        session.target = SpawnTarget(
            type="command",
            codespace={"name": "stale", "repo": "example"},
            venue={
                "_agent_bridge_request_overrides": {
                    "env": {},
                    "copilot_args": [],
                },
            },
        )
        resolver = MagicMock()
        resolver.resolve_async = AsyncMock(
            side_effect=RuntimeError("provider unavailable")
        )
        session_manager.set_resolver(resolver)

        with patch.object(
            session_manager,
            "_try_reattach_live_host",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(
                RuntimeError,
                match="Current provider target could not be resolved",
            ):
                await session_manager.resume_session(session.session_id)

        assert session.status == SessionStatus.STOPPED
        assert session.target.codespace["name"] == "stale"

    @pytest.mark.asyncio
    async def test_resume_legacy_provider_target_requires_recreate(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(
            spawn_target,
            agent_name="codespace:example",
        )
        await session_manager.stop_session(session.session_id)
        session.target = SpawnTarget(
            type="command",
            codespace={"name": "legacy", "repo": "example"},
        )
        resolver = MagicMock()
        resolver.resolve_async = AsyncMock()
        session_manager.set_resolver(resolver)

        with patch.object(
            session_manager,
            "_try_reattach_live_host",
            AsyncMock(return_value=False),
        ):
            with pytest.raises(RuntimeError, match="recreate the session"):
                await session_manager.resume_session(session.session_id)

        resolver.resolve_async.assert_not_awaited()
        assert session.status == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_resume_rejects_non_stopped(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        with pytest.raises(ValueError, match="not stopped"):
            await session_manager.resume_session(session.session_id)

    @pytest.mark.asyncio
    async def test_resume_rejects_missing_acp_id(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        await session_manager.stop_session(session.session_id)
        session.acp_session_id = None  # Simulate missing ACP ID

        with pytest.raises(RuntimeError, match="no ACP session ID"):
            await session_manager.resume_session(session.session_id)

    @pytest.mark.asyncio
    async def test_auto_recreate_missing_acp_id(
        self,
        session_manager,
        spawn_target,
        _patch_spawn,
        _patch_acp,
        mock_acp_client,
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        await session_manager.stop_session(session.session_id)
        session.acp_session_id = None
        session.mcp_servers = [{"name": "test-mcp", "command": "server"}]
        session_manager._db.update_session_acp_id(session.session_id, None)

        with patch.object(
            session_manager,
            "_try_reattach_live_host",
            AsyncMock(return_value=False),
        ):
            resumed = await session_manager.resume_session(
                session.session_id,
                allow_recreate=True,
            )

        assert resumed.status == SessionStatus.IDLE
        assert resumed.acp_session_id == "acp-test-123"
        mock_acp_client.new_session.assert_awaited_once()
        assert mock_acp_client.new_session.await_args.kwargs["mcp_servers"] == [
            {"name": "test-mcp", "command": "server"}
        ]
        mock_acp_client.load_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_failure_reverts_to_stopped(
        self, session_manager, spawn_target, _patch_spawn
    ) -> None:
        # First start with working mock
        with patch("agent_bridge.session_manager.AcpClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.is_running = True
            mock_client.pid = 111
            mock_client.acp_session_id = "acp-1"
            mock_client.start = AsyncMock()
            mock_client.new_session = AsyncMock(return_value="acp-1")
            mock_client.shutdown = AsyncMock()
            mock_client.cancel_prompt = AsyncMock()
            mock_client.has_active_background_tasks = False
            mock_client.active_background_tasks = []
            mock_cls.return_value = mock_client

            async def _fake_host_connect(self, target, **kwargs):
                return mock_client, "acp-1"

            with patch.object(
                SessionManager, "_connect_via_session_host", _fake_host_connect
            ):
                session = await session_manager.start_session(spawn_target)
            await session_manager.stop_session(session.session_id)

        # Now resume with failing ACP
        with patch("agent_bridge.session_manager.AcpClient") as mock_cls:
            fail_client = MagicMock()
            fail_client.start = AsyncMock(side_effect=RuntimeError("spawn failed"))
            fail_client.shutdown = AsyncMock()
            fail_client.stderr_tail = MagicMock(return_value="")
            mock_cls.return_value = fail_client

            with pytest.raises(RuntimeError, match="spawn failed"):
                await session_manager.resume_session(session.session_id)

            assert session.status == SessionStatus.STOPPED
            assert session.client is None

    @pytest.mark.asyncio
    async def test_resume_unknown_session(self, session_manager) -> None:
        with pytest.raises(KeyError):
            await session_manager.resume_session("nonexistent")


class TestResyncSession:
    """Session resync -- rebuild the event log from the agent's replay."""

    @staticmethod
    def _replay_acp_factory(replay_events):
        """Build a patch factory whose load_session emits ``replay_events``.

        When the SessionManager constructs ``AcpClient(on_event=cb)`` and then
        calls ``load_session(..., suppress_replay=False)``, the mock invokes
        ``cb`` with each replayed event -- emulating the agent streaming its
        full history back during load.
        """
        def factory(*args, on_event=None, **kwargs):
            client = MagicMock()
            client.is_running = True
            client.pid = 222
            client.acp_session_id = "acp-test-123"
            client.start = AsyncMock()
            client.shutdown = AsyncMock()
            client.cancel_prompt = AsyncMock()

            async def _load(cwd, session_id, suppress_replay=True):
                if not suppress_replay and on_event:
                    for etype, data in replay_events:
                        on_event(etype, data)

            client.load_session = AsyncMock(side_effect=_load)
            return client
        return factory

    @pytest.mark.asyncio
    async def test_resync_rebuilds_log_from_replay(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        # Simulate a truncated log: only a couple of events were captured live.
        session.event_log.append("agent_message", {"text": "partial"})
        session.event_log.append("error", {"message": "Connection closed"})

        replay = [
            ("agent_message", {"text": "Let's add a pride theme"}),
            ("tool_call_start", {"tool_call_id": "t1", "title": "hue-hue_export_scenes"}),
            ("tool_call_update", {"tool_call_id": "t1", "status": "completed",
                                  "content": ["Exported 46 scenes."]}),
            ("agent_message", {"text": "Here are the front-yard lights."}),
        ]
        with patch("agent_bridge.session_manager.AcpClient",
                   side_effect=self._replay_acp_factory(replay)):
            count = await session_manager.resync_session(sid)

        assert count == len(replay)
        events = session.event_log.get_events()
        # Rebuilt replay (IDs from 1) + a trailing resync state event.
        types = [e.event for e in events]
        assert types[:len(replay)] == [
            "agent_message", "tool_call_start", "tool_call_update", "agent_message",
        ]
        assert types[-1] == "session_state_changed"
        assert events[-1].data.get("resynced") is True
        # The old truncated "Connection closed" error is gone.
        assert all(e.data.get("message") != "Connection closed" for e in events)
        assert session.status == SessionStatus.IDLE
        assert session.client is not None

    @pytest.mark.asyncio
    async def test_resync_is_idempotent(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        replay = [("agent_message", {"text": "hi"})]
        factory = self._replay_acp_factory(replay)

        with patch("agent_bridge.session_manager.AcpClient", side_effect=factory):
            first = await session_manager.resync_session(sid)
        with patch("agent_bridge.session_manager.AcpClient", side_effect=factory):
            second = await session_manager.resync_session(sid)

        assert first == second == len(replay)
        # Log reflects exactly the replay (+ trailing state event), no growth.
        types = [e.event for e in session.event_log.get_events()]
        assert types == ["agent_message", "session_state_changed"]

    @pytest.mark.asyncio
    async def test_resync_rejects_missing_acp_id(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.acp_session_id = None
        with pytest.raises(RuntimeError, match="no ACP session ID"):
            await session_manager.resync_session(session.session_id)

    @pytest.mark.asyncio
    async def test_resync_unknown_session(self, session_manager) -> None:
        with pytest.raises(KeyError):
            await session_manager.resync_session("nonexistent")

    @pytest.mark.asyncio
    async def test_resync_heals_wedged_running_session(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A session left in RUNNING with no live prompt task (wedged -- e.g.
        a turn whose runner died without a terminal event, or a session
        rehydrated after a restart) must be resyncable so it can be healed
        back to idle (issue #22 / #2385)."""
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        session.status = SessionStatus.RUNNING
        session._prompt_task = None  # no live turn -> wedged, not live

        replay = [("agent_message", {"text": "recovered"})]
        with patch("agent_bridge.session_manager.AcpClient",
                   side_effect=self._replay_acp_factory(replay)):
            count = await session_manager.resync_session(sid)

        assert count == len(replay)
        assert session.status == SessionStatus.IDLE
        events = session.event_log.get_events()
        assert events[-1].event == "session_state_changed"
        assert events[-1].data.get("resynced") is True

    @pytest.mark.asyncio
    async def test_resync_refuses_live_running_turn(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A genuinely live turn (a running prompt task) must still be refused
        -- healing only applies to a wedged RUNNING with no live turn."""
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING

        async def _never() -> None:
            await asyncio.Event().wait()

        live_task = asyncio.ensure_future(_never())
        session._prompt_task = live_task
        try:
            with pytest.raises(ValueError, match="live turn"):
                await session_manager.resync_session(session.session_id)
        finally:
            live_task.cancel()


class TestReconcileWedged:
    """Eventual-terminal reconciliation of sessions wedged in RUNNING."""

    @staticmethod
    def _replay_factory(replay):
        def factory(*args, on_event=None, **kwargs):
            client = MagicMock()
            client.is_running = True
            client.pid = 333
            client.acp_session_id = "acp-test-123"
            client.start = AsyncMock()
            client.shutdown = AsyncMock()
            client.cancel_prompt = AsyncMock()

            async def _load(cwd, session_id, suppress_replay=True):
                if not suppress_replay and on_event:
                    for etype, data in replay:
                        on_event(etype, data)

            client.load_session = AsyncMock(side_effect=_load)
            return client
        return factory

    @pytest.mark.asyncio
    async def test_reconciles_stalled_running_with_no_live_turn(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A RUNNING session that has stopped producing output and has no live
        prompt task is healed back to idle instead of hanging forever."""
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING
        session._prompt_task = None
        session.last_output_at = time.time() - 10_000  # stalled

        with patch("agent_bridge.session_manager.AcpClient",
                   side_effect=self._replay_factory([("agent_message", {"text": "x"})])):
            healed = await session_manager.reconcile_wedged_running()

        assert healed == 1
        assert session.status == SessionStatus.IDLE
        assert session.event_log.get_events()[-1].data.get("resynced") is True

    @pytest.mark.asyncio
    async def test_leaves_stalled_no_live_turn_within_threshold(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A client-UP `stalled` session with no live prompt task but whose
        silence is still WITHIN the live-stall threshold is left alone -- it may
        be a reattached, still-thinking turn (adopted after a restart), and
        resyncing it would land a live think IDLE (dotfiles#1276). It heals only
        once silence passes the conservative threshold."""
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING
        session._prompt_task = None
        # Stalled (>_STALL_AFTER_S) but well within the 900s interrupt threshold.
        session.last_output_at = time.time() - (_STALL_AFTER_S + 120)
        assert session.liveness_state() == "stalled"

        with patch("agent_bridge.session_manager.AcpClient",
                   side_effect=self._replay_factory([("agent_message", {"text": "x"})])):
            healed = await session_manager.reconcile_wedged_running()

        assert healed == 0
        assert session.status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_leaves_actively_progressing_turn(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A RUNNING session still producing output (liveness 'active') is never
        reconciled."""
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING
        session._prompt_task = None
        session.last_output_at = time.time()  # active

        healed = await session_manager.reconcile_wedged_running()
        assert healed == 0
        assert session.status == SessionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_leaves_live_prompt_task_within_threshold(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A RUNNING session with a live prompt task that is stalled but still
        WITHIN the live-stall interrupt threshold is left untouched -- only real
        silence past the large threshold triggers an interrupt (#2427)."""
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING
        # Stalled (>180s silent) but well within the 900s interrupt threshold.
        session.last_output_at = time.time() - 300

        async def _never() -> None:
            await asyncio.Event().wait()

        live = asyncio.ensure_future(_never())
        session._prompt_task = live
        try:
            healed = await session_manager.reconcile_wedged_running()
        finally:
            live.cancel()
        assert healed == 0
        assert session.status == SessionStatus.RUNNING
        session.client.cancel_prompt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_interrupts_live_stalled_turn_past_threshold(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A RUNNING session with a *live* prompt task that has gone silent past
        the conservative live-stall threshold is gracefully interrupted so it
        settles to IDLE with the session intact (#2427, Phase 5)."""
        session = await session_manager.start_session(spawn_target)

        cancelled = asyncio.Event()

        async def _blocking_prompt(_text):
            await cancelled.wait()
            return {
                "response_text": "", "thought_text": "", "tool_calls": [],
                "stop_reason": "cancelled", "error": None,
            }

        async def _cancel():
            cancelled.set()

        session.client.send_prompt = AsyncMock(side_effect=_blocking_prompt)
        session.client.cancel_prompt = AsyncMock(side_effect=_cancel)

        await session_manager.submit_prompt(session.session_id, "Hello")
        assert session.status == SessionStatus.RUNNING
        assert session._prompt_task is not None and not session._prompt_task.done()

        # Live-stalled: transport up, output silent far past the 900s threshold.
        session.last_output_at = time.time() - 10_000

        healed = await session_manager.reconcile_wedged_running()

        assert healed == 1
        session.client.cancel_prompt.assert_awaited()
        assert session.status == SessionStatus.IDLE
        assert session.client is not None  # session survives -- not torn down

    @pytest.mark.asyncio
    async def test_live_stall_interrupt_disabled_by_zero_threshold(
        self, tmp_db, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """With live_stall_interrupt_after_s=0 the watchdog never interrupts a
        live-stalled turn, no matter how long it has been silent (opt-out)."""
        mgr = SessionManager(tmp_db, live_stall_interrupt_after_s=0)
        session = await mgr.start_session(spawn_target)
        session.status = SessionStatus.RUNNING
        session.last_output_at = time.time() - 10_000  # stalled, long silence

        async def _never() -> None:
            await asyncio.Event().wait()

        live = asyncio.ensure_future(_never())
        session._prompt_task = live
        try:
            healed = await mgr.reconcile_wedged_running()
        finally:
            live.cancel()
        assert healed == 0
        assert session.status == SessionStatus.RUNNING
        session.client.cancel_prompt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_new_turn_after_idle_gap_not_interrupted(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """Regression (bridge-resume-phantom-cancel, #4122): a fresh turn started
        after a long idle gap must NOT be interrupted by the live-stall watchdog.

        `last_output_at` only advances on an ACP frame, so after an idle gap it
        still points at the *previous* turn's last frame. `submit_prompt` must
        reset the stall clock at turn start; otherwise the watchdog sees the
        brand-new turn as silent for the entire idle gap and cancels it before
        it emits its first frame -- the phantom "Operation cancelled by user".
        """
        session = await session_manager.start_session(spawn_target)

        cancelled = asyncio.Event()

        async def _blocking_prompt(_text):
            await cancelled.wait()
            return {
                "response_text": "", "thought_text": "", "tool_calls": [],
                "stop_reason": "cancelled", "error": None,
            }

        session.client.send_prompt = AsyncMock(side_effect=_blocking_prompt)
        session.client.cancel_prompt = AsyncMock(
            side_effect=lambda: cancelled.set()
        )

        # A completed prior turn left the stall clock ancient; then a long idle
        # gap (well past the 900s interrupt threshold).
        session.last_output_at = time.time() - 10_000

        # The operator's next message after the idle gap.
        await session_manager.submit_prompt(session.session_id, "Yes, start")
        try:
            assert session.status == SessionStatus.RUNNING
            assert (session._prompt_task is not None
                    and not session._prompt_task.done())
            # Turn start reset the silence clock so the new turn reads as active,
            # not stalled-across-the-idle-gap.
            assert session.last_output_at is not None
            assert time.time() - session.last_output_at < _STALL_AFTER_S

            # The watchdog must leave the fresh turn alone (pre-fix: interrupted).
            healed = await session_manager.reconcile_wedged_running()
            assert healed == 0
            session.client.cancel_prompt.assert_not_awaited()
            assert session.status == SessionStatus.RUNNING
        finally:
            cancelled.set()
            session._prompt_task.cancel()
            try:
                await session._prompt_task
            except BaseException:
                pass


class TestInterruptTurn:
    """Per-turn interrupt -- cancel the turn, keep the session alive (#899)."""

    @pytest.mark.asyncio
    async def test_interrupt_cancels_and_settles_to_idle(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """Interrupt sends an ACP cancel and the session settles to IDLE with
        the client and session intact (not stopped/ended)."""
        session = await session_manager.start_session(spawn_target)

        cancelled = asyncio.Event()

        async def _blocking_prompt(_text):
            await cancelled.wait()
            return {
                "response_text": "", "thought_text": "", "tool_calls": [],
                "stop_reason": "cancelled", "error": None,
            }

        async def _cancel():
            cancelled.set()

        session.client.send_prompt = AsyncMock(side_effect=_blocking_prompt)
        session.client.cancel_prompt = AsyncMock(side_effect=_cancel)

        await session_manager.submit_prompt(session.session_id, "Hello")
        assert session.status == SessionStatus.RUNNING

        result = await session_manager.interrupt_turn(session.session_id)

        session.client.cancel_prompt.assert_awaited()
        assert result is session
        assert session.status == SessionStatus.IDLE
        assert session.client is not None  # session survives -- not torn down
        state_changes = [
            e for e in session.event_log.get_events()
            if e.event == "session_state_changed"
        ]
        assert state_changes[-1].data.get("status") == "idle"

    @pytest.mark.asyncio
    async def test_interrupt_is_noop_when_idle(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """Interrupting a session with no live turn does nothing (no cancel)."""
        session = await session_manager.start_session(spawn_target)
        # Idle session, no prompt task.
        result = await session_manager.interrupt_turn(session.session_id)
        assert result is session
        session.client.cancel_prompt.assert_not_awaited()
        assert session.status == SessionStatus.IDLE

    @pytest.mark.asyncio
    async def test_interrupt_unknown_session(self, session_manager) -> None:
        with pytest.raises(KeyError):
            await session_manager.interrupt_turn("nonexistent")


class TestAnswerAskUser:
    """Answering a parked ask_user elicitation on a live session."""

    @pytest.mark.asyncio
    async def test_answer_proxies_to_client_resolve(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.client.resolve_elicitation = MagicMock(return_value=True)

        ok = await session_manager.answer_ask_user(
            session.session_id, "tc-1", {"choice": "a"}
        )

        assert ok is True
        session.client.resolve_elicitation.assert_called_once_with(
            "tc-1", {"choice": "a"}, action="accept"
        )

    @pytest.mark.asyncio
    async def test_answer_forwards_action(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.client.resolve_elicitation = MagicMock(return_value=True)

        await session_manager.answer_ask_user(
            session.session_id, "tc-1", None, action="decline"
        )

        session.client.resolve_elicitation.assert_called_once_with(
            "tc-1", None, action="decline"
        )

    @pytest.mark.asyncio
    async def test_answer_returns_false_when_nothing_pending(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.client.resolve_elicitation = MagicMock(return_value=False)

        ok = await session_manager.answer_ask_user(
            session.session_id, "tc-x", {}
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_answer_unknown_session(self, session_manager) -> None:
        with pytest.raises(KeyError):
            await session_manager.answer_ask_user("nonexistent", "tc", {})

    @pytest.mark.asyncio
    async def test_answer_no_live_client(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.client = None
        with pytest.raises(ValueError):
            await session_manager.answer_ask_user(
                session.session_id, "tc", {}
            )


class TestRehydrate:
    """Session rehydration on restart."""

    def test_rehydrate_marks_running_as_stopped(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "running", now)
        tmp_db.update_session_acp_id("s1", "acp-123")

        mgr = SessionManager(tmp_db)
        session = mgr.get_session("s1")
        assert session is not None
        assert session.status == SessionStatus.STOPPED
        assert session.acp_session_id == "acp-123"

    def test_rehydrate_marks_incomplete_turns(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "running", now)
        tmp_db.create_turn("s1", 0, "hello", now)
        # Leave turn incomplete (no completed_at)

        # Constructing the manager triggers rehydrate, which marks the
        # incomplete turn as interrupted.
        SessionManager(tmp_db)
        turn = tmp_db.get_turn("s1", 0)
        assert turn["stop_reason"] == "interrupted"
        assert turn["completed_at"] is not None

    def test_rehydrate_closes_an_open_event_trace(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "running", now)
        tmp_db.create_turn("s1", 0, "hello", now)
        log = EventLog(db=tmp_db, session_id="s1")
        log.append("user_message", {"content": "hello"})
        log.append("session_state_changed", {"status": "running"})
        tmp_db.flush()

        mgr = SessionManager(tmp_db)
        session = mgr.get_session("s1")
        assert session is not None
        events = session.event_log.get_events()
        assert [event.event for event in events[-2:]] == [
            "turn_complete",
            "session_state_changed",
        ]
        assert events[-2].data["stop_reason"] == "interrupted"
        assert events[-1].data["status"] == "stopped"

    def test_rehydrate_does_not_duplicate_an_existing_turn_complete(
        self, tmp_db: Database
    ) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "running", now)
        # Simulate the crash window where the durable event arrived but the
        # turn row had not yet been marked complete.
        tmp_db.create_turn("s1", 0, "hello", now)
        log = EventLog(db=tmp_db, session_id="s1")
        log.append("user_message", {"content": "hello"})
        log.append("session_state_changed", {"status": "running"})
        log.append("turn_complete", {"stop_reason": "end_turn"})
        tmp_db.flush()

        mgr = SessionManager(tmp_db)
        session = mgr.get_session("s1")
        assert session is not None
        events = session.event_log.get_events()
        assert sum(event.event == "turn_complete" for event in events) == 1
        assert events[-1].event == "session_state_changed"
        assert events[-1].data["status"] == "stopped"

    def test_rehydrate_cleans_ended_sessions(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "ended", now)

        mgr = SessionManager(tmp_db)
        assert mgr.get_session("s1") is None
        assert tmp_db.get_session("s1") is None

    def test_rehydrate_preserves_stopped_sessions(self, tmp_db: Database) -> None:
        now = time.time()
        tmp_db.create_session("s1", "test", None, ".", "local", "stopped", now)
        tmp_db.update_session_acp_id("s1", "acp-456")

        mgr = SessionManager(tmp_db)
        session = mgr.get_session("s1")
        assert session is not None
        assert session.status == SessionStatus.STOPPED
        assert session.acp_session_id == "acp-456"


class TestTeardownDuringDrain:
    """Teardown (stop/end) must stay permitted while draining (#1755).

    The drain gate blocks only *new* work (create session / submit turn);
    stop/end are exactly what let the busy sessions the drain waits on settle,
    so gating them self-deadlocks a redeploy.
    """

    @pytest.mark.asyncio
    async def test_stop_allowed_while_draining(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session_manager.set_draining(True, source="test")
        await session_manager.stop_session(session.session_id)
        assert session.status == SessionStatus.STOPPED
        # The gate is untouched by teardown -- it stays open for the redeploy.
        assert session_manager.is_draining is True

    @pytest.mark.asyncio
    async def test_end_allowed_while_draining(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        session_manager.set_draining(True, source="test")
        await session_manager.end_session(sid)
        assert session_manager.get_session(sid) is None
        assert session_manager.is_draining is True

    @pytest.mark.asyncio
    async def test_create_and_turn_blocked_while_draining(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        from agent_bridge.session_manager import DaemonDrainingError

        session = await session_manager.start_session(spawn_target)
        session_manager.set_draining(True, source="test")
        # New work is refused...
        with pytest.raises(DaemonDrainingError):
            await session_manager.start_session(spawn_target)
        with pytest.raises(DaemonDrainingError):
            await session_manager.submit_prompt(session.session_id, "hi")
        # ...but teardown of the existing session still succeeds.
        await session_manager.end_session(session.session_id)
        assert session_manager.get_session(session.session_id) is None


class TestCodespaceRequiresSessionHost:
    """A CodeSpace target must never silently fall to classic (non-survivable)
    mode -- ``connect`` refuses it, keyed off ``_is_codespace_target``."""

    def test_detects_structured_codespace_metadata(self, tmp_db) -> None:
        from agent_bridge.transport import SpawnTarget
        mgr = SessionManager(tmp_db)
        meta = {"name": "cs-foo", "repo": "org/repo",
                "acp_command": "cd /workspaces/x && copilot --acp --stdio"}
        t = SpawnTarget(type="command", spawn_command=["x"], codespace=meta)
        assert mgr._is_codespace_target(t) is True

    def test_detects_codespace_shaped_spawn_command(self, tmp_db) -> None:
        from agent_bridge.transport import SpawnTarget
        mgr = SessionManager(tmp_db)
        cmd = [
            "python", "-m", "agent_codespaces", "ssh", "--stdio",
            "cs-x", "--repo", "org/repo-codespaces",
            "--remote-cmd", "cd /workspaces/repo && copilot --acp --stdio",
        ]
        t = SpawnTarget(type="command", spawn_command=cmd)
        assert mgr._is_codespace_target(t) is True

    def test_local_and_ssh_targets_are_not_codespace(self, tmp_db) -> None:
        from agent_bridge.transport import SpawnTarget
        mgr = SessionManager(tmp_db)
        assert mgr._is_codespace_target(SpawnTarget(type="local", cwd="/wt")) is False
        assert mgr._is_codespace_target(
            SpawnTarget(type="ssh", host="h", cwd="/w")) is False
        # agent_codespaces but not an stdio launch -> not a host-required target.
        assert mgr._is_codespace_target(SpawnTarget(
            type="command",
            spawn_command=["python", "-m", "agent_codespaces", "ssh", "cs-x",
                           "--remote-cmd", "ls"],
        )) is False


class TestDurablePromptQueue:
    """Durable send-or-queue (pending_prompts) + drain-on-settle (#4114)."""

    async def _settle_chain(self, mgr, session, sid, limit: int = 20) -> None:
        """Await the chain of drained turns until IDLE with an empty queue.

        Each settled turn's tail drains one queued prompt by scheduling a fresh
        prompt task, so follow the reassigned ``_prompt_task`` until the queue
        empties.
        """
        for _ in range(limit):
            task = session._prompt_task
            if task is not None and not task.done():
                await task
            if (session.status == SessionStatus.IDLE
                    and mgr._db.count_pending_prompts(sid) == 0):
                return
        raise AssertionError("queue did not drain")

    @pytest.mark.asyncio
    async def test_runs_immediately_when_idle(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        result = await session_manager.submit_or_queue_prompt(
            session.session_id, "Hello"
        )
        assert result["queued"] is False
        assert result["turn_index"] == 0
        assert session_manager._db.count_pending_prompts(session.session_id) == 0

    @pytest.mark.asyncio
    async def test_queues_when_running(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        session.status = SessionStatus.RUNNING  # simulate a live turn
        r1 = await session_manager.submit_or_queue_prompt(
            session.session_id, "one", caller_id="op"
        )
        r2 = await session_manager.submit_or_queue_prompt(session.session_id, "two")
        assert r1["queued"] is True and r1["position"] == 1
        assert r2["queued"] is True and r2["position"] == 2
        rows = session_manager._db.list_pending_prompts(session.session_id)
        assert [r["prompt"] for r in rows] == ["one", "two"]
        assert rows[0]["caller_id"] == "op"

    @pytest.mark.asyncio
    async def test_drains_fifo_exactly_once_on_settle(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp, mock_acp_client
    ) -> None:
        """The core guarantee: follow-ups queued during a turn drain in FIFO
        order, one per turn, each exactly once."""
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        # First prompt runs; two follow-ups queue behind it.
        await session_manager.submit_or_queue_prompt(sid, "A")
        assert session.status == SessionStatus.RUNNING
        await session_manager.submit_or_queue_prompt(sid, "B")
        await session_manager.submit_or_queue_prompt(sid, "C")
        assert session_manager._db.count_pending_prompts(sid) == 2

        await self._settle_chain(session_manager, session, sid)

        sent = [c.args[0] for c in mock_acp_client.send_prompt.call_args_list]
        assert sent == ["A", "B", "C"]
        assert session.status == SessionStatus.IDLE
        assert session_manager._db.count_pending_prompts(sid) == 0

    @pytest.mark.asyncio
    async def test_interrupt_clears_queue(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        # A live turn with queued follow-ups.
        await session_manager.submit_or_queue_prompt(sid, "A")
        await session_manager.submit_or_queue_prompt(sid, "B")
        assert session_manager._db.count_pending_prompts(sid) >= 1
        await session_manager.interrupt_turn(sid)
        assert session_manager._db.count_pending_prompts(sid) == 0
        events = [e.event for e in session.event_log.get_events()]
        assert "queue_cleared" in events

    @pytest.mark.asyncio
    async def test_end_clears_queue(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        session.status = SessionStatus.RUNNING
        await session_manager.submit_or_queue_prompt(sid, "A")
        assert session_manager._db.count_pending_prompts(sid) == 1
        await session_manager.end_session(sid, force=True)
        assert session_manager._db.count_pending_prompts(sid) == 0

    @pytest.mark.asyncio
    async def test_stop_preserves_queue(
        self, session_manager, spawn_target, _patch_spawn, _patch_acp
    ) -> None:
        """A plain stop (redeploy/idle-reap path) must NOT clear the queue --
        the durable rows survive so a later resume delivers them."""
        session = await session_manager.start_session(spawn_target)
        sid = session.session_id
        session.status = SessionStatus.RUNNING
        await session_manager.submit_or_queue_prompt(sid, "A")
        session.status = SessionStatus.IDLE  # stop refuses mid-turn; idle it
        await session_manager.stop_session(sid)
        assert session.status == SessionStatus.STOPPED
        assert session_manager._db.count_pending_prompts(sid) == 1


class TestCodespaceExclusiveClaim:
    """#897 Increment B step 2: the Session-Host CodeSpace dispatch path
    acquires an exclusive, worktree-keyed claim before establishing the
    transport (a second worktree is BOUNCED), and releases it on session end.

    Session-Host dispatch never runs ``agent-codespaces ssh`` (the direct-path
    enforcement point), so the daemon shells the ``agent-codespaces claim`` seam
    -- these tests mock that seam (``_claim_codespace`` / ``_release_codespace_claim``)
    to keep unit tests off real ``~/.agent-codespaces/leases.json`` state.
    """

    @staticmethod
    def _cs_target(
        caller_worktree: str | None = None,
        caller_owner_ref: str | None = None,
    ) -> SpawnTarget:
        acp = "cd /workspaces/example && copilot --acp --stdio --allow-all-tools"
        return SpawnTarget(
            type="command",
            cwd="/workspaces/example",
            caller_worktree=caller_worktree,
            caller_owner_ref=caller_owner_ref,
            codespace={
                "name": "example-codespace",
                "repo": "example/repo",
                "acp_command": acp,
                "workspace_folder": "/workspaces/example",
            },
        )

    async def _start(
        self,
        tmp_db,
        monkeypatch,
        *,
        claim_result,
        caller_worktree,
        caller_id=None,
        caller_owner_ref=None,
    ):
        monkeypatch.setattr(
            "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
            lambda *a, **k: SimpleNamespace(transport=object()),
        )
        monkeypatch.setattr(
            "agent_bridge.session_manager._resolve_remote_ai_plugin_dirs",
            AsyncMock(return_value=[]),
        )
        claim_calls: list[tuple[str, str, str | None]] = []

        def fake_claim(name, owner, *, holder_ref=None):
            claim_calls.append((name, owner, holder_ref))
            return claim_result

        monkeypatch.setattr(
            "agent_bridge.session_manager._claim_codespace", fake_claim,
        )

        async def fake_connect(self, target, **kwargs):
            client = MagicMock()
            client.is_running = True
            client.pid = 12345
            return client, "acp-test-123"

        monkeypatch.setattr(
            SessionManager, "_connect_via_session_host", fake_connect,
        )
        manager = SessionManager(tmp_db)
        session = await manager.start_session(
            self._cs_target(caller_worktree, caller_owner_ref),
            agent_name="codespace:example",
            caller_id=caller_id,
        )
        return manager, session, claim_calls

    @pytest.mark.asyncio
    async def test_claim_acquired_with_caller_worktree_owner(
        self, tmp_db, monkeypatch
    ) -> None:
        """A successful claim is keyed by the caller's worktree and the session
        comes up IDLE."""
        _, session, claim_calls = await self._start(
            tmp_db, monkeypatch,
            claim_result=("ok", ""),
            caller_worktree="/wt/dispatcher-a",
        )
        assert claim_calls == [
            ("example-codespace", "/wt/dispatcher-a", None)
        ]
        assert session.status == SessionStatus.IDLE

    @pytest.mark.asyncio
    async def test_claim_owner_falls_back_to_caller_id(
        self, tmp_db, monkeypatch
    ) -> None:
        """With no pre-bound caller_worktree, the claim owner is the caller_id
        (bound onto the target as caller_worktree earlier in start_session)."""
        _, _session, claim_calls = await self._start(
            tmp_db, monkeypatch,
            claim_result=("ok", ""),
            caller_worktree=None,
            caller_id="/wt/dispatcher-b",
        )
        assert claim_calls == [
            ("example-codespace", "/wt/dispatcher-b", None)
        ]

    @pytest.mark.asyncio
    async def test_claim_forwards_caller_owner_ref(
        self, tmp_db, monkeypatch
    ) -> None:
        _, session, claim_calls = await self._start(
            tmp_db,
            monkeypatch,
            claim_result=("ok", ""),
            caller_worktree="/wt/dispatcher-a",
            caller_owner_ref="machine/project/worktree",
        )
        assert claim_calls == [
            (
                "example-codespace",
                "/wt/dispatcher-a",
                "machine/project/worktree",
            )
        ]
        assert session.status == SessionStatus.IDLE

    @pytest.mark.asyncio
    async def test_conflict_bounces_session_failed(
        self, tmp_db, monkeypatch
    ) -> None:
        """A live claim conflict BOUNCES the dispatch: the session ends FAILED
        with a distinguishable ``codespace_claim_conflict`` event, not an opaque
        transport error."""
        _, session, _calls = await self._start(
            tmp_db, monkeypatch,
            claim_result=("conflict", "[BUSY] held by /wt/other"),
            caller_worktree="/wt/dispatcher-a",
        )
        assert session.status == SessionStatus.FAILED
        types = [e.event for e in session.event_log.get_events()]
        assert "codespace_claim_conflict" in types

    @pytest.mark.asyncio
    async def test_coordination_rejection_blocks_transport(
        self, tmp_db, monkeypatch
    ) -> None:
        _, session, _calls = await self._start(
            tmp_db,
            monkeypatch,
            claim_result=(
                "coordination-rejected",
                "[BLOCKED] knowledge_binding_required",
            ),
            caller_worktree="/wt/dispatcher-a",
            caller_owner_ref="machine/project/worktree",
        )
        assert session.status == SessionStatus.FAILED
        types = [e.event for e in session.event_log.get_events()]
        assert "codespace_coordination_rejected" in types

    @pytest.mark.asyncio
    async def test_end_session_releases_claim(
        self, tmp_db, monkeypatch
    ) -> None:
        """Ending a claimed CodeSpace session releases the claim (keyed off the
        persisted target: codespace name + caller worktree owner)."""
        released: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "agent_bridge.session_manager._release_codespace_claim",
            lambda name, owner: released.append((name, owner)),
        )
        manager, session, _calls = await self._start(
            tmp_db, monkeypatch,
            claim_result=("ok", ""),
            caller_worktree="/wt/dispatcher-a",
        )
        await manager.end_session(session.session_id, force=True)
        assert released == [("example-codespace", "/wt/dispatcher-a")]


class TestClaimCodespaceHelper:
    """``_claim_codespace`` shells ``agent-codespaces claim`` and maps its exit
    code: 75 -> conflict (bounce), 0/other -> proceed (degrade-safe)."""

    def test_conflict_on_busy_exit(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        monkeypatch.setattr(
            sm.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=75, stdout="", stderr="[BUSY] x"),
        )
        status, detail = sm._claim_codespace("cs", "/wt/a")
        assert status == "conflict"
        assert "BUSY" in detail

    def test_success_on_zero_exit(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        monkeypatch.setattr(
            sm.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="[OK]", stderr=""),
        )
        assert sm._claim_codespace("cs", "/wt/a") == ("ok", "")

    def test_other_nonzero_is_degrade_safe(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        monkeypatch.setattr(
            sm.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="boom"),
        )
        assert sm._claim_codespace("cs", "/wt/a") == ("ok", "")

    def test_coordination_rejection_is_blocking(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        seen = {}

        def run(command, **kwargs):
            seen["command"] = command
            return SimpleNamespace(
                returncode=78,
                stdout="",
                stderr="[BLOCKED] knowledge_binding_required",
            )

        monkeypatch.setattr(sm.subprocess, "run", run)
        result = sm._claim_codespace(
            "cs",
            "/wt/a",
            holder_ref="machine/project/worktree",
        )
        assert result[0] == "coordination-rejected"
        assert seen["command"][-2:] == [
            "--holder-ref",
            "machine/project/worktree",
        ]

    def test_no_owner_is_skip(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        called = {"ran": False}

        def _run(*a, **k):
            called["ran"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", _run)
        assert sm._claim_codespace("cs", "") == ("ok", "")
        assert called["ran"] is False

    def test_missing_binstub_is_skip(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: None)
        assert sm._claim_codespace("cs", "/wt/a") == ("ok", "")

    def test_disabled_env_is_skip(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
        called = {"ran": False}

        def _run(*a, **k):
            called["ran"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", _run)
        assert sm._claim_codespace("cs", "/wt/a") == ("ok", "")
        assert called["ran"] is False

    def test_disabled_env_with_holder_ref_still_preflights(
        self, monkeypatch
    ) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        seen = {}

        def run(command, **kwargs):
            seen["command"] = command
            return SimpleNamespace(
                returncode=78,
                stdout="",
                stderr="[BLOCKED] knowledge_binding_required",
            )

        monkeypatch.setattr(sm.subprocess, "run", run)
        status, detail = sm._claim_codespace(
            "cs",
            "/wt/a",
            holder_ref="machine/project/worktree",
        )
        assert status == "coordination-rejected"
        assert "knowledge_binding_required" in detail
        assert "--holder-ref" in seen["command"]


class TestReleaseCodespaceClaimHelper:
    def test_missing_binstub_is_not_confirmed(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: None)

        assert sm._release_codespace_claim("cs", "/wt/a") is False

    def test_disabled_claim_needs_no_release(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")

        assert sm._release_codespace_claim("cs", "/wt/a") is True


class TestCodespaceClaimKey:
    """``_codespace_claim_key`` resolves (name, owner) from a target for the
    end-session release, deterministically from persisted state."""

    def test_structured_codespace_dict(self) -> None:
        from agent_bridge import session_manager as sm

        t = SpawnTarget(
            type="command", cwd="/workspaces/x", caller_worktree="/wt/a",
            codespace={"name": "cs-1", "repo": "o/r", "acp_command": "c"},
        )
        assert sm._codespace_claim_key(t) == ("cs-1", "/wt/a")

    def test_no_owner_returns_none(self) -> None:
        from agent_bridge import session_manager as sm

        t = SpawnTarget(
            type="command", cwd="/workspaces/x",
            codespace={"name": "cs-1", "repo": "o/r", "acp_command": "c"},
        )
        assert sm._codespace_claim_key(t) is None

    def test_non_codespace_returns_none(self) -> None:
        from agent_bridge import session_manager as sm

        t = SpawnTarget(type="local", cwd="/tmp", caller_worktree="/wt/a")
        assert sm._codespace_claim_key(t) is None
