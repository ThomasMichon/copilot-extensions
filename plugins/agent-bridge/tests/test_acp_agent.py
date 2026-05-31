"""Tests for the upstream ACP agent interface (acp_agent.py)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.acp_agent import (
    BridgeAgent,
    _event_to_acp_update,
    _extract_text,
    _normalize_stop_reason,
)
from agent_bridge.db import Database
from agent_bridge.events import EventLog, SseEvent
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ClientCapabilities,
    Implementation,
    TextContentBlock,
    ToolCallStart,
    ToolCallProgress,
    UsageUpdate,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sm(tmp_path):
    """SessionManager with temp DB."""
    db = Database(tmp_path / "test.db")
    return SessionManager(db)


@pytest.fixture
def bridge_agent(sm):
    """BridgeAgent with no resolver (local-only)."""
    return BridgeAgent(sm, default_agent="test-agent")


@pytest.fixture
def mock_conn():
    """Mock AgentSideConnection."""
    conn = AsyncMock()
    conn.session_update = AsyncMock()
    conn.request_permission = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_single_text_block(self):
        blocks = [TextContentBlock(type="text", text="Hello")]
        assert _extract_text(blocks) == "Hello"

    def test_multiple_text_blocks(self):
        blocks = [
            TextContentBlock(type="text", text="Hello "),
            TextContentBlock(type="text", text="world"),
        ]
        assert _extract_text(blocks) == "Hello world"

    def test_empty_prompt_raises(self):
        with pytest.raises(Exception):
            _extract_text([])


# ---------------------------------------------------------------------------
# Stop reason normalization
# ---------------------------------------------------------------------------


class TestNormalizeStopReason:
    def test_valid_reasons(self):
        assert _normalize_stop_reason("end_turn") == "end_turn"
        assert _normalize_stop_reason("cancelled") == "cancelled"
        assert _normalize_stop_reason("max_tokens") == "max_tokens"

    def test_invalid_reason(self):
        assert _normalize_stop_reason("error: crash") == "end_turn"
        assert _normalize_stop_reason(None) == "end_turn"
        assert _normalize_stop_reason("") == "end_turn"


# ---------------------------------------------------------------------------
# Event-to-ACP update conversion
# ---------------------------------------------------------------------------


class TestEventToAcpUpdate:
    def test_agent_message(self):
        event = SseEvent(id=1, event="agent_message", data={"text": "Hi"})
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, AgentMessageChunk)

    def test_agent_thought(self):
        event = SseEvent(id=2, event="agent_thought", data={"text": "Thinking"})
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, AgentThoughtChunk)

    def test_tool_call_start(self):
        event = SseEvent(
            id=3, event="tool_call_start",
            data={"tool_call_id": "tc1", "title": "Read file", "kind": "read"},
        )
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, ToolCallStart)
        assert update.tool_call_id == "tc1"
        assert update.title == "Read file"

    def test_tool_call_update(self):
        event = SseEvent(
            id=4, event="tool_call_update",
            data={"tool_call_id": "tc1", "status": "completed"},
        )
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, ToolCallProgress)

    def test_usage_update(self):
        event = SseEvent(
            id=5, event="usage_update",
            data={"input_tokens": 100, "output_tokens": 50, "model": "gpt-4"},
        )
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, UsageUpdate)
        assert update.size == 100
        assert update.used == 50

    def test_session_state_changed_returns_none(self):
        event = SseEvent(
            id=6, event="session_state_changed",
            data={"status": "idle"},
        )
        assert _event_to_acp_update(event) is None

    def test_empty_text_returns_none(self):
        event = SseEvent(id=7, event="agent_message", data={"text": ""})
        assert _event_to_acp_update(event) is None


# ---------------------------------------------------------------------------
# BridgeAgent initialization and protocol
# ---------------------------------------------------------------------------


class TestBridgeAgentInitialize:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, bridge_agent):
        resp = await bridge_agent.initialize(
            protocol_version=1,
            client_info=Implementation(name="test-client", version="1.0"),
        )
        assert resp.protocol_version > 0
        assert resp.agent_info.name == "agent-bridge"
        assert resp.agent_capabilities.load_session is True
        assert resp.agent_capabilities.session_capabilities is not None

    @pytest.mark.asyncio
    async def test_on_connect_stores_connection(self, bridge_agent, mock_conn):
        bridge_agent.on_connect(mock_conn)
        assert bridge_agent._conn is mock_conn


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestBridgeAgentSessions:
    @pytest.mark.asyncio
    async def test_new_session(self, bridge_agent, mock_conn):
        bridge_agent.on_connect(mock_conn)

        with patch("agent_bridge.session_manager.spawn") as mock_spawn:
            mock_proc = MagicMock()
            mock_proc.proc = AsyncMock()
            mock_proc.proc.stdin = MagicMock()
            mock_proc.proc.stdout = MagicMock()
            mock_proc.proc.stderr = None
            mock_proc.proc.returncode = None
            mock_proc.proc.pid = 9999
            mock_spawn.return_value = mock_proc

            with patch("agent_bridge.session_manager.AcpClient") as MockClient:
                client_inst = AsyncMock()
                client_inst.is_running = True
                client_inst.pid = 9999
                client_inst.acp_session_id = "acp-123"
                client_inst.new_session = AsyncMock(return_value="acp-123")
                MockClient.return_value = client_inst

                resp = await bridge_agent.new_session(cwd="/tmp/test")

                assert resp.session_id is not None
                assert resp.session_id in bridge_agent._owned_sessions

    @pytest.mark.asyncio
    async def test_close_session(self, bridge_agent, sm):
        # Create a fake session in the manager
        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        sm._sessions["s1"] = session
        sm._db.create_session("s1", "test", None, "/tmp", "local", "idle", time.time())

        bridge_agent._owned_sessions.add("s1")
        resp = await bridge_agent.close_session("s1")
        assert resp is not None
        assert "s1" not in bridge_agent._owned_sessions

    @pytest.mark.asyncio
    async def test_list_sessions_only_owned(self, bridge_agent, sm):
        target = SpawnTarget(type="local", cwd="/tmp")

        # Create two sessions -- only one owned
        for sid in ("s1", "s2"):
            session = Session(sid, sid, target)
            session.status = SessionStatus.IDLE
            session.event_log = EventLog()
            sm._sessions[sid] = session

        bridge_agent._owned_sessions.add("s1")
        resp = await bridge_agent.list_sessions()
        assert len(resp.sessions) == 1
        assert resp.sessions[0].session_id == "s1"


# ---------------------------------------------------------------------------
# Prompt forwarding
# ---------------------------------------------------------------------------


class TestBridgeAgentPrompt:
    @pytest.mark.asyncio
    async def test_prompt_forwards_events(self, bridge_agent, sm, mock_conn):
        bridge_agent.on_connect(mock_conn)

        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        session.turn_count = 0
        sm._sessions["s1"] = session
        sm._db.create_session("s1", "test", None, "/tmp", "local", "idle", time.time())

        # Mock the ACP client
        mock_client = AsyncMock()
        mock_client.is_running = True

        async def _fake_prompt(text):
            # Simulate events appearing in EventLog during prompt
            session.event_log.append("agent_message", {"text": "Hello"})
            session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
            return {
                "response_text": "Hello",
                "thought_text": "",
                "tool_calls": [],
                "stop_reason": "end_turn",
                "error": None,
            }

        mock_client.send_prompt = _fake_prompt
        session.client = mock_client

        prompt_blocks = [TextContentBlock(type="text", text="Hi")]
        resp = await bridge_agent.prompt(prompt_blocks, "s1")

        assert resp.stop_reason == "end_turn"
        # session_update should have been called for agent_message
        assert mock_conn.session_update.call_count >= 1

    @pytest.mark.asyncio
    async def test_prompt_error_returns_end_turn(self, bridge_agent, sm, mock_conn):
        bridge_agent.on_connect(mock_conn)

        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        session.turn_count = 0
        sm._sessions["s1"] = session
        sm._db.create_session("s1", "test", None, "/tmp", "local", "idle", time.time())

        mock_client = AsyncMock()
        mock_client.is_running = True

        async def _failing_prompt(text):
            session.event_log.append("error", {"message": "crash"})
            raise RuntimeError("crash")

        mock_client.send_prompt = _failing_prompt
        session.client = mock_client

        prompt_blocks = [TextContentBlock(type="text", text="Hi")]
        resp = await bridge_agent.prompt(prompt_blocks, "s1")

        assert resp.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Unsupported methods
# ---------------------------------------------------------------------------


class TestBridgeAgentUnsupported:
    @pytest.mark.asyncio
    async def test_fork_session_raises(self, bridge_agent):
        with pytest.raises(Exception):
            await bridge_agent.fork_session(cwd="/tmp", session_id="s1")

    @pytest.mark.asyncio
    async def test_set_session_model_raises(self, bridge_agent):
        with pytest.raises(Exception):
            await bridge_agent.set_session_model(model_id="gpt-4", session_id="s1")

    @pytest.mark.asyncio
    async def test_ext_method_raises(self, bridge_agent):
        with pytest.raises(Exception):
            await bridge_agent.ext_method("custom/method", {})


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestBridgeAgentCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stops_owned_sessions(self, bridge_agent, sm):
        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        session.client = AsyncMock()
        session.client.is_running = True
        session.client.cancel_prompt = AsyncMock()
        session.client.shutdown = AsyncMock()
        sm._sessions["s1"] = session
        sm._db.create_session("s1", "test", None, "/tmp", "local", "idle", time.time())

        bridge_agent._owned_sessions.add("s1")
        await bridge_agent.cleanup()

        assert len(bridge_agent._owned_sessions) == 0
        assert session.status == SessionStatus.STOPPED


# ---------------------------------------------------------------------------
# Permission callback
# ---------------------------------------------------------------------------


class TestPermissionCallback:
    @pytest.mark.asyncio
    async def test_make_permission_callback_forwards_upstream(self, bridge_agent, mock_conn):
        from acp.schema import AllowedOutcome, RequestPermissionResponse

        bridge_agent.on_connect(mock_conn)
        mock_conn.request_permission.return_value = RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id="allow_always"),
        )

        cb = bridge_agent._make_permission_callback()
        resp = await cb("session-1", [], MagicMock())

        mock_conn.request_permission.assert_called_once()
        assert resp.outcome.outcome == "selected"

    @pytest.mark.asyncio
    async def test_make_permission_callback_auto_approves_without_conn(self, bridge_agent):
        # No connection set
        cb = bridge_agent._make_permission_callback()
        resp = await cb("session-1", [], MagicMock())
        assert resp.outcome.option_id == "allow_always"
