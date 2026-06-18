"""Integration tests for the get_events SSE generator (#43).

The ``event_stream()`` generator inside ``routes.sessions.get_events`` -- the
``while True`` loop and especially its quiet-period branch (tool_progress vs
bare heartbeat) -- was historically unexercised by any unit test. A ``_time``
NameError once shipped in that branch and passed the entire suite (commit
10d5e25, fixed in e051ce6). These tests drive the *real* generator end to end,
through all three sub-branches, so a regression in that loop fails the suite.

The generator is driven directly (the route returns a StreamingResponse whose
``body_iterator`` is the real generator). A real in-memory ``EventLog`` backs it
-- only ``wait_for_events``' timeout is shortened so the quiet branch fires in
milliseconds instead of the production 30s.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_bridge.events import EventLog
from agent_bridge.routes.sessions import get_events


def _short_timeout_log() -> EventLog:
    """A real in-memory EventLog whose wait_for_events returns fast when idle."""
    log = EventLog()
    real_wait = log.wait_for_events

    async def fast_wait(after: int, timeout: float = 30.0):
        # Ignore the generator's hardcoded 30s; fire the quiet branch quickly.
        return await real_wait(after, timeout=0.05)

    log.wait_for_events = fast_wait  # type: ignore[method-assign]
    return log


def _request_for(log: EventLog, sid: str = "sess-1") -> SimpleNamespace:
    """A minimal Request stand-in exposing app.state.session_manager.

    ``get_events`` only touches ``request.app.state.session_manager`` and, for
    that manager, ``get_session(sid).event_log``.
    """
    session = SimpleNamespace(event_log=log)
    mgr = SimpleNamespace(get_session=lambda s: session if s == sid else None)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=mgr)))


async def _start_stream(log: EventLog, sid: str = "sess-1"):
    """Return the live async iterator over the generator's SSE frames."""
    resp = await get_events(sid, _request_for(log, sid), after=0, caller_id=None)
    return resp.body_iterator


def _decode(frame) -> str:
    return frame.decode() if isinstance(frame, (bytes, bytearray)) else frame


@pytest.mark.asyncio
async def test_sse_stream_drives_all_branches():
    """events branch -> tool_progress (quiet+busy) -> heartbeat (quiet+idle)."""
    log = _short_timeout_log()
    log.append("agent_message", {"text": "hi"})  # id 1
    log.append(
        "tool_call_start",
        {"tool_call_id": "t1", "title": "Build webapp", "raw_input": {"command": "rush build"}},
    )  # id 2 -- in flight

    it = await _start_stream(log)

    # --- events branch: both seeded events stream first, with ids ---
    f1 = _decode(await it.__anext__())
    assert "event: agent_message" in f1
    assert "id: 1" in f1

    f2 = _decode(await it.__anext__())
    assert "event: tool_call_start" in f2
    assert "id: 2" in f2

    # --- quiet branch, tool in flight: cursor-neutral tool_progress comment ---
    f3 = _decode(await it.__anext__())
    assert f3.startswith(": tool_progress ")
    payload = json.loads(f3[len(": tool_progress "):].strip())
    assert payload["title"] == "Build webapp"
    assert payload["command"] == "rush build"
    assert "elapsed_s" in payload  # derived from started_at -> the once-broken path

    # Close the tool so the next quiet period has nothing in flight.
    log.append("tool_call_update", {"tool_call_id": "t1", "status": "completed"})  # id 3

    f4 = _decode(await it.__anext__())
    assert "event: tool_call_update" in f4
    assert "id: 3" in f4

    # --- quiet branch, idle: bare heartbeat comment ---
    f5 = _decode(await it.__anext__())
    assert f5 == ": heartbeat\n\n"

    await it.aclose()


@pytest.mark.asyncio
async def test_sse_heartbeat_when_idle():
    """Quiet + no tool in flight yields a bare heartbeat (the _time-NameError line)."""
    log = _short_timeout_log()
    it = await _start_stream(log)
    try:
        frame = _decode(await it.__anext__())
        assert frame == ": heartbeat\n\n"
    finally:
        await it.aclose()


@pytest.mark.asyncio
async def test_sse_tool_progress_when_busy():
    """Quiet + a tool in flight yields a tool_progress comment, never an id."""
    log = _short_timeout_log()
    log.append(
        "tool_call_start",
        {"tool_call_id": "t9", "title": "Lint", "raw_input": {"command": "rush lint"}},
    )  # id 1

    it = await _start_stream(log)
    try:
        # First frame is the seeded start event...
        first = _decode(await it.__anext__())
        assert "event: tool_call_start" in first
        # ...then the quiet period surfaces the in-flight tool.
        second = _decode(await it.__anext__())
        assert second.startswith(": tool_progress ")
        assert "id:" not in second  # comments are cursor-neutral by construction
        payload = json.loads(second[len(": tool_progress "):].strip())
        assert payload["tool_call_id"] == "t9"
    finally:
        await it.aclose()
