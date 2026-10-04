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
import time
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


class _FakeClient:
    """A minimal stand-in for ``DispatchClient`` wherever a test only needs
    something with a ``close()`` method (the real client is never actually
    used -- ``health``/``stream_events`` are reached through separately
    monkeypatched module-level functions/classes instead)."""

    base_url = "http://fake-coordinator"

    def close(self) -> None:
        pass


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


def test_event_loop_coalesces_events_arriving_during_the_rate_limit_wait(
    monkeypatch,
):
    """An event arriving while a fetch is deferred inside the post-debounce
    rate-limit wait (waiting out `refetch_floor` before the pending full
    re-fetch is allowed to run) must be coalesced into that same pending
    fetch -- not treated as a fresh wake that triggers a second, redundant
    trailing full-board fetch, even though the fetch about to run already
    covers it (sustained heartbeat/activity traffic could otherwise keep
    the relay fetching at the floor cadence indefinitely)."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 0.2)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with(("event", {"type": "task.progress"}))
    injected = threading.Event()

    def inject_event_during_rate_limit_wait():
        time.sleep(0.05)  # well within the ~0.2s rate-limit wait
        reader.queue.put(("event", {"type": "task.progress"}))
        injected.set()

    injector = threading.Thread(
        target=inject_event_during_rate_limit_wait, daemon=True
    )
    injector.start()

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
        None, None, reader, FakeSnapshot(), [], interval=0.0
    )
    injector.join(timeout=5)

    assert rc == 0
    assert injected.is_set()
    # Exactly one full fetch -- the injected mid-wait event must be
    # drained/coalesced into it, never left on the queue to trigger a
    # second, redundant trailing fetch.
    assert calls["full"] == 1


def test_event_loop_recompute_tick_survives_continuous_event_traffic(
    monkeypatch,
):
    """Continuous event traffic (events arriving faster than any single
    `queue.get()` timeout could elapse) must never starve already-due
    timer work. Once a fetch is deferred (`next_event_fetch_at` set), a
    steady stream of further events previously kept re-entering the
    `queue.get()` call with a real item every time, so `queue.Empty` (the
    only path into `kind == "timer"`) could never fire and the recompute/
    reconcile/pending-fetch timers would starve indefinitely. This proves
    the recompute tick still fires repeatedly even while events keep
    arriving back-to-back, faster than the recompute cadence itself."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 10.0)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 0.03)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with(("event", {"type": "task.progress"}))
    stop_injecting = threading.Event()

    def inject_continuous_events():
        # Faster than RECOMPUTE_INTERVAL_SECONDS, so the control loop's
        # `queue.get()` keeps finding a real item instead of ever timing
        # out, for as long as this injector runs.
        while not stop_injecting.is_set():
            reader.queue.put(("event", {"type": "task.activity_updated"}))
            time.sleep(0.005)

    injector = threading.Thread(target=inject_continuous_events, daemon=True)
    injector.start()

    calls = {"full": 0, "recompute": 0}

    class FakeSnapshot:
        def full_refetch(self):
            calls["full"] += 1
            return [{"id": "t1", "v": calls["full"]}]

        def recompute_only(self):
            calls["recompute"] += 1
            return [{"id": "t1", "v": calls["recompute"]}]

    stop = _StopAfter(limit=3)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    try:
        rc = board_relay._event_loop(
            None, None, reader, FakeSnapshot(), [], interval=10.0
        )
    finally:
        stop_injecting.set()
        injector.join(timeout=5)

    assert rc == 0
    # The recompute tick must have fired repeatedly despite the continuous
    # event stream -- the bug this guards against is it never firing at
    # all (starved) while events keep arriving.
    assert calls["recompute"] >= 3
    # The 10s rate-limit floor is far longer than this test's own runtime,
    # so the deferred fetch must not have run yet either.
    assert calls["full"] == 0


def test_event_loop_rate_limit_wait_never_blocks_independent_timers(
    monkeypatch,
):
    """A long `--interval` (and therefore a long `refetch_floor`) must
    never freeze the independent recompute/reconcile timers for its own
    duration -- an event wake that is still inside the rate-limit floor
    must defer its own fetch and return control to the scheduler, not
    block the single control loop in a sleep/drain for the whole wait.
    This proves the recompute tick keeps firing (several times) while an
    event-woken fetch sits pending for a much longer rate-limit floor."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 1.0)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with(("event", {"type": "task.progress"}))
    calls = {"full": 0, "recompute": 0}

    class FakeSnapshot:
        def full_refetch(self):
            calls["full"] += 1
            return [{"id": "t1", "v": calls["full"]}]

        def recompute_only(self):
            calls["recompute"] += 1
            return [{"id": "t1", "v": calls["recompute"]}]

    # Stop once the recompute tick has fired several times -- well before
    # the 1.0s rate-limit floor elapses -- proving it ran concurrently with
    # (not blocked by) the pending event-woken fetch.
    stop = _StopAfter(limit=3)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    # interval=1.0 mirrors a long `--interval` in practice (`refetch_floor`
    # = max(MIN_REFETCH_INTERVAL_SECONDS, interval) = 1.0s here).
    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=1.0
    )

    assert rc == 0
    assert calls["recompute"] >= 3
    # The event-woken fetch must not have fired yet -- three 0.02s
    # recompute cycles take ~0.06s, far short of the 1.0s rate-limit floor.
    assert calls["full"] == 0


def test_event_loop_serializes_every_writer_never_running_concurrently(
    monkeypatch,
):
    """The effort's Validation Plan requires proving the event-woken fetch,
    the long reconcile, and the local recompute tick never run
    concurrently (`efforts/active/pivot-streaming-transport/README.md`'s
    Validation Plan; `phase-3-design.md`'s "serialize every writer" design
    bullet). The single-threaded control loop achieves this by
    construction -- there is only ever one writer active at a time, with no
    explicit lock needed -- rather than the design document's own literal
    snapshot-owner-lock mechanism (see `board_relay.py`'s own module
    docstring for why). This test makes the event wake and the long
    reconcile/recompute timers all fall due together (every interval set to
    0), instruments every writer entry/exit with a shared counter (plus a
    real sleep inside each call, so an actual overlap would be observable),
    and asserts the maximum concurrent writer count ever seen is exactly
    1."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 0.0)

    reader = _reader_with(("event", {"type": "task.progress"}))

    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def _enter() -> None:
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])

    def _exit() -> None:
        with lock:
            state["active"] -= 1

    class FakeSnapshot:
        def __init__(self):
            self.calls = 0

        def full_refetch(self):
            _enter()
            try:
                time.sleep(0.02)
                self.calls += 1
                return [{"id": "t1", "v": self.calls}]
            finally:
                _exit()

        def recompute_only(self):
            _enter()
            try:
                time.sleep(0.02)
                self.calls += 1
                return [{"id": "t1", "v": self.calls}]
            finally:
                _exit()

    # Enough frames to let the one queued event, then at least one
    # recompute tick and one long reconcile (all due immediately with every
    # interval at 0), each actually run.
    stop = _StopAfter(limit=3)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=0.0
    )

    assert rc == 0
    assert len(stop.emitted) == 3
    assert state["max_active"] == 1
    assert state["active"] == 0  # every entry was matched by an exit


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


def test_event_loop_forces_reconnect_after_repeated_reconcile_failures(
    monkeypatch,
):
    """The SSE read timeout is unbounded and the coordinator sends no
    keepalive, so a hung/half-open connection can sit silently forever --
    the reader thread may never observe a disconnect even though every
    pinned-endpoint `/tasks` probe keeps failing. `_event_loop` must not
    just keep rescheduling a failing long reconcile forever: after
    `MAX_CONSECUTIVE_FETCH_FAILURES` failures in a row it must force a
    reconnect (return `_Disconnected`) so the caller re-resolves
    `active.json` and falls back to polling instead of leaving the board
    stale indefinitely."""
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 0.001)
    monkeypatch.setattr(board_relay, "MAX_CONSECUTIVE_FETCH_FAILURES", 3)

    reader = _reader_with()
    calls = {"full": 0}

    class FakeSnapshot:
        def recompute_only(self):
            raise AssertionError("recompute_only must not run for this test")

        def full_refetch(self):
            calls["full"] += 1
            return None  # every reconcile fails

    result = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), ["prev-rows"], interval=2.0
    )

    assert isinstance(result, board_relay._Disconnected)
    assert result.prev == ["prev-rows"]
    # Exactly 3 failed attempts before giving up -- neither fewer (too
    # eager) nor more (never gives up at all, the bug this guards against).
    assert calls["full"] == 3


def test_event_loop_resets_failure_count_on_a_successful_reconcile(
    monkeypatch,
):
    """A single successful full_refetch() must reset the consecutive-
    failure counter -- an occasional transient blip must never accumulate
    toward the reconnect threshold across unrelated successful cycles."""
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 0.001)
    monkeypatch.setattr(board_relay, "MAX_CONSECUTIVE_FETCH_FAILURES", 2)

    reader = _reader_with()
    calls = {"full": 0}

    class FakeSnapshot:
        def recompute_only(self):
            raise AssertionError("recompute_only must not run for this test")

        def full_refetch(self):
            calls["full"] += 1
            # Fail, succeed, fail, succeed, ... -- never two failures in a
            # row, so the threshold (2) must never be reached.
            if calls["full"] % 2 == 1:
                return None
            return [{"id": "t1", "v": calls["full"]}]

    stop = _StopAfter(limit=1)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=2.0
    )

    assert rc == 0
    assert calls["full"] == 2  # fail once, then the successful retry


def test_event_loop_disconnect_returns_disconnected_without_recursing(monkeypatch):
    """`_event_loop` never calls the reconnect loop itself -- it returns a
    plain `_Disconnected(prev)` for the top-level iterative driver to act
    on, so a channel that disconnects/reconnects many times never grows the
    Python call stack."""
    reader = _reader_with(("disconnected", RuntimeError("boom")))

    class FakeSnapshot:
        pass

    result = board_relay._event_loop(
        "args", "out", reader, FakeSnapshot(), ["prev-rows"], interval=3.0
    )

    assert isinstance(result, board_relay._Disconnected)
    assert result.prev == ["prev-rows"]


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


def test_daemon_supports_ready_frame_rejects_a_draining_coordinator():
    """A coordinator mid zero-downtime cutover (flip-then-drain window)
    must never be treated as a legitimately connectable target just
    because it still advertises `events_ready_frame` -- a reconnect that
    resolves the predecessor's endpoint just before the routing-table flip
    could otherwise finish against that draining generation, stop the
    fallback poller, and promote a snapshot that goes stale the moment the
    predecessor actually retires. `status: draining` must raise
    `HealthCheckFailed` (a transient connection failure), the same as any
    other health-check problem, so the caller routes it through the normal
    reconnect/retry path."""

    class DrainingClient:
        def health(self):
            return {"status": "draining", "events_ready_frame": True}

    with pytest.raises(board_relay.HealthCheckFailed):
        board_relay._daemon_supports_ready_frame(DrainingClient())


def test_daemon_supports_ready_frame_rejects_the_draining_flag_too():
    """Same rejection via the boolean `draining` field, in case a
    coordinator generation reports it that way instead of (or alongside)
    `status`."""

    class DrainingClient:
        def health(self):
            return {"draining": True, "events_ready_frame": True}

    with pytest.raises(board_relay.HealthCheckFailed):
        board_relay._daemon_supports_ready_frame(DrainingClient())


def test_daemon_supports_ready_frame_accepts_a_healthy_coordinator():
    """A non-draining, capability-advertising coordinator is unaffected by
    the draining check."""

    class HealthyClient:
        def health(self):
            return {"status": "ok", "events_ready_frame": True}

    assert board_relay._daemon_supports_ready_frame(HealthyClient()) is True


def test_connect_folds_a_draining_coordinator_into_disconnected(monkeypatch):
    """End-to-end through `_connect`: a draining coordinator on the very
    first connection attempt must be treated like any other transient
    connection failure (`_Disconnected`, falling back to the caller's own
    reconnect path) -- never `RelayUnavailable` (which would mean "this
    daemon permanently doesn't support the fast path at all")."""
    closed = {"n": 0}

    class DrainingClient:
        def health(self):
            return {"status": "draining", "events_ready_frame": True}

        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(board_relay, "_build_client", lambda args: DrainingClient())

    outcome = board_relay._connect(
        "args", None, [{"id": "t1"}], allow_relay_unavailable=True
    )

    assert isinstance(outcome, board_relay._Disconnected)
    assert outcome.prev == [{"id": "t1"}]
    assert closed["n"] == 1


def test_reconnect_loop_rebuilds_a_fresh_client_each_attempt(monkeypatch):
    """Every reconnect attempt re-resolves the endpoint via a fresh client,
    never retrying a stale one -- and exhausting the bounded retry cap
    settles into the ordinary poll loop."""
    monkeypatch.setattr(board_relay, "MAX_RECONNECT_ATTEMPTS", 3)
    monkeypatch.setattr(board_relay.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(board_cli, "_fetch_rows", lambda args: [{"id": "poll-tick"}])

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

    import io

    rc = board_relay._reconnect_loop(
        "args", io.StringIO(), [{"id": "seed"}], interval=2.0
    )

    assert rc == 0
    assert build_calls["n"] == 3  # once per bounded attempt, never reused
    assert poll_calls["interval"] == 2.0


def test_reconnect_loop_polls_at_interval_cadence_during_backoff(monkeypatch):
    """The fallback poll tick must keep running on its own ``interval``
    cadence throughout the *entire* backoff wait between reconnect
    attempts, not just once per attempt -- a configured 2s subscription
    must not silently degrade to the (much longer) backoff cadence."""
    fake_clock = {"t": 0.0}

    def fake_monotonic():
        return fake_clock["t"]

    def fake_sleep(secs):
        fake_clock["t"] += secs

    monkeypatch.setattr(board_relay.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(board_relay.time, "sleep", fake_sleep)
    monkeypatch.setattr(board_relay, "MAX_RECONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(board_relay, "INITIAL_RECONNECT_BACKOFF_SECONDS", 10.0)

    poll_calls = {"n": 0}

    def fake_fetch_rows(args):
        poll_calls["n"] += 1
        return [{"id": "t1", "v": poll_calls["n"]}]

    monkeypatch.setattr(board_cli, "_fetch_rows", fake_fetch_rows)

    def fail_build_client(args):
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(board_relay, "_build_client", fail_build_client)
    monkeypatch.setattr(board_cli, "poll_loop", lambda args, out, prev, interval: 0)

    import io

    board_relay._reconnect_loop(
        "args", io.StringIO(), [{"id": "t1", "v": 0}], interval=2.0
    )

    # A 10s backoff at a 2s poll interval should produce ~5 poll ticks
    # during the wait (plus the one correctness-first tick before it) --
    # not 1, which is what blocking for the whole backoff in one sleep
    # would give.
    assert poll_calls["n"] >= 5


def test_reconnect_loop_polls_immediately_after_the_final_backoff_sleep(
    monkeypatch,
):
    """The backoff wait's final sleep -- the one that reaches the deadline
    exactly -- must still be followed by a poll tick before the loop moves
    on to the next connection attempt. Skipping that last tick left up to
    an extra full `interval` gap at every reconnect-attempt boundary (this
    wait's own exit, plus `_establish_with_fallback_polling`'s own first
    tick not landing until its first `interval` elapses)."""
    fake_clock = {"t": 0.0}

    def fake_monotonic():
        return fake_clock["t"]

    def fake_sleep(secs):
        fake_clock["t"] += secs

    monkeypatch.setattr(board_relay.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(board_relay.time, "sleep", fake_sleep)
    monkeypatch.setattr(board_relay, "MAX_RECONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(board_relay, "INITIAL_RECONNECT_BACKOFF_SECONDS", 10.0)

    poll_calls = {"n": 0}

    def fake_fetch_rows(args):
        poll_calls["n"] += 1
        return [{"id": "t1", "v": poll_calls["n"]}]

    monkeypatch.setattr(board_cli, "_fetch_rows", fake_fetch_rows)

    def fail_build_client(args):
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr(board_relay, "_build_client", fail_build_client)
    monkeypatch.setattr(board_cli, "poll_loop", lambda args, out, prev, interval: 0)

    import io

    board_relay._reconnect_loop(
        "args", io.StringIO(), [{"id": "t1", "v": 0}], interval=2.0
    )

    # 1 correctness-first tick + 5 ticks during the 10s/2s backoff wait,
    # including the final tick right as the deadline is reached -- 6 total,
    # not 5 (the exact bug this guards against: skipping that last tick).
    assert poll_calls["n"] == 6


def test_reconnect_loop_successful_handoff_returns_connected_without_recursing(monkeypatch):
    """A successful reconnect returns a `_Connected` (carrying the promoted
    snapshot as `prev`) for the top-level iterative driver to resume the
    event loop with -- `_reconnect_loop` itself never calls `_event_loop`
    directly. The fallback poller still must have stopped (no further poll
    ticks) before the promotion reconcile runs, and never publish anything
    after it -- this test asserts the actual call sequence, not just that
    a `_Connected` was eventually returned."""
    monkeypatch.setattr(board_relay.time, "sleep", lambda _secs: None)

    order: list[str] = []

    def fake_fetch_rows(args):
        order.append("poll_tick")
        return [{"id": "poll", "v": 1}]

    monkeypatch.setattr(board_cli, "_fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(board_relay.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(board_relay, "INITIAL_RECONNECT_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "_build_client", lambda args: _FakeClient())
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
        def __init__(self, args, endpoint=None):
            pass

        def full_refetch(self):
            order.append("promotion")
            return [{"id": "promoted", "v": 1}]

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)

    import io

    result = board_relay._reconnect_loop(
        "args", io.StringIO(), [{"id": "seed", "v": 0}], interval=2.0
    )

    assert isinstance(result, board_relay._Connected)
    assert isinstance(result.reader, FakeReader)
    assert isinstance(result.snapshot, FakeSnapshot)
    # Exactly one poll tick (the correctness-first attempt before the
    # reconnect succeeds), then the promotion reconcile -- never a poll tick
    # after promotion.
    assert order == ["poll_tick", "promotion"]
    assert result.prev == [{"id": "promoted", "v": 1}]


def test_connect_waits_for_ready_before_the_startup_reconcile(monkeypatch):
    """The startup reconcile fetch must not run until the ready frame has
    actually arrived -- running it earlier (or treating `stream_events()`
    merely returning as proof of a live subscription) would race a mutation
    landing in the registration gap. This asserts the actual order: ready
    frame observed, *then* the reconcile fetch."""
    order: list[str] = []

    monkeypatch.setattr(board_relay, "_build_client", lambda args: _FakeClient())
    monkeypatch.setattr(
        board_relay, "_daemon_supports_ready_frame", lambda client: True
    )

    class FakeReader:
        def __init__(self, client):
            order.append("reader_started")
            self.queue: queue.Queue = queue.Queue()
            self.queue.put(("ready", None))

        def start(self):
            pass

    monkeypatch.setattr(board_relay, "_Reader", FakeReader)

    class FakeSnapshot:
        def __init__(self, args, endpoint=None):
            pass

        def full_refetch(self):
            order.append("reconcile_fetch")
            return [{"id": "t1", "v": 1}]

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)

    import io

    outcome = board_relay._connect(
        "args", io.StringIO(), [], allow_relay_unavailable=True
    )

    assert isinstance(outcome, board_relay._Connected)
    assert order == ["reader_started", "reconcile_fetch"]


def test_connect_emits_startup_reconcile_diff_against_prev(monkeypatch):
    """The startup reconcile's result must be diffed against the caller's
    `prev` (``initial_rows``) and the diff actually emitted -- not silently
    substituted as the new baseline with nothing emitted."""
    monkeypatch.setattr(board_relay, "_build_client", lambda args: _FakeClient())
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
        def __init__(self, args, endpoint=None):
            pass

        def full_refetch(self):
            return [{"id": "t1", "v": 2}]  # t1 changed from v=1

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)

    emitted = []
    monkeypatch.setattr(
        board_cli, "_emit_frame", lambda obj, out: emitted.append(obj) or True
    )

    outcome = board_relay._connect(
        "args", None, [{"id": "t1", "v": 1}], allow_relay_unavailable=True
    )

    assert isinstance(outcome, board_relay._Connected)
    assert outcome.prev == [{"id": "t1", "v": 2}]
    assert emitted == [{"type": "delta", "entry": {"id": "t1", "v": 2}}]


def test_connect_treats_priming_failure_as_disconnected_not_empty(monkeypatch):
    """A startup reconcile fetch that fails must never substitute an empty
    snapshot as the new baseline (which would make the next recompute tick
    emit every row in `prev` as `removed`) -- it's a transient failure,
    handled like any other disconnected attempt."""
    monkeypatch.setattr(board_relay, "_build_client", lambda args: _FakeClient())
    monkeypatch.setattr(
        board_relay, "_daemon_supports_ready_frame", lambda client: True
    )

    closed = {"n": 0}

    class FakeClient:
        base_url = "http://fake-coordinator"

        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(board_relay, "_build_client", lambda args: FakeClient())

    class FakeReader:
        def __init__(self, client):
            self.queue: queue.Queue = queue.Queue()
            self.queue.put(("ready", None))

        def start(self):
            pass

    monkeypatch.setattr(board_relay, "_Reader", FakeReader)

    class FakeSnapshot:
        def __init__(self, args, endpoint=None):
            pass

        def full_refetch(self):
            return None  # priming failed

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)

    outcome = board_relay._connect(
        "args", None, [{"id": "t1", "v": 1}], allow_relay_unavailable=True
    )

    assert isinstance(outcome, board_relay._Disconnected)
    assert outcome.prev == [{"id": "t1", "v": 1}]
    assert closed["n"] == 1


def test_connect_folds_mid_reconnect_unavailable_into_disconnected(monkeypatch):
    """A daemon that stops advertising ready-frame support *after* the relay
    is already running (mid-reconnect, `allow_relay_unavailable=False`) must
    never raise `RelayUnavailable` -- frames have already been emitted, so
    the caller's "fall back to the stale initial snapshot" contract no
    longer applies. It's just another connection failure."""
    closed = {"n": 0}

    class FakeClient:
        def health(self):
            return {"status": "ok"}  # no events_ready_frame key

        def close(self):
            closed["n"] += 1

    monkeypatch.setattr(board_relay, "_build_client", lambda args: FakeClient())

    outcome = board_relay._connect(
        "args", None, [{"id": "t1"}], allow_relay_unavailable=False
    )

    assert isinstance(outcome, board_relay._Disconnected)
    assert outcome.prev == [{"id": "t1"}]
    assert closed["n"] == 1


def test_many_reconnect_cycles_never_recurse(monkeypatch):
    """The iterative driver (`run_relay` -> `_drive_from_connected`/
    `_drive_from_reconnect`) must handle an arbitrarily long sequence of
    disconnect/reconnect cycles without growing the Python call stack --
    proven here by running far more cycles than the default recursion limit
    would tolerate if each one were a nested call."""
    import sys

    cycles = sys.getrecursionlimit() * 3
    state = {"n": 0}

    monkeypatch.setattr(board_relay, "INITIAL_RECONNECT_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "MAX_RECONNECT_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(board_cli, "_fetch_rows", lambda args: [])
    monkeypatch.setattr(board_relay, "_build_client", lambda args: _FakeClient())
    monkeypatch.setattr(
        board_relay, "_daemon_supports_ready_frame", lambda client: True
    )

    class FakeReader:
        def __init__(self, client):
            self.queue: queue.Queue = queue.Queue()
            self.queue.put(("ready", None))
            # Immediately disconnect right after the one reconcile fetch
            # runs, so `_event_loop` exits on its very first iteration.
            self.queue.put(("disconnected", None))

        def start(self):
            pass

    monkeypatch.setattr(board_relay, "_Reader", FakeReader)

    class FakeSnapshot:
        def __init__(self, args, endpoint=None):
            pass

        def full_refetch(self):
            state["n"] += 1
            return [{"id": "t1", "v": state["n"]}]

        def recompute_only(self):
            return [{"id": "t1", "v": state["n"]}]

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)
    monkeypatch.setattr(board_relay.time, "sleep", lambda _secs: None)

    def fake_emit_frame(obj, out):
        return state["n"] < cycles  # stop once we've proven enough cycles

    monkeypatch.setattr(board_cli, "_emit_frame", fake_emit_frame)

    import io

    rc = board_relay.run_relay(
        "args", io.StringIO(), initial_rows=[], interval=0.0
    )

    assert rc == 0
    assert state["n"] >= cycles


def test_reader_queue_coalesces_a_burst_of_events_into_one_pending_wake():
    """``_CoalescingEventQueue`` must never grow unboundedly under sustained
    event traffic: many wake-only ``put_event()`` calls while nothing has
    drained the queue yet collapse into exactly one queued ``("event",
    None)`` item, not one per call."""
    q = board_relay._CoalescingEventQueue()
    for _ in range(500):
        q.put_event()

    assert q.get(timeout=0.01) == ("event", None)
    with pytest.raises(queue.Empty):
        q.get(timeout=0.01)

    # Once drained, a fresh wake is signaled again (not permanently
    # coalesced away).
    q.put_event()
    assert q.get(timeout=0.01) == ("event", None)


def test_reader_queue_never_coalesces_or_drops_control_items():
    """``ready``/``disconnected`` control items are never coalesced with
    each other or with a pending event wake -- every call to
    ``put_control`` is unconditionally enqueued and delivered in order."""
    q = board_relay._CoalescingEventQueue()
    q.put_event()
    q.put_control("ready", None)
    q.put_event()  # coalesced: an event wake is already pending
    q.put_control("disconnected", None)

    assert q.get(timeout=0.01) == ("event", None)
    assert q.get(timeout=0.01) == ("ready", None)
    assert q.get(timeout=0.01) == ("disconnected", None)
    with pytest.raises(queue.Empty):
        q.get(timeout=0.01)


def test_reader_queue_never_loses_a_wakeup_under_concurrent_racing(monkeypatch):
    """Regression for a lost-wakeup race: in an earlier version of
    `_CoalescingEventQueue`, popping an event marker off the queue and
    clearing its pending flag were two separate locked steps -- a
    producer's `put_event()` landing in the gap between them could observe
    the (still-true) flag and silently discard its own wake, even though
    the marker it thought it was coalescing into had already been popped
    by the consumer, leaving that mutation invisible until the next long
    reconcile. This stress-tests many concurrent `put_event()` callers
    racing a draining consumer and proves no permanent wedge results: after
    the race settles, one more `put_event()` must still be observable via
    `get()`, and real progress (consumed events) must have happened
    throughout, not stalled at zero."""
    q = board_relay._CoalescingEventQueue()
    stop = threading.Event()
    consumed = {"events": 0}

    def consumer() -> None:
        while not stop.is_set():
            try:
                kind, _payload = q.get(timeout=0.001)
            except queue.Empty:
                continue
            if kind == "event":
                consumed["events"] += 1

    def producer() -> None:
        while not stop.is_set():
            q.put_event()

    consumer_thread = threading.Thread(target=consumer, daemon=True)
    producer_threads = [
        threading.Thread(target=producer, daemon=True) for _ in range(4)
    ]
    consumer_thread.start()
    for t in producer_threads:
        t.start()

    time.sleep(0.3)
    stop.set()
    consumer_thread.join(timeout=5)
    for t in producer_threads:
        t.join(timeout=5)

    # The queue must remain consistent and usable after the race settles --
    # a fresh wake must still be observable (never permanently wedged with
    # `_event_pending` stuck true/false out of sync with reality).
    q.put_event()
    assert q.get(timeout=1.0) == ("event", None)
    # Real progress must have happened throughout the race, not stalled.
    assert consumed["events"] > 0


def test_run_relay_returns_0_on_keyboard_interrupt_during_initial_connect(
    monkeypatch,
):
    """A `KeyboardInterrupt` landing during the very first connection
    attempt (before `_drive`'s own loop, and before `_event_loop`'s own
    internal handler could ever see it) must still return 0 cleanly, like
    `board_cli.poll_loop` does -- not propagate uncaught."""

    def raise_keyboard_interrupt(args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(board_relay, "_build_client", raise_keyboard_interrupt)

    import io

    rc = board_relay.run_relay(
        "args", io.StringIO(), initial_rows=[], interval=2.0
    )

    assert rc == 0


def test_event_loop_retries_a_failed_event_woken_refetch(monkeypatch):
    """A transient `/tasks` failure on an event-woken re-fetch must not
    silently drop the wake until the next (up to 45s later) long
    reconcile -- the loop must retry on its own (rate-limited), and once
    the retry succeeds, actually emit the diff."""
    monkeypatch.setattr(board_relay, "DEBOUNCE_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(board_relay, "MIN_REFETCH_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(board_relay, "RECOMPUTE_INTERVAL_SECONDS", 1000.0)
    monkeypatch.setattr(board_relay, "LONG_RECONCILE_SECONDS", 1000.0)

    reader = _reader_with(("event", {"type": "task.progress"}))
    calls = {"full": 0}

    class FakeSnapshot:
        def full_refetch(self):
            calls["full"] += 1
            if calls["full"] == 1:
                return None  # transient failure on the event-woken fetch
            return [{"id": "t1", "v": calls["full"]}]

        def recompute_only(self):
            raise AssertionError("recompute_only must not run for this test")

    stop = _StopAfter(limit=1)
    monkeypatch.setattr(board_cli, "_emit_frame", stop)

    rc = board_relay._event_loop(
        None, None, reader, FakeSnapshot(), [], interval=0.01
    )

    assert rc == 0
    assert calls["full"] == 2  # the failed fetch, then exactly one retry
    assert stop.emitted and stop.emitted[0]["type"] == "delta"


def test_connect_binds_snapshot_reads_to_the_connected_endpoint(monkeypatch):
    """`_Snapshot` must be constructed against the exact coordinator base
    URL the connected client resolved (`client.base_url`), never an
    independent `_endpoint()` re-resolution -- otherwise a cutover landing
    between the SSE connection and the snapshot's own fetches could read a
    different coordinator generation than the one publishing events."""
    seen_endpoints = []

    class FakeClient:
        base_url = "http://pinned-endpoint:1234"

        def close(self):
            pass

    monkeypatch.setattr(board_relay, "_build_client", lambda args: FakeClient())
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
        def __init__(self, args, endpoint=None):
            seen_endpoints.append(endpoint)

        def full_refetch(self):
            return []

    monkeypatch.setattr(board_relay, "_Snapshot", FakeSnapshot)

    import io

    outcome = board_relay._connect(
        "args", io.StringIO(), [], allow_relay_unavailable=True
    )

    assert isinstance(outcome, board_relay._Connected)
    assert seen_endpoints == ["http://pinned-endpoint:1234"]


def test_snapshot_full_refetch_pins_endpoint_to_the_connected_coordinator(
    monkeypatch,
):
    """`_Snapshot.full_refetch()` must pass its bound ``endpoint`` through
    to both `_fetch_raw_tasks_direct` and `_relay_fetch_many` -- never let
    either re-resolve `active.json` independently."""
    seen = {"tasks_endpoint": None, "relay_endpoint": None}

    def fake_fetch_raw_tasks_direct(args, *, endpoint=None):
        seen["tasks_endpoint"] = endpoint
        return [{"id": "t1", "repo": "r", "worktree_id": "w"}]

    def fake_relay_fetch_many(refs, *, endpoint=None):
        seen["relay_endpoint"] = endpoint
        return {}

    monkeypatch.setattr(
        board_cli, "_fetch_raw_tasks_direct", fake_fetch_raw_tasks_direct
    )
    monkeypatch.setattr(board_cli, "_relay_fetch_many", fake_relay_fetch_many)

    args = types.SimpleNamespace(machine="m1", recent_mins=60)
    snapshot = board_relay._Snapshot(args, "http://pinned-endpoint:5678")
    rows = snapshot.full_refetch()

    assert rows is not None
    assert seen["tasks_endpoint"] == "http://pinned-endpoint:5678"
    # relay_fetch_many is only invoked if _build actually needs a relay
    # lookup for this task's group; a minimal task dict with no
    # worktree-status-relevant fields may not trigger one, so only assert
    # the endpoint when it was actually called.
    if seen["relay_endpoint"] is not None:
        assert seen["relay_endpoint"] == "http://pinned-endpoint:5678"


def test_snapshot_full_refetch_preserves_relay_cache_on_transient_failure(
    monkeypatch,
):
    """`board_cli._build()` itself catches any `relay_fetch_many` failure
    and silently falls back to an empty relay cache for that pass -- so a
    transient `/worktree-status-relays` blip never raises up to
    `full_refetch()` at all. Without its own detection, `full_refetch()`
    would report success and wipe every row's cached worktree-relay status
    instead of preserving it. This proves a fetch failure on a *second*
    call falls back to the first call's successfully-cached relay entry,
    rather than showing it as unknown."""
    call = {"n": 0}

    def fake_fetch_raw_tasks_direct(args, *, endpoint=None):
        return [{"id": "t1", "repo": "r", "owner": "m1/wt1", "status": "started"}]

    def fake_relay_fetch_many(refs, *, endpoint=None):
        call["n"] += 1
        if call["n"] == 1:
            return {ref: {"turn_state": "active"} for ref in refs}
        raise RuntimeError("relay endpoint unreachable")

    monkeypatch.setattr(
        board_cli, "_fetch_raw_tasks_direct", fake_fetch_raw_tasks_direct
    )
    monkeypatch.setattr(board_cli, "_relay_fetch_many", fake_relay_fetch_many)

    args = types.SimpleNamespace(machine="m1", recent_mins=60)
    snapshot = board_relay._Snapshot(args, "http://pinned-endpoint:5678")

    first_rows = snapshot.full_refetch()
    assert first_rows is not None
    assert snapshot.relay_cache == {("r", "wt1"): {"turn_state": "active"}}

    second_rows = snapshot.full_refetch()

    assert second_rows is not None  # a relay-only failure is not fatal
    assert call["n"] == 2
    # The previously-cached entry must survive the second (failed) fetch --
    # never silently replaced with an empty cache.
    assert snapshot.relay_cache == {("r", "wt1"): {"turn_state": "active"}}


def test_establish_with_fallback_polling_keeps_polling_during_the_handshake(
    monkeypatch,
):
    """The fallback poller must keep ticking on `interval`'s own cadence for
    the *entire* duration of `_establish`'s blocking handshake, not just
    during the backoff wait between attempts."""
    handshake_entered = threading.Event()
    release_handshake = threading.Event()
    poll_calls = {"n": 0}

    def fake_establish(args, *, allow_relay_unavailable):
        handshake_entered.set()
        assert release_handshake.wait(timeout=5), "poller never ran during handshake"
        return board_relay._Established(client=_FakeClient(), reader=None)

    monkeypatch.setattr(board_relay, "_establish", fake_establish)

    def poll_tick(prev):
        poll_calls["n"] += 1
        if poll_calls["n"] >= 3:
            release_handshake.set()
        return True, prev

    outcome, prev = board_relay._establish_with_fallback_polling(
        "args", None, [], 0.01, poll_tick=poll_tick
    )

    assert isinstance(outcome, board_relay._Established)
    assert poll_calls["n"] >= 3


def test_establish_with_fallback_polling_stops_when_poll_tick_reports_closed_pipe(
    monkeypatch,
):
    """If the fallback poller's own pipe closes while still waiting on the
    handshake, the helper must return 0 immediately rather than waiting for
    the (now-pointless) background connect attempt to finish."""
    release_handshake = threading.Event()

    def fake_establish(args, *, allow_relay_unavailable):
        assert release_handshake.wait(timeout=5)
        return board_relay._ConnectFailed()

    monkeypatch.setattr(board_relay, "_establish", fake_establish)

    def poll_tick(prev):
        release_handshake.set()
        return False, prev  # simulated closed pipe

    outcome, _prev = board_relay._establish_with_fallback_polling(
        "args", None, [], 0.01, poll_tick=poll_tick
    )

    assert outcome == 0
