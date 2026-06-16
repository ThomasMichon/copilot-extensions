"""Streaming engine survives a mid-dispatch service restart (#23).

Regression: the client used to ``sys.exit(1)`` on a connection failure. That
SystemExit (a BaseException) tunneled through ``_stream_feed``'s
``except Exception`` reconnect guards and killed a live dispatch on a brief
daemon restart. The client now raises ``BridgeConnectionError`` (an Exception),
so the engine reconnects and resumes from the caller's acked cursor.
"""

from __future__ import annotations

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeConnectionError


class _Renderer:
    def heartbeat_line(self, elapsed):
        return ""

    def tool_progress_line(self, data):
        return ""

    def render_event(self, etype, data):
        return ""


def test_stream_feed_reconnects_after_connection_error(monkeypatch):
    monkeypatch.setattr(m, "_RECONNECT_BACKOFF", 0)
    calls = {"stream": 0, "session": 0}

    class _Client:
        def get_cursor(self, sid, *, caller_id=None):
            return 0

        def stream_events(self, sid, *, after=0, caller_id=None):
            calls["stream"] += 1
            if calls["stream"] == 1:
                # Daemon restarting -- must NOT kill the dispatch.
                raise BridgeConnectionError("Cannot connect to agent-bridge")
            return iter(())  # reconnected: a quiet pass, nothing new

        def ack_cursor(self, sid, up_to, *, caller_id=None):
            return up_to

        def read_range(self, sid, *, start=0, end=None):
            return []

        def get_session(self, sid):
            calls["session"] += 1
            # Still running right after the drop; settles only once reconnected.
            return {"status": "running" if calls["session"] == 1 else "idle"}

    result = m._stream_feed(
        _Client(), "s1", caller_id=None, renderer=_Renderer(), command_timeout=0
    )
    assert result == "complete"
    assert calls["stream"] >= 2  # reconnected rather than crashing


def test_stream_feed_tolerates_request_failure_in_settled_check(monkeypatch):
    # A _request-based call (get_session) raising BridgeConnectionError mid-loop
    # must be swallowed by the reconnect guard, not propagate.
    monkeypatch.setattr(m, "_RECONNECT_BACKOFF", 0)
    calls = {"stream": 0, "session": 0}

    class _Client:
        def get_cursor(self, sid, *, caller_id=None):
            return 0

        def stream_events(self, sid, *, after=0, caller_id=None):
            calls["stream"] += 1
            return iter(())

        def ack_cursor(self, sid, up_to, *, caller_id=None):
            return up_to

        def read_range(self, sid, *, start=0, end=None):
            return []

        def get_session(self, sid):
            calls["session"] += 1
            if calls["session"] == 1:
                raise BridgeConnectionError("down mid-check")
            return {"status": "idle"}

    result = m._stream_feed(
        _Client(), "s1", caller_id=None, renderer=_Renderer(), command_timeout=0
    )
    assert result == "complete"
