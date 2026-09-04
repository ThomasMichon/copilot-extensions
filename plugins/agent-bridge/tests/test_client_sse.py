"""Tests for the CLI client's SSE line parsing in ``stream_events``.

Locks in the contract that liveness rides the comment channel:
``: tool_progress <json>`` surfaces as a ``tool_progress`` dict (cursor-neutral,
``id=""``), a bare comment surfaces as ``_heartbeat``, and real ``event:``/
``data:`` blocks still parse with their durable id.
"""

from __future__ import annotations

import gc
import json
import sys
import weakref
from unittest.mock import patch

import pytest

from agent_bridge.client import BridgeClient


class _FakeSseResp:
    """Iterable byte-line response mimicking a urlopen SSE stream."""

    def __init__(self, lines: list[str]) -> None:
        # Each SSE line is delivered as its own bytes chunk, newline-terminated.
        self._lines = [(ln + "\n").encode() for ln in lines]
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class _FailingCloseSseResp(_FakeSseResp):
    def __init__(
        self,
        lines: list[str],
        failure_type: type[BaseException] = OSError,
    ) -> None:
        super().__init__(lines)
        self.failure_type = failure_type

    def close(self) -> None:
        self.closed = True
        raise self.failure_type("close failed")


def _drain(lines: list[str]) -> list[dict]:
    client = BridgeClient("http://127.0.0.1:0", "tok")
    with patch(
        "agent_bridge.client.urllib.request.urlopen",
        return_value=_FakeSseResp(lines),
    ):
        return list(client.stream_events("sess-1"))


class TestSseCommentParsing:
    def test_abandoned_stream_closes_response(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FakeSseResp([": heartbeat", ""])
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            stream = client.stream_events("sess-1")
            assert next(stream)["event"] == "_heartbeat"
            del stream
            gc.collect()

        assert response.closed is True

    def test_stream_context_manager_closes_response(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FakeSseResp([": heartbeat", ""])
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            with client.stream_events("sess-1") as stream:
                assert next(stream)["event"] == "_heartbeat"

        assert response.closed is True

    def test_context_manager_preserves_body_exception(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FailingCloseSseResp([])
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            with pytest.raises(ValueError, match="body failed"):
                with client.stream_events("sess-1"):
                    raise ValueError("body failed")

        assert response.closed is True

    def test_context_manager_surfaces_close_failure(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FailingCloseSseResp([])
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            with pytest.raises(OSError, match="close failed"):
                with client.stream_events("sess-1"):
                    pass

        assert response.closed is True

    @pytest.mark.parametrize("failure_type", [OSError, KeyboardInterrupt])
    def test_abandoned_stream_suppresses_close_failure(
        self, monkeypatch, failure_type
    ) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FailingCloseSseResp(
            [": heartbeat", ""],
            failure_type,
        )
        unraisable = []
        monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            stream = client.stream_events("sess-1")
            stream_ref = weakref.ref(stream)
            del stream
            gc.collect()

        assert stream_ref() is None
        assert response.closed is True
        assert unraisable == []

    def test_exhausted_stream_suppresses_close_failure(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FailingCloseSseResp([])
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            stream = client.stream_events("sess-1")
            with pytest.raises(StopIteration):
                next(stream)

        assert response.closed is True

    def test_exhausted_stream_preserves_cancellation(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        response = _FailingCloseSseResp([], KeyboardInterrupt)
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=response,
        ):
            stream = client.stream_events("sess-1")
            with pytest.raises(KeyboardInterrupt):
                next(stream)

        assert response.closed is True

    def test_successful_stream_resets_outage_budget(self) -> None:
        client = BridgeClient("http://127.0.0.1:0", "tok")
        client._outage_deadline = 1.0
        with patch(
            "agent_bridge.client.urllib.request.urlopen",
            return_value=_FakeSseResp([": heartbeat", ""]),
        ):
            assert list(client.stream_events("sess-1"))
        assert client._outage_deadline is None

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

    def test_empty_event_block_does_not_leak_id_or_type(self) -> None:
        data = json.dumps({"event": "agent_message", "data": {"text": "hi"}})

        events = _drain(
            [
                "id: 6",
                "event: stale",
                "",
                f"data: {data}",
                "",
            ]
        )

        assert events == [
            {"id": "", "event": "agent_message", "data": {"text": "hi"}}
        ]

    def test_controlled_event_keeps_timestamp_and_continuity(self) -> None:
        data = json.dumps(
            {
                "event": "assistant.turn_end",
                "data": {"stop_reason": "end_turn"},
                "timestamp": 123.0,
                "continuity_id": "epoch-a",
            }
        )

        events = _drain(
            ["id: 8", "event: assistant.turn_end", f"data: {data}", ""]
        )

        assert events == [
            {
                "id": "8",
                "event": "assistant.turn_end",
                "data": {"stop_reason": "end_turn"},
                "timestamp": 123.0,
                "continuity_id": "epoch-a",
            }
        ]
