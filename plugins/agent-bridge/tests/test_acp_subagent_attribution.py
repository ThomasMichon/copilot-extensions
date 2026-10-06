"""Tests for sub-agent attribution from Copilot's raw session-event feed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.exceptions import RequestError
from acp.router import MessageRouter
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from agent_bridge import acp_client as acp_mod
from agent_bridge import acp_subagents as subagents_mod
from agent_bridge.acp_client import AcpClient

SESSION = "sess-1"
EXPECTED_META = {
    "github.com/copilot": {
        "events": [
            "subagent.started",
            "subagent.completed",
            "subagent.failed",
            "assistant.message",
            "assistant.reasoning",
            "tool.execution_start",
        ]
    }
}


@pytest.fixture(autouse=True)
def _feed_enabled(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_SUBAGENT_EVENTS", raising=False)


def _client() -> tuple[AcpClient, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []
    client = AcpClient(on_event=lambda t, d: events.append((t, d)))
    client._acp_session_id = SESSION
    return client, events


def _raw(client: AcpClient, type_: str, data: dict | None = None, *,
         agent_id: str | None = None, session_id: str = SESSION) -> None:
    params: dict[str, Any] = {
        "sessionId": session_id,
        "type": type_,
        "timestamp": "2026-01-01T00:00:00Z",
        "data": data or {},
    }
    if agent_id is not None:
        params["agentId"] = agent_id
    client._subagent_attr.handle_raw_event(params)


def _chunk(client: AcpClient, text: str) -> None:
    client._handle_session_update(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text=text),
        )
    )


def _thought(client: AcpClient, text: str) -> None:
    client._handle_session_update(
        AgentThoughtChunk(
            session_update="agent_thought_chunk",
            content=TextContentBlock(type="text", text=text),
        )
    )


def _tool_start(client: AcpClient, tool_call_id: str, meta: dict | None = None) -> None:
    kwargs: dict[str, Any] = {}
    if meta is not None:
        kwargs["field_meta"] = meta
    client._handle_session_update(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id=tool_call_id,
            title="Read file",
            kind="read",
            **kwargs,
        )
    )


def _tool_done(client: AcpClient, tool_call_id: str, meta: dict | None = None) -> None:
    kwargs: dict[str, Any] = {}
    if meta is not None:
        kwargs["field_meta"] = meta
    client._handle_session_update(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=tool_call_id,
            status="completed",
            **kwargs,
        )
    )


def _started(client: AcpClient, agent_id: str = "agent-a", launch: str = "task-1") -> None:
    _raw(
        client,
        "subagent.started",
        {
            "toolCallId": launch,
            "agentName": "explore",
            "agentDisplayName": "Explore Agent",
            "agentDescription": "Read-only exploration",
            "model": "some-model",
        },
        agent_id=agent_id,
    )


def _of(events: list[tuple[str, dict]], kind: str) -> list[dict]:
    return [d for t, d in events if t == kind]


# -- Feed request ---------------------------------------------------------------


def _session_connection() -> MagicMock:
    conn = MagicMock()
    conn.new_session = AsyncMock(return_value=SimpleNamespace(session_id="sess-new"))
    conn.load_session = AsyncMock(return_value=SimpleNamespace())
    conn.set_config_option = AsyncMock()
    return conn


@pytest.fixture
def _no_model_config(monkeypatch):
    monkeypatch.setattr(acp_mod, "resolve_acp_model_config", lambda: {})


def test_new_session_requests_raw_event_feed(_no_model_config) -> None:
    client, _ = _client()
    client._connection = _session_connection()
    asyncio.run(client.new_session(cwd="/repo"))
    kwargs = client._connection.new_session.await_args.kwargs
    assert kwargs["cwd"] == "/repo"
    assert kwargs["github.com/copilot"] == EXPECTED_META["github.com/copilot"]


def test_load_session_requests_raw_event_feed(_no_model_config) -> None:
    client, _ = _client()
    client._connection = _session_connection()
    asyncio.run(client.load_session(cwd="/repo", session_id="sess-old"))
    kwargs = client._connection.load_session.await_args.kwargs
    assert kwargs["session_id"] == "sess-old"
    assert kwargs["github.com/copilot"] == EXPECTED_META["github.com/copilot"]


@pytest.mark.parametrize("value", ["0", "false", "off", "No"])
def test_feed_request_can_be_disabled(_no_model_config, monkeypatch, value) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_SUBAGENT_EVENTS", value)
    client, _ = _client()
    client._connection = _session_connection()
    asyncio.run(client.new_session(cwd="/repo"))
    assert "github.com/copilot" not in client._connection.new_session.await_args.kwargs


def test_meta_serializes_as_underscore_meta() -> None:
    from acp.schema import NewSessionRequest

    request = NewSessionRequest(cwd="/repo", mcp_servers=[], field_meta=EXPECTED_META)
    dumped = request.model_dump(by_alias=True, exclude_none=True)
    assert dumped["_meta"] == EXPECTED_META


def test_feed_request_fails_open_on_invalid_params(_no_model_config) -> None:
    client, _ = _client()
    conn = _session_connection()
    calls: list[dict] = []

    async def _new_session(**kwargs):
        calls.append(kwargs)
        if "github.com/copilot" in kwargs:
            raise RequestError(-32602, "Invalid params")
        return SimpleNamespace(session_id="sess-new")

    conn.new_session = _new_session
    client._connection = conn
    assert asyncio.run(client.new_session(cwd="/repo")) == "sess-new"
    assert len(calls) == 2
    assert "github.com/copilot" not in calls[1]


def test_feed_request_does_not_mask_other_errors(_no_model_config) -> None:
    client, _ = _client()
    conn = _session_connection()
    conn.new_session = AsyncMock(side_effect=RequestError(-32603, "boom"))
    client._connection = conn
    with pytest.raises(RequestError):
        asyncio.run(client.new_session(cwd="/repo"))
    assert conn.new_session.await_count == 1


def test_session_event_route_dispatches_notification() -> None:
    client, events = _client()
    router = MessageRouter()
    connection = SimpleNamespace(_conn=SimpleNamespace(_handler=router))

    with pytest.raises(RequestError):
        asyncio.run(router("github.com/copilot/sessionEvent", {}, True))

    client._subagent_attr.install_route(connection)
    asyncio.run(
        router(
            "github.com/copilot/sessionEvent",
            {
                "sessionId": SESSION,
                "type": "subagent.started",
                "agentId": "agent-a",
                "data": {"toolCallId": "task-1", "agentName": "explore"},
            },
            True,
        )
    )
    assert _of(events, "subagent_started")[0]["agent_id"] == "agent-a"


def test_route_install_is_best_effort() -> None:
    client, _ = _client()
    client._subagent_attr.install_route(object())  # no router: must not raise


# -- Lifecycle ------------------------------------------------------------------


def test_subagent_lifecycle_events() -> None:
    client, events = _client()
    _started(client)
    _raw(
        client,
        "subagent.completed",
        {
            "toolCallId": "task-1",
            "agentName": "explore",
            "agentDisplayName": "Explore Agent",
            "model": "some-model",
            "durationMs": 1200,
            "totalToolCalls": 3,
            "totalTokens": 4567,
        },
        agent_id="agent-a",
    )
    assert _of(events, "subagent_started") == [{
        "agent_id": "agent-a",
        "parent_tool_call_id": "task-1",
        "agent_name": "explore",
        "agent_display_name": "Explore Agent",
        "agent_description": "Read-only exploration",
        "model": "some-model",
    }]
    completed = _of(events, "subagent_completed")[0]
    assert completed["agent_id"] == "agent-a"
    assert completed["parent_tool_call_id"] == "task-1"
    assert completed["duration_ms"] == 1200
    assert completed["total_tool_calls"] == 3
    assert completed["total_tokens"] == 4567


def test_subagent_failed_falls_back_to_known_parent() -> None:
    client, events = _client()
    _started(client, "agent-b", "task-9")
    _raw(client, "subagent.failed", {"error": "boom"}, agent_id="agent-b")
    failed = _of(events, "subagent_failed")[0]
    assert failed["agent_id"] == "agent-b"
    assert failed["parent_tool_call_id"] == "task-9"
    assert failed["error"] == "boom"


def test_lifecycle_without_agent_id_is_ignored() -> None:
    client, events = _client()
    _raw(client, "subagent.started", {"toolCallId": "task-1"})
    assert events == []


# -- Tool-call attribution ------------------------------------------------------


def test_main_agent_tool_calls_keep_their_shape() -> None:
    client, events = _client()
    _tool_start(client, "tc-main")
    _tool_done(client, "tc-main")
    assert events[0] == ("tool_call_start", {
        "tool_call_id": "tc-main", "title": "Read file", "kind": "read", "raw_input": None,
    })
    assert "agent_id" not in events[1][1]
    assert "parent_tool_call_id" not in events[1][1]


def test_tool_call_tagged_from_update_meta() -> None:
    client, events = _client()
    _started(client)
    meta = {"github.com/copilot": {"agentId": "agent-a"}}
    _tool_start(client, "tc-sub", meta)
    _tool_done(client, "tc-sub", meta)
    start = _of(events, "tool_call_start")[0]
    update = _of(events, "tool_call_update")[0]
    for event in (start, update):
        assert event["agent_id"] == "agent-a"
        assert event["parent_tool_call_id"] == "task-1"


def test_tool_call_tagged_from_raw_execution_start() -> None:
    client, events = _client()
    _started(client)
    _raw(
        client,
        "tool.execution_start",
        {"toolCallId": "tc-sub", "parentToolCallId": "task-1", "toolName": "view"},
        agent_id="agent-a",
    )
    _tool_start(client, "tc-sub")  # no _meta on the update itself
    _tool_done(client, "tc-sub")
    assert _of(events, "tool_call_start")[0]["agent_id"] == "agent-a"
    assert _of(events, "tool_call_update")[0]["parent_tool_call_id"] == "task-1"
    assert "tc-sub" not in client._subagent_attr.tool_call_agents  # dropped once terminal


def test_main_agent_raw_tool_start_does_not_tag() -> None:
    client, events = _client()
    _raw(client, "tool.execution_start", {"toolCallId": "tc-main"})
    _tool_start(client, "tc-main")
    assert "agent_id" not in _of(events, "tool_call_start")[0]


# -- Text attribution -----------------------------------------------------------


def test_subagent_message_run_is_attributed_without_duplicating_text() -> None:
    client, events = _client()
    _started(client)
    _chunk(client, "Hello ")
    _chunk(client, "world")
    _raw(
        client,
        "assistant.message",
        {"content": "Hello world", "parentToolCallId": "task-1"},
        agent_id="agent-a",
    )
    assert [d["text"] for d in _of(events, "agent_message")] == ["Hello ", "world"]
    assert _of(events, "subagent_message") == [{
        "agent_id": "agent-a", "parent_tool_call_id": "task-1", "chunk_count": 2,
    }]
    assert not any("Hello world" in str(d) for t, d in events if t != "agent_message")


def test_main_agent_message_is_never_attributed() -> None:
    client, events = _client()
    _chunk(client, "main says hi")
    _raw(client, "assistant.message", {"content": "main says hi"})
    assert _of(events, "subagent_message") == []
    # The next sub-agent run starts fresh after the main-agent boundary.
    _chunk(client, "sub")
    _raw(client, "assistant.message", {"content": "sub"}, agent_id="agent-a")
    assert _of(events, "subagent_message")[0]["chunk_count"] == 1


def test_interleaved_run_is_not_attributed() -> None:
    client, events = _client()
    _chunk(client, "main text ")
    _chunk(client, "sub text")
    _raw(client, "assistant.message", {"content": "sub text"}, agent_id="agent-a")
    assert _of(events, "subagent_message") == []


def test_dropped_boundary_prevents_attribution() -> None:
    client, events = _client()
    _chunk(client, "first")
    # The boundary for "first" was dropped under backpressure.
    _chunk(client, "second")
    _raw(client, "assistant.message", {"content": "second"}, agent_id="agent-a")
    assert _of(events, "subagent_message") == []


def test_overflowed_run_is_not_attributed(monkeypatch) -> None:
    monkeypatch.setattr(subagents_mod, "MAX_TEXT_RUN_CHUNKS", 2)
    client, events = _client()
    for part in ("a", "b", "c"):
        _chunk(client, part)
    _raw(client, "assistant.message", {"content": "abc"}, agent_id="agent-a")
    assert _of(events, "subagent_message") == []
    _chunk(client, "d")
    _raw(client, "assistant.message", {"content": "d"}, agent_id="agent-a")
    assert _of(events, "subagent_message")[0]["chunk_count"] == 1


def test_subagent_thought_run_is_attributed() -> None:
    client, events = _client()
    _started(client)
    _thought(client, "think")
    _thought(client, "ing")
    _chunk(client, "answer")
    _raw(client, "assistant.reasoning", {"content": "thinking"}, agent_id="agent-a")
    assert _of(events, "subagent_thought") == [{
        "agent_id": "agent-a", "parent_tool_call_id": "task-1", "chunk_count": 2,
    }]
    _raw(client, "assistant.message", {"content": "answer"}, agent_id="agent-a")
    assert _of(events, "subagent_message")[0]["chunk_count"] == 1


# -- Robustness -----------------------------------------------------------------


def test_other_session_events_are_ignored() -> None:
    client, events = _client()
    _raw(client, "subagent.started", {"toolCallId": "t"}, agent_id="a", session_id="other")
    assert events == []


@pytest.mark.parametrize(
    "params",
    [
        None,
        "nope",
        [],
        {"type": "subagent.started", "agentId": "a", "data": "x"},
        # A lifecycle notification without a usable sessionId is never
        # emitted into the current session.
        {"type": "subagent.started", "agentId": "a", "data": {"toolCallId": "t"}},
        {"sessionId": "", "type": "subagent.started", "agentId": "a",
         "data": {"toolCallId": "t"}},
        {"sessionId": None, "type": "subagent.started", "agentId": "a",
         "data": {"toolCallId": "t"}},
        {"sessionId": 7, "type": "subagent.started", "agentId": "a",
         "data": {"toolCallId": "t"}},
    ],
)
def test_malformed_params_never_raise_or_emit(params) -> None:
    client, events = _client()
    client._subagent_attr.handle_raw_event(params)
    assert events == []


def test_raw_events_before_session_id_is_known_are_ignored() -> None:
    client, events = _client()
    client._acp_session_id = None
    _raw(client, "subagent.started", {"toolCallId": "t"}, agent_id="a")
    assert events == []


def test_replay_suppression_ignores_raw_events() -> None:
    client, events = _client()
    client._loading_session = True
    client._suppress_replay = True
    _started(client)
    assert events == []


def test_no_raw_events_leaves_stream_unchanged() -> None:
    client, events = _client()
    _chunk(client, "hello")
    _tool_start(client, "tc-1")
    _tool_done(client, "tc-1")
    assert [t for t, _ in events] == ["agent_message", "tool_call_start", "tool_call_update"]
    assert not any("agent_id" in d for _, d in events)


def test_attribution_state_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(subagents_mod, "MAX_ATTRIBUTION_ENTRIES", 3)
    client, _ = _client()
    for i in range(5):
        _started(client, f"agent-{i}", f"task-{i}")
    assert list(client._subagent_attr.subagents) == ["agent-2", "agent-3", "agent-4"]


def test_shutdown_resets_attribution_state() -> None:
    client, _ = _client()
    _started(client)
    _chunk(client, "x")
    asyncio.run(client.shutdown())
    assert client._subagent_attr.subagents == {}
    assert client._subagent_attr.runs["message"] == []
