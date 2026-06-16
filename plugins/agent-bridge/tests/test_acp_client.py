"""Tests for AcpClient session-update -> event emission fidelity."""

from __future__ import annotations

from acp.schema import ToolCallProgress, ToolCallStart

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
