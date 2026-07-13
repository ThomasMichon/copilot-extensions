"""Tests for AcpClient session-update -> event emission fidelity."""

from __future__ import annotations

import pytest
from acp.schema import ContentToolCallContent, TextContentBlock, ToolCallProgress, ToolCallStart

from agent_bridge.acp_client import AcpClient


def _client_with_recorder() -> tuple[AcpClient, list[tuple[str, dict]]]:
    events: list[tuple[str, dict]] = []
    client = AcpClient(on_event=lambda t, d: events.append((t, d)))
    return client, events


def test_tool_call_start_emits_raw_input() -> None:
    client, events = _client_with_recorder()
    client._handle_session_update(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="tc1",
            title="Read file",
            kind="read",
            raw_input={"path": "/etc/hosts"},
        )
    )
    assert events == [
        (
            "tool_call_start",
            {
                "tool_call_id": "tc1",
                "title": "Read file",
                "kind": "read",
                "raw_input": {"path": "/etc/hosts"},
            },
        )
    ]


def test_tool_call_update_emits_results() -> None:
    client, events = _client_with_recorder()
    client._handle_session_update(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="tc2",
            title="Run",
            kind="execute",
        )
    )
    client._handle_session_update(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="tc2",
            status="completed",
            raw_output={"exit_code": 0, "stdout": "hello"},
        )
    )

    update = next(d for t, d in events if t == "tool_call_update")
    assert update["status"] == "completed"
    assert update["raw_output"] == {"exit_code": 0, "stdout": "hello"}
    # content list is always present (accumulated tool-result text)
    assert update["content"] == []


def _content_block(text: str) -> ContentToolCallContent:
    return ContentToolCallContent(
        type="content", content=TextContentBlock(type="text", text=text)
    )


def test_tool_call_update_content_only_on_terminal() -> None:
    """In-progress updates must NOT carry the accumulated content/raw_output.

    Emitting the growing accumulation on every progress chunk is O(n^2) in
    storage/CPU/SSE and backpressures the ingestion loop (dotfiles #99). Only the
    terminal update carries the full accumulated result -- the only point any
    consumer reads it (render._render_tool_update).
    """
    client, events = _client_with_recorder()
    client._handle_session_update(
        ToolCallStart(
            session_update="tool_call", tool_call_id="tc3", title="Run", kind="execute"
        )
    )
    # Two in-progress chunks accumulate internally but must emit empty content.
    for chunk in ("line1\n", "line2\n"):
        client._handle_session_update(
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id="tc3",
                status="in_progress",
                content=[_content_block(chunk)],
                raw_output={"partial": True},
            )
        )
    in_progress = [d for t, d in events if t == "tool_call_update"]
    assert in_progress, "expected in-progress updates"
    assert all(d["content"] == [] for d in in_progress)
    assert all(d["raw_output"] is None for d in in_progress)

    # The terminal update carries the full accumulation.
    client._handle_session_update(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="tc3",
            status="completed",
            raw_output={"exit_code": 0},
        )
    )
    final = [d for t, d in events if t == "tool_call_update"][-1]
    assert final["status"] == "completed"
    assert final["content"] == ["line1\n", "line2\n"]
    assert final["raw_output"] == {"exit_code": 0}


def test_load_session_replay_is_suppressed() -> None:
    """Replayed history during load_session must not be re-emitted (#706)."""
    from acp.schema import AgentMessageChunk, TextContentBlock

    client, events = _client_with_recorder()
    client._loading_session = True
    client._handle_session_update(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="DONE"),
        )
    )
    assert events == []  # suppressed while loading

    client._loading_session = False
    client._handle_session_update(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="DONE"),
        )
    )
    assert events == [("agent_message", {"text": "DONE"})]


def test_user_message_emitted_only_during_replay() -> None:
    """User prompts are captured on resync replay, not during a live turn.

    During a live turn the client already records the user message, so the
    agent's echo must not be re-emitted (it would duplicate). During a load
    replay (resync) the agent is the only source of the user's turns, so
    capture them to preserve user messages in the rebuilt log.
    """
    from acp.schema import TextContentBlock, UserMessageChunk

    client, events = _client_with_recorder()

    # Live turn: not loading -> user message chunk is NOT emitted.
    client._handle_session_update(
        UserMessageChunk(
            session_update="user_message_chunk",
            content=TextContentBlock(type="text", text="hello"),
        )
    )
    assert events == []

    # Resync replay: loading with suppression cleared -> emitted as user_message.
    client._loading_session = True
    client._suppress_replay = False
    client._handle_session_update(
        UserMessageChunk(
            session_update="user_message_chunk",
            content=TextContentBlock(type="text", text="add a pride theme"),
        )
    )
    assert events == [("user_message", {"content": "add a pride theme"})]


def test_child_exit_without_prompt_is_not_an_error() -> None:
    """An idle/just-resumed child exiting must not emit an error (#706)."""
    client, events = _client_with_recorder()
    client._prompt_in_flight = False
    client._handle_child_exit()
    assert events == []
    assert client._prompt_error is None


def test_child_exit_during_prompt_emits_error() -> None:
    """A child dying mid-turn is still surfaced as an error."""
    client, events = _client_with_recorder()
    client._prompt_in_flight = True
    client._handle_child_exit()
    assert any(t == "error" for t, _ in events)
    assert client._prompt_error is not None


def test_transport_lost_wakes_in_flight_prompt() -> None:
    """A host-mode transport drop mid-turn must fail the in-flight prompt
    instead of hanging forever on a reply that will never arrive (issue #22).

    Without the wake, ``send_prompt`` awaits ``connection.prompt()`` on a dead
    reader indefinitely and the session wedges in 'running' with no terminal
    event."""
    import asyncio
    from unittest.mock import MagicMock

    async def scenario() -> None:
        client, events = _client_with_recorder()
        client._host_mode = True
        client._acp_session_id = "acp-1"

        conn = MagicMock()

        async def _hang(*_args, **_kwargs):
            await asyncio.Event().wait()  # never resolves

        conn.prompt = _hang
        client._connection = conn

        task = asyncio.ensure_future(client.send_prompt("hi"))
        await asyncio.sleep(0.05)  # let the prompt start awaiting
        client.mark_transport_lost()

        with pytest.raises(ConnectionResetError):
            await asyncio.wait_for(task, timeout=1.0)

        assert any(t == "error" for t, _ in events)
        assert client._prompt_error is not None

    asyncio.run(scenario())


def test_transport_lost_does_not_disturb_a_completing_prompt() -> None:
    """When the prompt completes normally, the transport-lost race must not
    interfere -- a clean turn still emits turn_complete (issue #22)."""
    import asyncio
    from unittest.mock import MagicMock

    async def scenario() -> None:
        client, events = _client_with_recorder()
        client._host_mode = True
        client._acp_session_id = "acp-1"

        conn = MagicMock()

        async def _ok(*_args, **_kwargs):
            result = MagicMock()
            result.stop_reason = "end_turn"
            return result

        conn.prompt = _ok
        client._connection = conn

        await client.send_prompt("hi")
        assert any(t == "turn_complete" for t, _ in events)
        assert not any(t == "error" for t, _ in events)

    asyncio.run(scenario())


def _tool_call(client: AcpClient, tool_call_id: str, title: str = "Run task") -> None:
    client._handle_session_update(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id=tool_call_id,
            title=title,
            kind="execute",
        )
    )


def _terminal(client: AcpClient, tool_call_id: str, text: str) -> None:
    """Drive a tool call to a completed terminal update carrying ``text``."""
    client._handle_session_update(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=tool_call_id,
            status="completed",
            content=[_content_block(text)],
        )
    )


def test_background_task_launch_tracked() -> None:
    client, events = _client_with_recorder()
    assert client.has_active_background_tasks is False

    _tool_call(client, "tc-launch")
    _terminal(
        client,
        "tc-launch",
        "Agent started in background with agent_id: pr-daemon. "
        "You'll be notified when it finishes.",
    )

    assert client.has_active_background_tasks is True
    assert client.active_background_tasks == ["pr-daemon"]
    started = [d for t, d in events if t == "background_task_started"]
    assert started and started[-1]["agent_id"] == "pr-daemon"


def test_background_task_completion_clears() -> None:
    client, events = _client_with_recorder()
    _tool_call(client, "tc-launch")
    _terminal(
        client,
        "tc-launch",
        "Agent started in background with agent_id: pr-daemon.",
    )
    assert client.has_active_background_tasks is True

    _tool_call(client, "tc-read")
    _terminal(
        client,
        "tc-read",
        "Agent completed. agent_id: pr-daemon, name: pr-daemon, "
        "status: completed, duration: 10s",
    )

    assert client.has_active_background_tasks is False
    assert client.active_background_tasks == []
    finished = [d for t, d in events if t == "background_task_finished"]
    assert finished and finished[-1]["agent_id"] == "pr-daemon"
    assert finished[-1]["status"] == "completed"


def test_background_task_idle_clears() -> None:
    """An idle sub-agent is parked, not actively working -- it clears."""
    client, _ = _client_with_recorder()
    _tool_call(client, "tc1")
    _terminal(client, "tc1", "Agent started in background with agent_id: chatty.")
    assert client.has_active_background_tasks is True

    _tool_call(client, "tc2")
    _terminal(
        client,
        "tc2",
        "Agent is idle (waiting for messages). agent_id: chatty, status: idle",
    )
    assert client.has_active_background_tasks is False


def test_background_task_running_status_does_not_clear() -> None:
    """A non-terminal status sighting must NOT clear an active task."""
    client, _ = _client_with_recorder()
    _tool_call(client, "tc1")
    _terminal(client, "tc1", "Agent started in background with agent_id: worker.")

    _tool_call(client, "tc2")
    _terminal(
        client,
        "tc2",
        "Agent is still running. agent_id: worker, status: running",
    )
    assert client.has_active_background_tasks is True
    assert client.active_background_tasks == ["worker"]


def test_background_tasks_multiple_independent() -> None:
    client, _ = _client_with_recorder()
    _tool_call(client, "a")
    _terminal(client, "a", "Agent started in background with agent_id: one.")
    _tool_call(client, "b")
    _terminal(client, "b", "Agent started in background with agent_id: two.")
    assert client.active_background_tasks == ["one", "two"]

    _tool_call(client, "c")
    _terminal(client, "c", "Agent failed. agent_id: one, status: failed")
    assert client.active_background_tasks == ["two"]
    assert client.has_active_background_tasks is True


# --- per-session MCP injection (build_mcp_servers) ---------------------------

from acp.schema import (  # noqa: E402
    HttpMcpServer,
    McpServerStdio,
    SseMcpServer,
)

from agent_bridge.acp_client import build_mcp_servers  # noqa: E402


def test_build_mcp_servers_none_and_empty_yield_empty() -> None:
    assert build_mcp_servers(None) == []
    assert build_mcp_servers([]) == []


def test_build_mcp_servers_stdio_default_type() -> None:
    servers = build_mcp_servers(
        [
            {
                "name": "review-broker",
                "command": "/opt/id/.venv/bin/python",
                "args": ["-m", "broker.server"],
                "env": {"TOKEN": "abc", "PR": "42"},
            }
        ]
    )
    assert len(servers) == 1
    s = servers[0]
    assert isinstance(s, McpServerStdio)
    assert s.name == "review-broker"
    assert s.command == "/opt/id/.venv/bin/python"
    assert s.args == ["-m", "broker.server"]
    assert {e.name: e.value for e in s.env} == {"TOKEN": "abc", "PR": "42"}


def test_build_mcp_servers_http_and_sse() -> None:
    servers = build_mcp_servers(
        [
            {"type": "http", "name": "h", "url": "https://x/mcp",
             "headers": {"Authorization": "Bearer t"}},
            {"type": "sse", "name": "s", "url": "https://y/sse"},
        ]
    )
    assert isinstance(servers[0], HttpMcpServer)
    assert servers[0].url == "https://x/mcp"
    assert {h.name: h.value for h in servers[0].headers} == {"Authorization": "Bearer t"}
    assert isinstance(servers[1], SseMcpServer)
    assert servers[1].url == "https://y/sse"
    assert servers[1].headers == []


def test_build_mcp_servers_stdio_minimal_defaults() -> None:
    servers = build_mcp_servers([{"name": "m", "command": "/bin/echo"}])
    assert servers[0].args == []
    assert servers[0].env == []


def test_build_mcp_servers_rejects_bad_specs() -> None:
    with pytest.raises(ValueError):
        build_mcp_servers([{"command": "/bin/echo"}])  # missing name
    with pytest.raises(ValueError):
        build_mcp_servers([{"name": "m"}])  # stdio missing command
    with pytest.raises(ValueError):
        build_mcp_servers([{"type": "http", "name": "m"}])  # http missing url
    with pytest.raises(ValueError):
        build_mcp_servers([{"type": "bogus", "name": "m"}])  # unknown type


# -- ask_user elicitation ---------------------------------------------------


def _form_session_mode(tool_call_id: str, schema: dict):
    from acp.schema import ElicitationFormSessionMode, ElicitationSchema

    return ElicitationFormSessionMode(
        session_id="acp-1",
        tool_call_id=tool_call_id,
        requested_schema=ElicitationSchema.model_validate(schema),
    )


def test_ask_user_elicitation_emits_request_and_parks() -> None:
    """create_elicitation surfaces an ask_user_request event and blocks until
    a human answers -- it must never auto-answer."""
    import asyncio

    from acp.schema import AcceptElicitationResponse

    async def scenario() -> None:
        client, events = _client_with_recorder()
        client._acp_session_id = "acp-1"
        mode = _form_session_mode(
            "tc-ask",
            {"type": "object", "properties": {"choice": {"type": "string"}}},
        )

        task = asyncio.ensure_future(
            client._handle_elicitation("Pick one", mode)
        )
        await asyncio.sleep(0.05)  # let it emit + park

        # The question is surfaced, and nothing is resolved yet.
        assert ("ask_user_request", {
            "tool_call_id": "tc-ask",
            "message": "Pick one",
            "requested_schema": {
                "type": "object",
                "properties": {"choice": {"type": "string"}},
            },
        }) in events
        assert not task.done()
        assert client.has_pending_elicitation("tc-ask")

        # A human answers -> the parked future resolves with the content.
        assert client.resolve_elicitation("tc-ask", {"choice": "a"}) is True
        result = await asyncio.wait_for(task, timeout=1.0)
        assert isinstance(result, AcceptElicitationResponse)
        assert result.content == {"choice": "a"}
        assert not client.has_pending_elicitation("tc-ask")

    asyncio.run(scenario())


def test_resolve_elicitation_unknown_returns_false() -> None:
    client, _ = _client_with_recorder()
    assert client.resolve_elicitation("nope", {"x": 1}) is False


def test_ask_user_elicitation_decline_and_cancel() -> None:
    import asyncio

    from acp.schema import CancelElicitationResponse, DeclineElicitationResponse

    async def scenario() -> None:
        client, _ = _client_with_recorder()
        client._acp_session_id = "acp-1"
        mode = _form_session_mode(
            "tc-d", {"type": "object", "properties": {}}
        )
        task = asyncio.ensure_future(client._handle_elicitation("m", mode))
        await asyncio.sleep(0.05)
        assert client.resolve_elicitation("tc-d", None, action="decline") is True
        assert isinstance(await asyncio.wait_for(task, 1.0), DeclineElicitationResponse)

        mode2 = _form_session_mode(
            "tc-c", {"type": "object", "properties": {}}
        )
        task2 = asyncio.ensure_future(client._handle_elicitation("m", mode2))
        await asyncio.sleep(0.05)
        assert client.resolve_elicitation("tc-c", None, action="cancel") is True
        assert isinstance(await asyncio.wait_for(task2, 1.0), CancelElicitationResponse)

    asyncio.run(scenario())


def test_withdraw_elicitation_cancels_sole_pending() -> None:
    """An elicitation/complete withdrawal (agent no longer needs the answer)
    unwinds the parked request as cancelled."""
    import asyncio

    from acp.schema import CancelElicitationResponse

    async def scenario() -> None:
        client, _ = _client_with_recorder()
        client._acp_session_id = "acp-1"
        mode = _form_session_mode(
            "tc-w", {"type": "object", "properties": {}}
        )
        task = asyncio.ensure_future(client._handle_elicitation("m", mode))
        await asyncio.sleep(0.05)
        # id doesn't match the tool_call_id, but it's the sole pending one.
        client._withdraw_elicitation("some-elicitation-id")
        assert isinstance(await asyncio.wait_for(task, 1.0), CancelElicitationResponse)

    asyncio.run(scenario())


def test_shutdown_cancels_pending_elicitations() -> None:
    import asyncio

    from acp.schema import CancelElicitationResponse

    async def scenario() -> None:
        client, _ = _client_with_recorder()
        client._acp_session_id = "acp-1"
        mode = _form_session_mode(
            "tc-s", {"type": "object", "properties": {}}
        )
        task = asyncio.ensure_future(client._handle_elicitation("m", mode))
        await asyncio.sleep(0.05)
        await client.shutdown()
        assert isinstance(await asyncio.wait_for(task, 1.0), CancelElicitationResponse)

    asyncio.run(scenario())
