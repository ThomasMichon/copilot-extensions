"""Tests for ``board_relay.py`` -- Phase 3a's agent-dispatch CLI relay.

These exercise the single-threaded control loop directly (feeding a plain
``queue.Queue`` as a stand-in for ``_Reader``, and a fake snapshot object),
rather than spinning up a real SSE connection -- `test_coordinator.py`'s own
`test_stream_events_ready_frame_handshake` already covers the real
client/server handshake end-to-end. A fake ``board_cli._emit_frame`` that
returns ``False`` after a bounded number of calls doubles as this test
module's deterministic "stop the loop" mechanism (mirroring the real
contract: the loop ends the moment the reader closes the pipe), so no test
here needs to fake the wall clock.
"""

from __future__ import annotations

import queue
import types

import pytest

from agent_dispatch import board_cli, board_relay


def _reader_with(*items) -> types.SimpleNamespace:
    q: queue.Queue = queue.Queue()
    for item in items:
        q.put(item)
    return types.SimpleNamespace(queue=q)


class _StopAfter:
    """A fake ``board_cli._emit_frame`` that records every emitted frame and
    returns ``False`` (simulating a closed pipe) once ``limit`` frames have
    been emitted, ending the control loop deterministically."""

    def __init__(self, limit: int = 1):
        self.limit = limit
        self.emitted: list[dict] = []

    def __call__(self, obj, out) -> bool:
        self.emitted.append(obj)
        return len(self.emitted) < self.limit


def test_event_loop_debounces_burst_into_single_refetch(monkeypatch):
    """A burst of events arriving close together coalesces into exactly one
    full re-fetch, never one re-fetch per event."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.01)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with(
        ("event", {"type": "task.progress"}),
        ("event", {"type": "task.progress"}),
        ("event", {"type": "task.progress"}),
    )
    calls = {"full": 0}

    class FakeSnapshot:
        def full_refetch(self):
            calls["full"] += 1
            return [{"id": "t1", "v": calls["full"]}]

        def recompute_only(self):
            raise AssertionError("recompute_only must not run for this test")

    stop = _StopAfter(limit=1)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=2.0
    )

    assert rc == 0
    assert calls["full"] == 1
    assert stop.emitted and stop.emitted[0]["type"] == "delta"


def test_event_loop_recompute_tick_never_touches_network(monkeypatch):
    """The local recompute tick fires on its own cadence even with zero
    events pending, and never calls the network-hitting full re-fetch."""
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with()  # empty: every wait times out into "timer"
    calls = {"recompute": 0, "full": 0}

    class FakeSnapshot:
        def recompute_only(self):
            calls["recompute"] += 1
            return [{"id": "t1", "v": calls["recompute"]}]

        def full_refetch(self):
            calls["full"] += 1
            return [{"id": "t1", "v": 99}]

    stop = _StopAfter(limit=1)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=2.0
    )

    assert rc == 0
    assert calls["recompute"] == 1
    assert calls["full"] == 0


def test_event_loop_long_reconcile_runs_a_full_refetch(monkeypatch):
    """The long reconcile fires its own full re-fetch on its own cadence,
    independent of the recompute tick and with zero events pending."""
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 0.01)

    reader = _reader_with()
    calls = {"recompute": 0, "full": 0}

    class FakeSnapshot:
        def recompute_only(self):
            calls["recompute"] += 1
            return []

        def full_refetch(self):
            calls["full"] += 1
            return [{"id": "t1", "v": calls["full"]}]

    stop = _StopAfter(limit=1)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=2.0
    )

    assert rc == 0
    assert calls["full"] == 1
    assert calls["recompute"] == 0


def test_event_loop_disconnect_hands_off_to_reconnect(monkeypatch):
    reader = _reader_with(("disconnected", RuntimeError("boom")))
    sentinel = object()
    recorded = {}

    def fake_reconnect(args, out, prev, interval):
        recorded["prev"] = prev
        recorded["interval"] = interval
        return sentinel

    monkeypatch.setattr(board_relay, "_reconnect_loop", fake_reconnect)

    class FakeSnapshot:
        pass

    result = board_relay._event_loop(
        "args", "out", reader, FakeSnapshot(), ["prev-rows"], interval=3.0
    )

    assert result is sentinel
    assert recorded == {"prev": ["prev-rows"], "interval": 3.0}


def test_run_relay_raises_when_daemon_doesnt_advertise_support(monkeypatch):
    closed = {"n": 0}

    class FakeClient:
        def health(self):
            return {"status": "ok"}  # no events_ready_frame key

        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(board_relay, "_build_client", lambda args: FakeClient())

    with pytest.raises(board_relay.RelayUnavailable):
        board_relay.run_relay(
            "args", None, initial_rows=[{"id": "t1"}], interval=2.0
        )
    assert closed["n"] == 1


def test_reconnect_loop_rebuilds_a_fresh_client_each_attempt(monkeypatch):
    """Every reconnect attempt re-resolves the endpoint via a fresh client,
    never retrying a stale one -- and exhausting the bounded retry cap
    settles into the ordinary poll loop."""
    monkeypatch.setattr(board_relay, "MAX_RECONNECT_ATTEMPTS", 3)
    monkeypatch.setattr(board_relay.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(board_cli, "_fetch_rows", lambda args: ["poll-tick"])

    build_calls = {"n": 0}

    def fake_build_client(args):
        build_calls["n"] += 1
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(board_relay, "_build_client", fake_build_client)

    poll_calls = {}

    def fake_poll_loop(args, out, prev, interval):
        poll_calls["prev"] = prev
        poll_calls["interval"] = interval
        return 0

    monkeypatch.setattr(board_cli, "poll_loop", fake_poll_loop)

    rc = board_relay._reconnect_loop("args", None, ["seed"], interval=2.0)

    assert rc == 0
    assert build_calls["n"] == 3  # once per bounded attempt, never reused
    assert poll_calls["interval"] == 2.0
