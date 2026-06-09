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
