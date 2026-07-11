"""Tests for SessionManager lifecycle operations."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.db import Database
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import SessionManager
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

