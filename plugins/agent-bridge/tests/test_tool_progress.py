"""Tests for the server-side tool-progress liveness SSE framing."""

from __future__ import annotations

import json

from agent_bridge.routes.sessions import _tool_progress_sse


def _parse_sse(block: str) -> tuple[dict[str, str], dict]:
    """Parse one SSE event block into (fields, data-json)."""
    fields: dict[str, str] = {}
    data = None
    for line in block.splitlines():
        if not line or line.startswith(":"):
            continue
        key, _, value = line.partition(": ")
        if key == "data":
            data = json.loads(value)
        else:
            fields[key] = value
    return fields, data


class TestToolProgressSse:
    def test_framed_as_tool_progress_event(self) -> None:
        active = {
            "tool_call_id": "t1",
            "title": "Build odsp-legacy",
            "kind": "execute",
            "command": "rush build -t @ms/app-cores-odsp-legacy",
            "started_at": 1000.0,
            "started_id": 42,
        }
        block = _tool_progress_sse(active, now=1000.0 + 1027)
        fields, data = _parse_sse(block)
        assert fields["event"] == "tool_progress"
        assert data["event"] == "tool_progress"
        assert data["data"]["title"] == "Build odsp-legacy"
        assert data["data"]["command"] == "rush build -t @ms/app-cores-odsp-legacy"
        assert round(data["data"]["elapsed_s"]) == 1027

    def test_has_no_id_line_so_cursor_is_untouched(self) -> None:
        active = {"tool_call_id": "t1", "title": "X", "started_at": 5.0}
        block = _tool_progress_sse(active, now=10.0)
        # No ``id:`` field -> the client never acks it -> cursor never moves.
        assert "id:" not in block
        fields, _ = _parse_sse(block)
        assert "id" not in fields

    def test_started_at_not_leaked_to_client(self) -> None:
        active = {"tool_call_id": "t1", "title": "X", "started_at": 5.0}
        _, data = _parse_sse(_tool_progress_sse(active, now=10.0))
        assert "started_at" not in data["data"]
        assert data["data"]["elapsed_s"] == 5.0

    def test_multiline_command_stays_single_sse_data_line(self) -> None:
        active = {"tool_call_id": "t1", "title": "X",
                  "command": "line one\nline two", "started_at": 0.0}
        block = _tool_progress_sse(active, now=1.0)
        # JSON-escapes the newline -> exactly one ``data:`` line (SSE-safe).
        data_lines = [ln for ln in block.splitlines() if ln.startswith("data: ")]
        assert len(data_lines) == 1
        _, data = _parse_sse(block)
        assert data["data"]["command"] == "line one\nline two"

    def test_elapsed_never_negative(self) -> None:
        active = {"tool_call_id": "t1", "title": "X", "started_at": 100.0}
        _, data = _parse_sse(_tool_progress_sse(active, now=90.0))
        assert data["data"]["elapsed_s"] == 0.0
