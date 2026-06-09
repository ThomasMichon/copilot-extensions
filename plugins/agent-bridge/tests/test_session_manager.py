"""Tests for SessionManager lifecycle operations."""

from __future__ import annotations

import asyncio
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
    async def test_submit_unknown_session(self, session_manager) -> None:
        with pytest.raises(KeyError):
            await session_manager.submit_prompt("nonexistent", "Hello")


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

        mgr = SessionManager(tmp_db)
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
