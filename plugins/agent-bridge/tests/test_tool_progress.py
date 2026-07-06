"""Tests for the server-side tool-progress liveness SSE framing.

Liveness rides the SSE *comment* channel (``: tool_progress <json>``), not an
``event:``/``data:`` block -- so spec-compliant ``EventSource`` consumers (and
HTTP API consumers like Neuron Forge) ignore it, and it can never carry an
``id:`` to move a delivery cursor. Only the CLI renderer opts in to parsing it.
"""

from __future__ import annotations

import asyncio
import json

from agent_bridge.routes import sessions as sessions_mod
from agent_bridge.routes.sessions import _tool_progress_sse

_PREFIX = ": tool_progress "


def _parse_tool_progress(block: str) -> dict:
    """Extract the JSON payload from a ``: tool_progress <json>`` comment."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one comment line, got {lines!r}"
    line = lines[0]
    assert line.startswith(_PREFIX), f"unexpected framing: {line!r}"
    return json.loads(line[len(_PREFIX):])


class TestToolProgressSse:
    def test_framed_as_comment_not_event(self) -> None:
        active = {
            "tool_call_id": "t1",
            "title": "Build webapp",
            "kind": "execute",
            "command": "rush build -t @scope/webapp",
            "started_at": 1000.0,
            "started_id": 42,
        }
        block = _tool_progress_sse(active, now=1000.0 + 1027)
        # Comment channel only -- no real-event framing that a relay consumer
        # could mistake for a durable event.
        assert "event:" not in block
        assert "data:" not in block
        data = _parse_tool_progress(block)
        assert data["title"] == "Build webapp"
        assert data["command"] == "rush build -t @scope/webapp"
        assert round(data["elapsed_s"]) == 1027

    def test_has_no_id_line_so_cursor_is_untouched(self) -> None:
        active = {"tool_call_id": "t1", "title": "X", "started_at": 5.0}
        block = _tool_progress_sse(active, now=10.0)
        # A comment structurally cannot carry an ``id:`` -> never acked.
        assert "id:" not in block
        assert block.startswith(_PREFIX)

    def test_started_at_not_leaked_to_client(self) -> None:
        active = {"tool_call_id": "t1", "title": "X", "started_at": 5.0}
        data = _parse_tool_progress(_tool_progress_sse(active, now=10.0))
        assert "started_at" not in data
        assert data["elapsed_s"] == 5.0

    def test_multiline_command_stays_single_comment_line(self) -> None:
        active = {"tool_call_id": "t1", "title": "X",
                  "command": "line one\nline two", "started_at": 0.0}
        block = _tool_progress_sse(active, now=1.0)
        # JSON-escapes the newline -> exactly one comment line (SSE-safe: a
        # literal newline would terminate the comment).
        comment_lines = [ln for ln in block.splitlines() if ln.startswith(":")]
        assert len(comment_lines) == 1
        data = _parse_tool_progress(block)
        assert data["command"] == "line one\nline two"

    def test_elapsed_never_negative(self) -> None:
        active = {"tool_call_id": "t1", "title": "X", "started_at": 100.0}
        data = _parse_tool_progress(_tool_progress_sse(active, now=90.0))
        assert data["elapsed_s"] == 0.0


class _FakeEventLog:
    def __init__(self, active: dict | None) -> None:
        self._active = active

    async def wait_for_events(self, cursor: int, timeout: float) -> list:
        return []  # always quiet -> exercise the liveness branch immediately

    def active_tool_call(self) -> dict | None:
        return self._active


class _FakeSession:
    def __init__(self, active: dict | None) -> None:
        self.event_log = _FakeEventLog(active)
        self.session_id = "sess-1"


class _FakeMgr:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def get_session(self, _session_id: str) -> _FakeSession:
        return self._session

    def add_subscriber(self, _session_id: str) -> None:
        pass

    def remove_subscriber(self, _session_id: str) -> None:
        pass


class _FakeRequest:
    def __init__(self, mgr: _FakeMgr) -> None:
        self.app = type("_App", (), {"state": type("_State", (), {})()})()
        self.app.state.session_manager = mgr


def _first_stream_chunk(active: dict | None) -> str:
    """Drive the get_events SSE generator one iteration in the quiet branch."""
    req = _FakeRequest(_FakeMgr(_FakeSession(active)))

    async def _run() -> str:
        resp = await sessions_mod.get_events("sess-1", req, after=0, caller_id=None)
        async for chunk in resp.body_iterator:
            return chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk
        return ""

    return asyncio.run(_run())


class TestQuietBranchLiveness:
    def test_active_tool_call_yields_tool_progress_comment(self) -> None:
        # Regression: the liveness branch once referenced an undefined ``_time``
        # (imported only inside ack_cursor), so this path raised NameError the
        # instant a remote blocked on a quiet tool call -- the exact "is it dead?"
        # scenario it exists to answer.
        chunk = _first_stream_chunk(
            {"tool_call_id": "t1", "title": "Build", "command": "rush build",
             "started_at": 0.0}
        )
        assert chunk.startswith(": tool_progress ")
        data = json.loads(chunk[len(": tool_progress "):].strip())
        assert data["title"] == "Build"

    def test_no_active_tool_call_yields_bare_heartbeat(self) -> None:
        chunk = _first_stream_chunk(None)
        assert chunk.strip() == ": heartbeat"
