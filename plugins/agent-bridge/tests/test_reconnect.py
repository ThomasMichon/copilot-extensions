"""Streaming engine survives a mid-dispatch service restart (#23).

Regression: the client used to ``sys.exit(1)`` on a connection failure. That
SystemExit (a BaseException) tunneled through ``_stream_feed``'s
``except Exception`` reconnect guards and killed a live dispatch on a brief
daemon restart. The client now raises ``BridgeConnectionError`` (an Exception),
so the engine reconnects and resumes from the caller's acked cursor.
"""

from __future__ import annotations

import pytest

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

        def refresh_endpoint(self):
            return False

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

        def refresh_endpoint(self):
            return False

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


def test_sustained_outage_is_framed_as_resumable(capsys):
    detail = "Cannot connect to agent-bridge at http://127.0.0.1:47000"
    with pytest.raises(SystemExit) as exc_info:
        m._exit_bridge_outage(BridgeConnectionError(detail))

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "[RETRY]" in err
    assert "restart grace" in err
    assert "preserved and resumable" in err
    assert "re-run it shortly" in err
    assert detail in err
    assert all(word not in err.lower() for word in ("died", "stale", "gone"))


class _RecordingRenderer(_Renderer):
    def render_event(self, etype, data):
        return data.get("text", "")


def test_stream_feed_retries_transient_404_within_grace(monkeypatch):
    """A mid-stream session-404 (daemon bounced, session not re-registered yet)
    must NOT be a terminal 'not found' -- follow any port cutover and retry
    within the grace, then resume (dotfiles#1713)."""
    from agent_bridge.client import BridgeClientError

    monkeypatch.setattr(m, "_RECONNECT_BACKOFF", 0)
    monkeypatch.setattr(m, "_STREAM_404_GRACE_S", 30.0)
    calls = {"stream": 0, "refresh": 0, "session": 0}

    class _Client:
        def get_cursor(self, sid, *, caller_id=None):
            return 0

        def stream_events(self, sid, *, after=0, caller_id=None):
            calls["stream"] += 1
            if calls["stream"] == 1:
                raise BridgeClientError(404, "session not found")
            return iter(({"event": "agent_message", "id": "1",
                          "data": {"text": "hi"}},))

        def refresh_endpoint(self):
            calls["refresh"] += 1
            return True

        def ack_cursor(self, sid, up_to, *, caller_id=None):
            return up_to

        def read_range(self, sid, *, start=0, end=None):
            return []

        def get_session(self, sid):
            calls["session"] += 1
            # Running right after the 404 (so it doesn't settle yet), idle once
            # the reconnect has delivered.
            return {"status": "running" if calls["session"] == 1 else "idle"}

    result = m._stream_feed(
        _Client(), "s1", caller_id=None,
        renderer=_RecordingRenderer(), command_timeout=0,
    )
    assert result == "complete"
    assert calls["refresh"] >= 1  # followed the cutover instead of dying
    assert calls["stream"] >= 2   # reconnected after the transient 404


def test_stream_feed_reports_settled_404_as_resumable(monkeypatch, capsys):
    """A session-404 that persists past the grace is reported -- but with
    resumable framing, never a 'died/stale/gone' verdict (dotfiles#1713)."""
    from agent_bridge.client import BridgeClientError

    monkeypatch.setattr(m, "_RECONNECT_BACKOFF", 0)
    monkeypatch.setattr(m, "_STREAM_404_GRACE_S", 0.0)  # first 404 is already settled

    class _Client:
        def get_cursor(self, sid, *, caller_id=None):
            return 0

        def stream_events(self, sid, *, after=0, caller_id=None):
            raise BridgeClientError(404, "session not found")

        def refresh_endpoint(self):
            return False

        def ack_cursor(self, sid, up_to, *, caller_id=None):
            return up_to

        def read_range(self, sid, *, start=0, end=None):
            return []

        def get_session(self, sid):
            return {"status": "running"}

    result = m._stream_feed(
        _Client(), "s1", caller_id=None, renderer=_Renderer(), command_timeout=0
    )
    assert result == "error"
    err = capsys.readouterr().err
    assert "[RETRY]" in err
    assert "resumable" in err
    assert "not found" not in err  # no death verdict
    assert all(word not in err.lower() for word in ("died", "stale", "gone"))
