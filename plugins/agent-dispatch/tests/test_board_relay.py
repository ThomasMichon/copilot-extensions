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
import threading
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


def test_event_loop_trailing_fetch_after_mid_fetch_event(monkeypatch):
    """An event arriving *while* a full re-fetch is already in flight is not
    a no-op: it must trigger exactly one trailing re-fetch afterward (not
    zero -- the mutation would otherwise be invisible until the next long
    reconcile -- and not a pile-up of re-fetches either)."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.01)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with(("event", {"type": "task.progress"}))
    entered = threading.Event()
    release = threading.Event()
    calls = {"full": 0}

    class FakeSnapshot:
        def full_refetch(self):
            calls["full"] += 1
            if calls["full"] == 1:
                # Signal the injector that this fetch is genuinely in
                # flight, then block until it has pushed the mid-fetch
                # event onto the reader's queue.
                entered.set()
                assert release.wait(timeout=5), "injector never released the fetch"
            return [{"id": "t1", "v": calls["full"]}]

        def recompute_only(self):
            raise AssertionError("recompute_only must not run for this test")

    def inject_mid_fetch_event():
        assert entered.wait(timeout=5), "full_refetch never started"
        reader.queue.put(("event", {"type": "task.progress"}))
        release.set()

    injector = threading.Thread(target=inject_mid_fetch_event, daemon=True)
    injector.start()

    # Stop the loop the moment the trailing (second) fetch's delta is
    # emitted -- exactly two full_refetch calls total proves "exactly one
    # trailing fetch," neither zero nor a pile-up.
    stop = _StopAfter(limit=2)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=0.01
    )
    injector.join(timeout=5)

    assert rc == 0
    assert calls["full"] == 2
    assert [frame["entry"]["v"] for frame in stop.emitted] == [1, 2]


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


def test_reconnect_loop_successful_handoff_stops_polling_before_promotion(monkeypatch):
    """A successful reconnect's full reconcile pass becomes the event loop's
    ``prev`` and hands off to it -- the fallback poller must have already
    stopped (no further poll ticks) before that promotion runs, and never
    publishes anything after it. Sharing the snapshot-owner role alone
    doesn't prove this ordering; this test asserts the actual sequence."""
    monkeypatch.setattr(board_relay.time, "sleep", lambda _secs: None)

    order: list[str] = []

    def fake_fetch_rows(args):
        order.append("poll_tick")
        return [{"id": "poll", "v": 1}]

    monkeypatch.setattr(board_cli, "_fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(board_relay, "_build_client", lambda args: object())
    monkeypatch.setattr(
        board_relay, "_daemon_supports_ready_frame", lambda client: True
    )

    class FakeReader:
        def __init__(self, client):
            self.queue: queue.Queue = queue.Queue()
            self.queue.put(("ready", None))

        def start(self):
            pass

    monkeypatch.setattr(board_relay, "_Reader", FakeReader)

    class FakeSnapshot:
        def __init__(self, args):
            pass

        def full_refetch(self):
            order.append("promotion")
            return [{"id": "promoted", "v": 1}]

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)

    recorded = {}

    def fake_event_loop(args, out, reader, snapshot, prev, interval):
        order.append("event_loop_handoff")
        recorded["prev"] = prev
        return "handed-off"

    monkeypatch.setattr(board_relay, "_event_loop", fake_event_loop)

    import io

    result = board_relay._reconnect_loop(
        "args", io.StringIO(), [{"id": "seed", "v": 0}], interval=2.0
    )

    assert result == "handed-off"
    # Exactly one poll tick (the correctness-first attempt before the
    # reconnect succeeds), then the promotion reconcile, then handoff --
    # never a poll tick after promotion.
    assert order == ["poll_tick", "promotion", "event_loop_handoff"]
    assert recorded["prev"] == [{"id": "promoted", "v": 1}]
