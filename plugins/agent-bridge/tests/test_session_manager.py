"""Tests for SessionManager lifecycle operations."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import (
    Session,
    SessionManager,
    _default_cwd,
    _STALL_AFTER_S,
)
from agent_bridge.transport import SpawnTarget


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
    """Patch AcpClient construction to return a mock."""
    with patch("agent_bridge.session_manager.AcpClient") as mock_cls:
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
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        lambda *args, **kwargs: object(),
    )
    manager = SessionManager(tmp_db, session_host_enabled=True)
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
            tmp_db, session_host_enabled=True, session_host_state_dir=str(tmp_path),
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
            tmp_db, session_host_enabled=True, session_host_state_dir=str(tmp_path),
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
            tmp_db, session_host_enabled=True, session_host_state_dir=str(tmp_path),
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
            tmp_db, session_host_enabled=True, session_host_state_dir=str(tmp_path),
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

            session = await session_manager.start_session(spawn_target)
            await session_manager.stop_session(session.session_id)

        # Now resume with failing ACP
        with patch("agent_bridge.session_manager.AcpClient") as mock_cls:
            fail_client = MagicMock()
            fail_client.start = AsyncMock(side_effect=RuntimeError("spawn failed"))
            fail_client.shutdown = AsyncMock()
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

    @pytest.mark.asyncio
    async def test_codespace_target_fails_loud_instead_of_classic(self, tmp_db) -> None:
        """With host mode OFF, starting a CodeSpace target must FAIL LOUD (a
        clear session-host-required error) rather than silently degrade to the
        classic, non-survivable process-owned path."""
        from agent_bridge.transport import SpawnTarget
        mgr = SessionManager(tmp_db, session_host_enabled=False)
        meta = {"name": "cs-foo", "repo": "org/repo",
                "acp_command": "cd /workspaces/x && copilot --acp --stdio"}
        target = SpawnTarget(type="command", spawn_command=["x"], codespace=meta)
        session = await mgr.start_session(target, agent_name="cs-foo")
        assert session.status == SessionStatus.FAILED
        msgs = [e.data.get("message", "") for e in session.event_log.get_events()
                if e.event == "error"]
        assert any("session-host mode" in m for m in msgs), msgs


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
    def _cs_target(caller_worktree: str | None = None) -> SpawnTarget:
        acp = "cd /workspaces/example && copilot --acp --stdio --allow-all-tools"
        return SpawnTarget(
            type="command",
            cwd="/workspaces/example",
            caller_worktree=caller_worktree,
            codespace={
                "name": "example-codespace",
                "repo": "example/repo",
                "acp_command": acp,
                "workspace_folder": "/workspaces/example",
            },
        )

    async def _start(self, tmp_db, monkeypatch, *, claim_result, caller_worktree,
                     caller_id=None):
        monkeypatch.setattr(
            "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
            lambda *a, **k: object(),
        )
        claim_calls: list[tuple[str, str]] = []

        def fake_claim(name, owner):
            claim_calls.append((name, owner))
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
        manager = SessionManager(tmp_db, session_host_enabled=True)
        session = await manager.start_session(
            self._cs_target(caller_worktree),
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
            claim_result=(True, ""),
            caller_worktree="/wt/dispatcher-a",
        )
        assert claim_calls == [("example-codespace", "/wt/dispatcher-a")]
        assert session.status == SessionStatus.IDLE

    @pytest.mark.asyncio
    async def test_claim_owner_falls_back_to_caller_id(
        self, tmp_db, monkeypatch
    ) -> None:
        """With no pre-bound caller_worktree, the claim owner is the caller_id
        (bound onto the target as caller_worktree earlier in start_session)."""
        _, _session, claim_calls = await self._start(
            tmp_db, monkeypatch,
            claim_result=(True, ""),
            caller_worktree=None,
            caller_id="/wt/dispatcher-b",
        )
        assert claim_calls == [("example-codespace", "/wt/dispatcher-b")]

    @pytest.mark.asyncio
    async def test_conflict_bounces_session_failed(
        self, tmp_db, monkeypatch
    ) -> None:
        """A live claim conflict BOUNCES the dispatch: the session ends FAILED
        with a distinguishable ``codespace_claim_conflict`` event, not an opaque
        transport error."""
        _, session, _calls = await self._start(
            tmp_db, monkeypatch,
            claim_result=(False, "[BUSY] held by /wt/other"),
            caller_worktree="/wt/dispatcher-a",
        )
        assert session.status == SessionStatus.FAILED
        types = [e.event for e in session.event_log.get_events()]
        assert "codespace_claim_conflict" in types

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
            claim_result=(True, ""),
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
        ok, detail = sm._claim_codespace("cs", "/wt/a")
        assert ok is False
        assert "BUSY" in detail

    def test_success_on_zero_exit(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        monkeypatch.setattr(
            sm.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=0, stdout="[OK]", stderr=""),
        )
        assert sm._claim_codespace("cs", "/wt/a") == (True, "")

    def test_other_nonzero_is_degrade_safe(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: "/usr/bin/agent-codespaces")
        monkeypatch.setattr(
            sm.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr="boom"),
        )
        assert sm._claim_codespace("cs", "/wt/a") == (True, "")

    def test_no_owner_is_skip(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        called = {"ran": False}

        def _run(*a, **k):
            called["ran"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", _run)
        assert sm._claim_codespace("cs", "") == (True, "")
        assert called["ran"] is False

    def test_missing_binstub_is_skip(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
        monkeypatch.setattr(sm.shutil, "which", lambda _: None)
        assert sm._claim_codespace("cs", "/wt/a") == (True, "")

    def test_disabled_env_is_skip(self, monkeypatch) -> None:
        from agent_bridge import session_manager as sm

        monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
        called = {"ran": False}

        def _run(*a, **k):
            called["ran"] = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(sm.subprocess, "run", _run)
        assert sm._claim_codespace("cs", "/wt/a") == (True, "")
        assert called["ran"] is False


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
