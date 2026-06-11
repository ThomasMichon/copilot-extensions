"""Simulation harness for the streaming engine + delivery-cursor discipline.

These tests drive the *real* CLI streaming engine (``_stream_feed``) against a
fake bridge that mimics the shell-tool stream/collect/continue flow: events are
produced over a turn, then the stream goes quiet (heartbeat), and the engine
acks the delivery cursor only after flushing each event.

They assert the issue #22 acceptance properties:

- the host ingests one contiguous, gap-free, duplicate-free stream;
- an ungraceful interrupt mid-stream resumes from the last acked event --
  nothing skipped;
- the cursor advances only for content actually delivered.
"""

from __future__ import annotations

import contextlib
import io

from agent_bridge.__main__ import _stream_feed
from agent_bridge.render import StreamRenderer


def _turn_events():
    """A representative turn: thoughts, a tool call, message chunks, complete."""
    return [
        {"id": 1, "event": "agent_thought", "data": {"text": "planning"}},
        {"id": 2, "event": "tool_call_start",
         "data": {"tool_call_id": "t1", "title": "Read file"}},
        {"id": 3, "event": "tool_call_update",
         "data": {"tool_call_id": "t1", "status": "completed"}},
        {"id": 4, "event": "agent_message", "data": {"text": "Here "}},
        {"id": 5, "event": "agent_message", "data": {"text": "is the answer."}},
        {"id": 6, "event": "turn_complete", "data": {"stop_reason": "end_turn"}},
    ]


class FakeBridge:
    """In-memory stand-in for BridgeClient implementing the engine's surface."""

    def __init__(self, events, *, status="idle"):
        self.events = events
        self.status = status
        self.cursors: dict[str | None, int] = {}
        self.acks: list[tuple[str | None, int]] = []
        self.streamed: list[int] = []
        self._kill_after: int | None = None
        self._kill_exc: BaseException | None = None

    def kill_after(self, n: int, exc: BaseException) -> None:
        """Raise *exc* after streaming *n* events (simulate ungraceful death)."""
        self._kill_after = n
        self._kill_exc = exc

    # -- engine surface ------------------------------------------------------

    def get_cursor(self, session_id, *, caller_id=None):
        return self.cursors.get(caller_id, 0)

    def ack_cursor(self, session_id, last_id, *, caller_id=None):
        cur = self.cursors.get(caller_id, 0)
        new = max(cur, last_id)
        self.cursors[caller_id] = new
        self.acks.append((caller_id, last_id))
        return new

    def get_session(self, session_id):
        return {"status": self.status}

    def read_range(self, session_id, *, start=0, end=None):
        return [
            e for e in self.events
            if e["id"] >= start and (end is None or e["id"] <= end)
        ]

    def stream_events(self, session_id, *, after=0, caller_id=None):
        delivered_this_call = 0
        for e in self.events:
            if e["id"] > after:
                self.streamed.append(e["id"])
                yield e
                delivered_this_call += 1
                if (
                    self._kill_after is not None
                    and delivered_this_call >= self._kill_after
                ):
                    raise self._kill_exc
        # Stream goes quiet -> heartbeat sentinel, prompting the engine to
        # check for completion.
        yield {"id": "", "event": "_heartbeat", "data": {}}


def _run(bridge, caller_id="wt-1"):
    renderer = StreamRenderer(color=False)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        status = _stream_feed(
            bridge, "sess-1",
            caller_id=caller_id,
            renderer=renderer,
            command_timeout=0.0,
        )
    return status, buf.getvalue()


class TestContiguousStream:
    def test_full_turn_delivered_once(self) -> None:
        bridge = FakeBridge(_turn_events())
        status, out = _run(bridge)
        assert status == "complete"
        # Every event delivered exactly once, in order, no gaps.
        assert bridge.streamed == [1, 2, 3, 4, 5, 6]
        # The actual answer text is present in full.
        assert "Here is the answer." in out
        # Cursor caught up to the last event.
        assert bridge.get_cursor("sess-1", caller_id="wt-1") == 6

    def test_cursor_only_advances_for_delivered_events(self) -> None:
        bridge = FakeBridge(_turn_events())
        _run(bridge)
        # Acks correspond exactly to delivered event ids (after flush).
        acked_ids = [aid for (_caller, aid) in bridge.acks]
        assert acked_ids == [1, 2, 3, 4, 5, 6]


class TestResumeAfterInterrupt:
    def test_interrupt_then_resume_no_skip_no_duplicate(self) -> None:
        events = _turn_events()
        bridge = FakeBridge(events)
        # Ungraceful interrupt after delivering 3 events.
        bridge.kill_after(3, KeyboardInterrupt())

        status1, out1 = _run(bridge)
        assert status1 == "interrupted"
        # Cursor reflects exactly what was flushed + acked.
        assert bridge.get_cursor("sess-1", caller_id="wt-1") == 3

        # Resume: clear the kill switch and stream again from the cursor.
        bridge.kill_after(0, KeyboardInterrupt())
        bridge._kill_after = None
        status2, out2 = _run(bridge)
        assert status2 == "complete"

        # No event skipped, none duplicated across the two runs.
        assert sorted(bridge.streamed) == [1, 2, 3, 4, 5, 6]
        assert len(bridge.streamed) == len(set(bridge.streamed))
        # The full answer is delivered across the resume seam.
        assert "Here is the answer." in (out1 + out2)

    def test_resume_from_advanced_cursor_delivers_remainder(self) -> None:
        events = _turn_events()
        bridge = FakeBridge(events)
        # Pretend a prior consumer already acked through event 4.
        bridge.cursors["wt-1"] = 4

        status, _out = _run(bridge)
        assert status == "complete"
        # Only the un-acked remainder (5, 6) is streamed.
        assert bridge.streamed == [5, 6]


class TestEmptyAndSettled:
    def test_already_idle_no_events(self) -> None:
        bridge = FakeBridge([], status="idle")
        status, _out = _run(bridge)
        assert status == "complete"
        assert bridge.streamed == []
