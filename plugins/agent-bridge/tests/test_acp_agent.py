"""Tests for the upstream ACP agent interface (acp_agent.py)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, TypeAlias, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp import RequestError
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    AudioContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    Implementation,
    ResourceContentBlock,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

from agent_bridge.acp_agent import (
    BridgeAgent,
    _event_to_acp_update,
    _extract_text,
    _normalize_stop_reason,
    _singleton_slot_locks,
)
from agent_bridge.db import Database
from agent_bridge.events import EventLog, SseEvent
from agent_bridge.agent_registry import AgentResolver
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.session_status import SessionStatus
from agent_bridge.transport import SpawnTarget

_PromptBlock: TypeAlias = (
    TextContentBlock | ImageContentBlock | AudioContentBlock
    | ResourceContentBlock | EmbeddedResourceContentBlock
)

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sm(tmp_path: Path) -> SessionManager:
    """SessionManager with temp DB."""
    db = Database(tmp_path / "test.db")
    return SessionManager(db)


@pytest.fixture
def bridge_agent(sm: SessionManager) -> BridgeAgent:
    """BridgeAgent with no resolver (local-only)."""
    return BridgeAgent(sm, default_agent="test-agent")


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Mock AgentSideConnection."""
    conn = AsyncMock()
    conn.session_update = AsyncMock()
    conn.request_permission = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_single_text_block(self) -> None:
        blocks: list[_PromptBlock] = [TextContentBlock(type="text", text="Hello")]
        assert _extract_text(blocks) == "Hello"

    def test_multiple_text_blocks(self) -> None:
        blocks: list[_PromptBlock] = [
            TextContentBlock(type="text", text="Hello "),
            TextContentBlock(type="text", text="world"),
        ]
        assert _extract_text(blocks) == "Hello world"

    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(RequestError) as exc_info:
            _extract_text([])
        assert exc_info.value.to_error_obj() == {
            "code": -32602,
            "message": "Invalid params",
            "data": {"reason": "Prompt must contain at least one text block"},
        }


# ---------------------------------------------------------------------------
# Stop reason normalization
# ---------------------------------------------------------------------------


class TestNormalizeStopReason:
    def test_valid_reasons(self) -> None:
        assert _normalize_stop_reason("end_turn") == "end_turn"
        assert _normalize_stop_reason("cancelled") == "cancelled"
        assert _normalize_stop_reason("max_tokens") == "max_tokens"

    def test_invalid_reason(self) -> None:
        assert _normalize_stop_reason("error: crash") == "end_turn"
        assert _normalize_stop_reason(None) == "end_turn"
        assert _normalize_stop_reason("") == "end_turn"


# ---------------------------------------------------------------------------
# Event-to-ACP update conversion
# ---------------------------------------------------------------------------


class TestEventToAcpUpdate:
    def test_agent_message(self) -> None:
        event = SseEvent(id=1, event="agent_message", data={"text": "Hi"})
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, AgentMessageChunk)
        assert update.model_dump(mode="json", by_alias=True, exclude_none=True) == {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hi"},
        }

    def test_agent_thought(self) -> None:
        event = SseEvent(id=2, event="agent_thought", data={"text": "Thinking"})
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, AgentThoughtChunk)
        assert update.model_dump(mode="json", by_alias=True, exclude_none=True) == {
            "sessionUpdate": "agent_thought_chunk",
            "content": {"type": "text", "text": "Thinking"},
        }

    def test_tool_call_start(self) -> None:
        event = SseEvent(
            id=3, event="tool_call_start",
            data={"tool_call_id": "tc1", "title": "Read file", "kind": "read"},
        )
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, ToolCallStart)
        assert update.tool_call_id == "tc1"
        assert update.title == "Read file"
        assert update.model_dump(mode="json", by_alias=True, exclude_none=True) == {
            "sessionUpdate": "tool_call", "toolCallId": "tc1", "title": "Read file", "kind": "read",
        }

    def test_tool_call_update(self) -> None:
        event = SseEvent(
            id=4, event="tool_call_update",
            data={"tool_call_id": "tc1", "status": "completed"},
        )
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, ToolCallProgress)
        assert update.model_dump(mode="json", by_alias=True, exclude_none=True) == {
            "sessionUpdate": "tool_call_update", "toolCallId": "tc1", "status": "completed",
        }

    def test_usage_update(self) -> None:
        event = SseEvent(
            id=5, event="usage_update",
            data={
                "input_tokens": 100, "output_tokens": 50, "model": "gpt-4",
                "context_size": 200000, "context_used": 110000,
            },
        )
        update = _event_to_acp_update(event)
        assert update is not None
        assert isinstance(update, UsageUpdate)
        assert update.size == 200000
        assert update.used == 110000

    def test_session_state_changed_returns_none(self) -> None:
        event = SseEvent(
            id=6, event="session_state_changed",
            data={"status": "idle"},
        )
        assert _event_to_acp_update(event) is None

    def test_empty_text_returns_none(self) -> None:
        event = SseEvent(id=7, event="agent_message", data={"text": ""})
        assert _event_to_acp_update(event) is None


# ---------------------------------------------------------------------------
# BridgeAgent initialization and protocol
# ---------------------------------------------------------------------------


class TestBridgeAgentInitialize:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self, bridge_agent: BridgeAgent) -> None:
        resp = await bridge_agent.initialize(
            protocol_version=1,
            client_info=Implementation(name="test-client", version="1.0"),
        )
        assert resp.protocol_version > 0
        assert resp.agent_info is not None
        assert resp.agent_info.name == "agent-bridge"
        assert resp.agent_capabilities is not None
        assert resp.agent_capabilities.load_session is True
        assert resp.agent_capabilities.session_capabilities is not None

    @pytest.mark.asyncio
    async def test_on_connect_stores_connection(
        self, bridge_agent: BridgeAgent, mock_conn: AsyncMock,
    ) -> None:
        bridge_agent.on_connect(mock_conn)
        assert bridge_agent._conn is mock_conn


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


class TestBridgeAgentSessions:
    @pytest.mark.asyncio
    async def test_new_session(
        self, bridge_agent: BridgeAgent, mock_conn: AsyncMock, tmp_path: Path,
    ) -> None:
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

                # Session Hosts are always on (dotfiles#1478): a local start now
                # connects via _connect_via_session_host, which can't stand up in
                # a unit test. Stub it to return the mock client + acp id.
                async def _fake_host_connect(
                    self: SessionManager, target: SpawnTarget, **kwargs: object,
                ) -> tuple[AsyncMock, str]:
                    return client_inst, "acp-123"

                with patch.object(
                    SessionManager, "_connect_via_session_host", _fake_host_connect
                ):
                    # An existing directory: the cwd is resolved strictly.
                    resp = await bridge_agent.new_session(cwd=str(tmp_path))

                assert resp.session_id is not None
                assert resp.field_meta == {"agent-bridge": {"role": "owner"}}
                assert resp.session_id in bridge_agent._owned_sessions

    @pytest.mark.asyncio
    async def test_new_session_auto_adopts_singleton_occupant(self, sm: SessionManager) -> None:
        target = SpawnTarget(type="local", cwd="/tmp", worktree_id="wt-1")
        session = Session("s-adopt", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        sm._sessions["s-adopt"] = session
        sm._db.create_session(
            "s-adopt", "test", None, "/tmp", "local", "idle", time.time()
        )

        class _Resolver:
            async def resolve_async(self, _agent_name: str) -> SpawnTarget:
                return SpawnTarget(
                    type="local",
                    cwd="/tmp",
                    worktree_id="wt-1",
                    venue={"provider": "agent-dispatch", "target_id": "task-1"},
                )

        agent = BridgeAgent(sm, resolver=cast(AgentResolver, _Resolver()), default_agent="dispatch:task-1")

        resp = await agent.new_session(cwd="/tmp")

        assert resp.session_id == "s-adopt"
        assert resp.field_meta == {"agent-bridge": {"role": "guest"}}
        assert "s-adopt" in agent._adopted_sessions
        assert "s-adopt" not in agent._owned_sessions

    @pytest.mark.asyncio
    async def test_new_session_treats_starting_singleton_as_occupied(
        self, sm: SessionManager,
    ) -> None:
        target = SpawnTarget(type="local", cwd="/tmp", worktree_id="wt-1")
        session = Session("s-starting", "test", target)
        session.status = SessionStatus.STARTING
        session.event_log = EventLog()
        sm._sessions["s-starting"] = session

        class _Resolver:
            async def resolve_async(self, _agent_name: str) -> SpawnTarget:
                return SpawnTarget(
                    type="local",
                    cwd="/tmp",
                    worktree_id="wt-1",
                    venue={"provider": "agent-dispatch", "target_id": "task-1"},
                )

        agent = BridgeAgent(sm, resolver=cast(AgentResolver, _Resolver()), default_agent="dispatch:task-1")

        resp = await agent.new_session(cwd="/tmp")

        assert resp.session_id == "s-starting"
        assert resp.field_meta == {"agent-bridge": {"role": "guest"}}
        assert "s-starting" in agent._adopted_sessions
        assert "s-starting" not in agent._owned_sessions

    @pytest.mark.asyncio
    async def test_new_session_serializes_dispatch_singleton_ownership(
        self, sm: SessionManager, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        created: list[str] = []
        real_start = sm.start_session

        async def _fake_start_session(
            target: SpawnTarget, agent_name: str | None = None,
            permission_callback: object = None,
        ) -> Session:
            session = Session("s-owner", "owner", target, agent_name)
            session.status = SessionStatus.STARTING
            session.event_log = EventLog()
            sm._sessions[session.session_id] = session
            created.append(session.session_id)
            started.set()
            await release.wait()
            session.status = SessionStatus.IDLE
            return session

        monkeypatch.setattr(sm, "start_session", _fake_start_session)

        class _Resolver:
            async def resolve_async(self, _agent_name: str) -> SpawnTarget:
                return SpawnTarget(
                    type="local",
                    cwd="/tmp",
                    worktree_id="wt-1",
                    venue={"provider": "agent-dispatch", "target_id": "task-1"},
                )

        agent_a = BridgeAgent(sm, resolver=cast(AgentResolver, _Resolver()), default_agent="dispatch:task-1")
        agent_b = BridgeAgent(sm, resolver=cast(AgentResolver, _Resolver()), default_agent="dispatch:task-1")
        try:
            first = asyncio.create_task(agent_a.new_session(cwd="/tmp"))
            await started.wait()
            second = asyncio.create_task(agent_b.new_session(cwd="/tmp"))
            await asyncio.sleep(0)
            assert not second.done()

            release.set()
            first_resp, second_resp = await asyncio.gather(first, second)

            assert first_resp.session_id == "s-owner"
            assert first_resp.field_meta == {"agent-bridge": {"role": "owner"}}
            assert second_resp.session_id == "s-owner"
            assert second_resp.field_meta == {"agent-bridge": {"role": "guest"}}
            assert created == ["s-owner"]
            assert "s-owner" in agent_a._owned_sessions
            assert "s-owner" in agent_b._adopted_sessions
        finally:
            monkeypatch.setattr(sm, "start_session", real_start)

    @pytest.mark.asyncio
    async def test_singleton_lock_registry_prunes_idle_slot(
        self, sm: SessionManager, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _singleton_slot_locks.clear()

        class _Resolver:
            async def resolve_async(self, _agent_name: str) -> SpawnTarget:
                return SpawnTarget(
                    type="local",
                    cwd="/tmp",
                    worktree_id="wt-prune",
                    venue={"provider": "agent-dispatch", "target_id": "task-1"},
                )

        real_start = sm.start_session

        async def _fake_start_session(
            target: SpawnTarget, agent_name: str | None = None,
            permission_callback: object = None,
        ) -> Session:
            session = Session("s-owner", "owner", target, agent_name)
            session.status = SessionStatus.IDLE
            session.event_log = EventLog()
            sm._sessions[session.session_id] = session
            return session

        monkeypatch.setattr(sm, "start_session", _fake_start_session)
        try:
            agent = BridgeAgent(sm, resolver=cast(AgentResolver, _Resolver()), default_agent="dispatch:task-1")
            await agent.new_session(cwd="/tmp")
        finally:
            monkeypatch.setattr(sm, "start_session", real_start)

        assert _singleton_slot_locks == {}

    @pytest.mark.asyncio
    async def test_close_session(self, bridge_agent: BridgeAgent, sm: SessionManager) -> None:
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
    async def test_list_sessions_only_owned(self, bridge_agent: BridgeAgent, sm: SessionManager) -> None:
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
    @pytest.mark.parametrize("legacy_order", [False, True])
    async def test_prompt_forwards_events(
        self, bridge_agent: BridgeAgent, sm: SessionManager, mock_conn: AsyncMock,
        legacy_order: bool,
    ) -> None:
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

        async def _fake_prompt(text: str) -> dict[str, Any]:
            # Simulate events appearing in EventLog during prompt
            assert text == "Hi"
            assert session.event_log is not None
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

        prompt_blocks: list[_PromptBlock] = [TextContentBlock(type="text", text="Hi")]
        if legacy_order:
            resp = await bridge_agent.prompt(prompt_blocks, "s1")
        else:
            resp = await bridge_agent.prompt("s1", prompt_blocks)

        assert resp.stop_reason == "end_turn"
        # session_update should have been called for agent_message
        assert mock_conn.session_update.call_count >= 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("both_are_blocks", [False, True])
    async def test_prompt_rejects_invalid_argument_pair(
        self, bridge_agent: BridgeAgent, both_are_blocks: bool,
    ) -> None:
        blocks: list[_PromptBlock] = [TextContentBlock(type="text", text="Hi")]
        with pytest.raises(RequestError) as exc_info:
            if both_are_blocks:
                await bridge_agent.prompt(blocks, blocks)
            else:
                await bridge_agent.prompt("s1", "Hi")
        assert exc_info.value.to_error_obj() == {
            "code": -32602,
            "message": "Invalid params",
            "data": {"reason": "Prompt requires a session ID and content blocks"},
        }

    @pytest.mark.asyncio
    async def test_prompt_error_returns_end_turn(
        self, bridge_agent: BridgeAgent, sm: SessionManager, mock_conn: AsyncMock,
    ) -> None:
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

        async def _failing_prompt(text: str) -> None:
            assert session.event_log is not None
            session.event_log.append("error", {"message": "crash"})
            raise RuntimeError("crash")

        mock_client.send_prompt = _failing_prompt
        session.client = mock_client

        prompt_blocks: list[_PromptBlock] = [TextContentBlock(type="text", text="Hi")]
        resp = await bridge_agent.prompt("s1", prompt_blocks)

        assert resp.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Unsupported methods
# ---------------------------------------------------------------------------


class TestBridgeAgentUnsupported:
    @pytest.mark.asyncio
    async def test_fork_session_raises(self, bridge_agent: BridgeAgent) -> None:
        with pytest.raises(Exception):
            await bridge_agent.fork_session(cwd="/tmp", session_id="s1")

    @pytest.mark.asyncio
    async def test_set_session_model_raises(self, bridge_agent: BridgeAgent) -> None:
        with pytest.raises(Exception):
            await bridge_agent.set_session_model(model_id="gpt-4", session_id="s1")

    @pytest.mark.asyncio
    async def test_ext_method_raises(self, bridge_agent: BridgeAgent) -> None:
        with pytest.raises(Exception):
            await bridge_agent.ext_method("custom/method", {})


# ---------------------------------------------------------------------------
# Cold-recreate on resume (dotfiles#1610)
# ---------------------------------------------------------------------------


class TestBridgeAgentColdRecreate:
    """The upstream resume/load/adopt paths must opt into the end+create last
    resort (``allow_recreate=True``) so a stale/unloadable but non-deleted
    session cold-recreates a fresh ACP session under the same bridge id instead
    of surfacing a raw 500 (operator principle: a non-deleted session must
    always cold-resume)."""

    def _stopped_session(self, sm: SessionManager) -> Session:
        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.STOPPED
        session.event_log = EventLog()
        sm._sessions["s1"] = session
        sm._db.create_session(
            "s1", "test", None, "/tmp", "local", "stopped", time.time()
        )
        return session

    @pytest.mark.asyncio
    async def test_load_session_allows_recreate(
        self, bridge_agent: BridgeAgent, sm: SessionManager, mock_conn: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge_agent.on_connect(mock_conn)
        self._stopped_session(sm)
        resume_session = AsyncMock()
        monkeypatch.setattr(sm, "resume_session", resume_session)

        await bridge_agent.load_session(cwd="/tmp", session_id="s1")

        resume_session.assert_awaited_once()
        assert resume_session.await_args is not None
        assert resume_session.await_args.kwargs["allow_recreate"] is True

    @pytest.mark.asyncio
    async def test_resume_session_allows_recreate(
        self, bridge_agent: BridgeAgent, sm: SessionManager, mock_conn: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge_agent.on_connect(mock_conn)
        self._stopped_session(sm)
        resume_session = AsyncMock()
        monkeypatch.setattr(sm, "resume_session", resume_session)

        await bridge_agent.resume_session(cwd="/tmp", session_id="s1")

        resume_session.assert_awaited_once()
        assert resume_session.await_args is not None
        assert resume_session.await_args.kwargs["allow_recreate"] is True

    @pytest.mark.asyncio
    async def test_adopt_existing_allows_recreate(
        self, bridge_agent: BridgeAgent, sm: SessionManager, mock_conn: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        bridge_agent.on_connect(mock_conn)
        self._stopped_session(sm)
        resume_session = AsyncMock()
        monkeypatch.setattr(sm, "resume_session", resume_session)

        await bridge_agent._adopt_existing("s1")

        resume_session.assert_awaited_once()
        assert resume_session.await_args is not None
        assert resume_session.await_args.kwargs["allow_recreate"] is True


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestBridgeAgentCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stops_owned_sessions(
        self, bridge_agent: BridgeAgent, sm: SessionManager,
    ) -> None:
        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        client = AsyncMock()
        client.is_running = True
        client.cancel_prompt = AsyncMock()
        client.shutdown = AsyncMock()
        client.has_active_background_tasks = False
        client.active_background_tasks = []
        session.client = client
        sm._sessions["s1"] = session
        sm._db.create_session("s1", "test", None, "/tmp", "local", "idle", time.time())

        bridge_agent._owned_sessions.add("s1")
        await bridge_agent.cleanup()

        assert len(bridge_agent._owned_sessions) == 0
        assert session.status == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_cleanup_preserves_sessions_with_background_tasks(
        self, bridge_agent: BridgeAgent, sm: SessionManager,
    ) -> None:
        """An owned session hosting active background tasks is left running on
        disconnect rather than torn down."""
        target = SpawnTarget(type="local", cwd="/tmp")
        session = Session("s1", "test", target)
        session.status = SessionStatus.IDLE
        session.event_log = EventLog()
        client = AsyncMock()
        client.is_running = True
        client.cancel_prompt = AsyncMock()
        client.shutdown = AsyncMock()
        client.has_active_background_tasks = True
        client.active_background_tasks = ["pr-daemon"]
        session.client = client
        sm._sessions["s1"] = session
        sm._db.create_session("s1", "test", None, "/tmp", "local", "idle", time.time())

        bridge_agent._owned_sessions.add("s1")
        await bridge_agent.cleanup()

        # Session survives -- background work keeps running.
        assert session.status == SessionStatus.IDLE
        assert session.client is not None
        client.shutdown.assert_not_awaited()


# ---------------------------------------------------------------------------
# Permission callback
# ---------------------------------------------------------------------------


class TestPermissionCallback:
    @pytest.mark.asyncio
    async def test_make_permission_callback_forwards_upstream(
        self, bridge_agent: BridgeAgent, mock_conn: AsyncMock,
    ) -> None:
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
    async def test_make_permission_callback_auto_approves_without_conn(
        self, bridge_agent: BridgeAgent,
    ) -> None:
        # No connection set
        cb = bridge_agent._make_permission_callback()
        resp = await cb("session-1", [], MagicMock())
        assert isinstance(resp.outcome, AllowedOutcome)
        assert resp.outcome.option_id == "allow_always"
