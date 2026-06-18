"""Tests for the CLI client's SSE line parsing in ``stream_events``.

Locks in the contract that liveness rides the comment channel:
``: tool_progress <json>`` surfaces as a ``tool_progress`` dict (cursor-neutral,
``id=""``), a bare comment surfaces as ``_heartbeat``, and real ``event:``/
``data:`` blocks still parse with their durable id.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from agent_bridge.client import BridgeClient


class _FakeSseResp:
    """Iterable byte-line response mimicking a urlopen SSE stream."""

    def __init__(self, lines: list[str]) -> None:
        # Each SSE line is delivered as its own bytes chunk, newline-terminated.
        self._lines = [(ln + "\n").encode() for ln in lines]

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


def _drain(lines: list[str]) -> list[dict]:
    client = BridgeClient("http://127.0.0.1:0", "tok")
    with patch(
        "agent_bridge.client.urllib.request.urlopen",
        return_value=_FakeSseResp(lines),
    ):
        return list(client.stream_events("sess-1"))


class TestSseCommentParsing:
    def test_tool_progress_comment_becomes_liveness_dict(self) -> None:
        payload = json.dumps(
            {"title": "Build webapp", "command": "rush build", "elapsed_s": 1027}
        )
        events = _drain([f": tool_progress {payload}", ""])
        assert events == [
            {
                "id": "",
                "event": "tool_progress",
                "data": {
                    "title": "Build webapp",
                    "command": "rush build",
                    "elapsed_s": 1027,
                },
            }
        ]
        # Cursor-neutral: no durable id to ack.
        assert events[0]["id"] == ""

    def test_bare_comment_is_heartbeat(self) -> None:
        events = _drain([": heartbeat", ""])
        assert events == [{"id": "", "event": "_heartbeat", "data": {}}]

    def test_malformed_tool_progress_payload_degrades_to_empty_data(self) -> None:
        events = _drain([": tool_progress {not json", ""])
        assert events == [{"id": "", "event": "tool_progress", "data": {}}]

    def test_real_event_block_still_parses_with_id(self) -> None:
        data = json.dumps({"event": "agent_message", "data": {"text": "hi"}})
        events = _drain(["id: 7", "event: agent_message", f"data: {data}", ""])
        assert events == [
            {"id": "7", "event": "agent_message", "data": {"text": "hi"}}
        ]
