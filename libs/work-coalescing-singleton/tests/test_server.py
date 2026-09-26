"""Unit tests for CoalescingServer: request coalescing, ref-count/linger, reap."""

from __future__ import annotations

import threading
import time

import pytest

from work_coalescing_singleton.server import _CLOSE_SERVE_WAIT_S, CoalescingServer, Unavailable


def _make_server(**kwargs) -> CoalescingServer:
    kwargs.setdefault("linger_seconds", 0.15)
    kwargs.setdefault("subscriber_ttl", 0.2)
    kwargs.setdefault("reap_interval", 0.05)
    return CoalescingServer(lambda kind, payload: {"kind": kind, **payload}, **kwargs)


def test_identical_key_coalesces_into_one_execution():
    calls = []
    gate = threading.Event()

    def compute(kind, payload):
        calls.append(payload["n"])
        gate.wait(timeout=2)
        return {"echo": payload["n"]}

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        results = {}

        def owner():
            results["owner"] = server.handle_request("k", "same-key", {"n": 1}, time.time() + 5)

        t = threading.Thread(target=owner)
        t.start()
        time.sleep(0.1)  # let the owner grab the in-flight slot before the joiner arrives

        joiner_result = None

        def joiner():
            nonlocal joiner_result
            joiner_result = server.handle_request("k", "same-key", {"n": 2}, time.time() + 5)

        j = threading.Thread(target=joiner)
        j.start()
        time.sleep(0.1)
        gate.set()
        t.join(timeout=2)
        j.join(timeout=2)

        assert calls == [1]  # only the owner's payload was ever computed
        assert results["owner"] == {"echo": 1}
        assert joiner_result == {"echo": 1}  # the joiner got the owner's result, not its own
    finally:
        server.close()


def test_distinct_keys_never_coalesced():
    calls = []

    def compute(kind, payload):
        calls.append(payload["n"])
        return {"n": payload["n"]}

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        r1 = server.handle_request("k", "key-1", {"n": 1}, time.time() + 5)
        r2 = server.handle_request("k", "key-2", {"n": 2}, time.time() + 5)
        assert sorted(calls) == [1, 2]
        assert r1 == {"n": 1}
        assert r2 == {"n": 2}
    finally:
        server.close()


def test_expired_deadline_raises_unavailable_without_blocking_owner():
    gate = threading.Event()

    def compute(kind, payload):
        gate.wait(timeout=2)
        return {"ok": True}

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        owner_result = {}

        def owner():
            owner_result["r"] = server.handle_request("k", "slow", {}, time.time() + 5)

        t = threading.Thread(target=owner)
        t.start()
        time.sleep(0.1)

        with pytest.raises(Unavailable):
            server.handle_request("k", "slow", {}, time.time() + 0.05)

        gate.set()
        t.join(timeout=2)
        assert owner_result["r"] == {"ok": True}
    finally:
        server.close()


def test_compute_error_is_raised_to_every_joiner():
    gate = threading.Event()

    def compute(kind, payload):
        gate.wait(timeout=2)
        raise RuntimeError("boom")

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        owner_exc = {}

        def owner():
            try:
                server.handle_request("k", "err", {}, time.time() + 5)
            except RuntimeError as exc:
                owner_exc["e"] = exc

        t = threading.Thread(target=owner)
        t.start()
        time.sleep(0.1)

        joiner_exc = {}

        def joiner():
            try:
                server.handle_request("k", "err", {}, time.time() + 5)
            except RuntimeError as exc:
                joiner_exc["e"] = exc

        j = threading.Thread(target=joiner)
        j.start()
        time.sleep(0.1)
        gate.set()
        t.join(timeout=2)
        j.join(timeout=2)

        assert str(owner_exc["e"]) == "boom"
        assert str(joiner_exc["e"]) == "boom"
    finally:
        server.close()


def test_refcount_linger_fires_only_after_last_release():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set)
    try:
        server.subscribe("a")
        server.subscribe("b")
        server.release("a")
        assert not idle.wait(timeout=0.3)  # "b" still subscribed -- must not go idle
        assert server.subscriber_count() == 1

        server.release("b")
        assert idle.wait(timeout=1.0)  # linger expired with zero subscribers
    finally:
        server.close()


def test_new_subscribe_cancels_pending_linger():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set, linger_seconds=0.2)
    try:
        server.subscribe("a")
        server.release("a")
        time.sleep(0.05)  # linger started but not yet expired
        server.subscribe("b")  # must cancel it
        assert not idle.wait(timeout=0.35)
        assert server.subscriber_count() == 1
    finally:
        server.close()


def test_liveness_reap_drops_stale_subscriber_and_goes_idle():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set, subscriber_ttl=0.1, reap_interval=0.05)
    server.start()  # the reap loop only runs once started
    try:
        server.subscribe("crashed-client")
        # No release ever sent -- simulates a crash. The reaper must drop it
        # on its own and then start (and let expire) the linger.
        assert idle.wait(timeout=2.0)
        assert server.subscriber_count() == 0
    finally:
        server.close()


def test_touch_keeps_a_fire_and_forget_caller_alive():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set, subscriber_ttl=0.3, reap_interval=0.05)
    server.start()  # the reap loop only runs once started
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            server.touch("poller")
            time.sleep(0.05)
        assert not idle.is_set()  # kept alive throughout by repeated touch()
    finally:
        server.close()


def test_close_after_a_failed_serve_thread_launch_never_blocks():
    """Copilot review finding: the previous single ``_started`` flag was
    set to ``True`` *before* ``self._serve_thread.start()`` ran, so a
    ``.start()`` failure (e.g. OS thread-creation exhaustion) left
    ``_started=True`` even though ``serve_forever()`` was never entered.
    ``close()``'s ``self._server.shutdown()`` then blocks forever waiting
    for a ``serve_forever()`` loop that will never notice the shutdown
    request, per ``socketserver``'s own documented behavior. ``close()``
    must return promptly even when the serve thread's own launch failed."""
    server = _make_server()
    real_start = threading.Thread.start

    def _boom(self):
        if self.name == "work-coalescing-singleton":
            raise RuntimeError("simulated thread-creation failure")
        return real_start(self)

    threading.Thread.start = _boom
    try:
        with pytest.raises(RuntimeError):
            server.start()
    finally:
        threading.Thread.start = real_start
    assert server._serve_started is False
    assert server._reap_started is False
    # The regression itself: this must return promptly, never hang.
    close_thread = threading.Thread(target=server.close)
    close_thread.start()
    close_thread.join(timeout=3)
    assert not close_thread.is_alive(), "close() hung waiting on a never-started server"


def test_close_after_serve_thread_dies_before_confirming_running_never_blocks():
    """Copilot review finding: the sibling test above only exercises
    ``Thread.start()`` itself raising, which leaves ``_serve_started``
    ``False`` -- ``close()`` then short-circuits the ``_serve_started and
    ...`` check and never even reaches ``_serve_running.wait()``. That
    does not cover the actual partial-start failure this class's own
    comments describe: ``Thread.start()`` succeeds (the OS thread object
    is created and ``_serve_started`` is set ``True``), but the thread's
    target callable (``serve_forever``) raises before ever calling
    ``service_actions()`` -- so ``_serve_running`` is never set. ``close()``
    must still return within its bounded wait (``_CLOSE_SERVE_WAIT_S``),
    never hang waiting on a loop that will never confirm running."""
    server = _make_server()

    def _dies_immediately(poll_interval):
        raise RuntimeError("simulated early serve_forever failure")

    # `self._serve_thread` was constructed in `__init__` with
    # `target=self._server.serve_forever` already bound -- reassigning
    # `server._server.serve_forever` afterwards would not affect that
    # already-captured target, so the substitute must replace the thread's
    # own `_target` instead for the injected failure to actually run.
    server._serve_thread._target = _dies_immediately
    server.start()
    assert server._serve_started is True
    assert server._reap_started is True

    start_time = time.time()
    close_thread = threading.Thread(target=server.close)
    close_thread.start()
    close_thread.join(timeout=_CLOSE_SERVE_WAIT_S + 3)
    elapsed = time.time() - start_time
    assert not close_thread.is_alive(), "close() hung waiting on a serve loop that never confirmed running"
    assert elapsed < _CLOSE_SERVE_WAIT_S + 2, "close() should not wait meaningfully longer than its own bound"


def test_close_never_calls_shutdown_on_a_thread_that_died_after_confirming_running():
    """Copilot review finding: ``_serve_running`` is a one-way readiness
    event -- once ``service_actions()`` sets it, it stays set even if the
    serve thread has since died on its own. If ``close()`` trusted that
    stale readiness signal alone, it would call ``self._server.shutdown()``
    against a loop that no longer exists. Gate ``shutdown()`` on the thread
    still being alive too, not merely on having once confirmed running."""
    server = _make_server()
    shutdown_calls: list[None] = []
    real_shutdown = server._server.shutdown
    server._server.shutdown = lambda: shutdown_calls.append(None) or real_shutdown()

    def _confirms_running_then_dies(poll_interval):
        server._serve_running.set()

    # See the sibling test above for why the thread's own `_target` (not
    # `server._server.serve_forever`) must be replaced.
    server._serve_thread._target = _confirms_running_then_dies
    server.start()
    assert server._serve_started is True
    server._serve_thread.join(timeout=2)
    assert not server._serve_thread.is_alive()  # died on its own, post-readiness

    close_thread = threading.Thread(target=server.close)
    close_thread.start()
    close_thread.join(timeout=3)
    assert not close_thread.is_alive(), "close() hung on a thread that had already died"
    assert shutdown_calls == [], "close() must not call shutdown() against an already-dead serve loop"


def test_close_after_a_failed_reap_thread_launch_still_stops_the_serve_loop():
    """The other half of the same finding: if the serve thread starts fine
    but the *reap* thread's own launch fails, close() must still properly
    shut down and join the serve loop that IS actually running (not treat
    the whole start() as a no-op just because one of the two threads
    failed)."""
    server = _make_server()
    real_start = threading.Thread.start

    def _boom(self):
        if self.name == "work-coalescing-singleton-reaper":
            raise RuntimeError("simulated thread-creation failure")
        return real_start(self)

    threading.Thread.start = _boom
    try:
        with pytest.raises(RuntimeError):
            server.start()
    finally:
        threading.Thread.start = real_start
    assert server._serve_started is True
    assert server._reap_started is False
    close_thread = threading.Thread(target=server.close)
    close_thread.start()
    close_thread.join(timeout=3)
    assert not close_thread.is_alive(), "close() hung despite a real running serve loop"
    assert not server._serve_thread.is_alive()  # actually shut down, not leaked


def test_start_itself_closes_a_partially_started_server_before_reraising():
    """Copilot review finding: if the reap thread's launch fails after the
    serve thread already started, the server would otherwise be left
    accepting connections with no owner -- several existing consumers (e.g.
    the resident status-monitor's classify/hook server startup) wrap
    ``start()`` in a bare ``try/except`` and discard the reference on
    failure, which would leak that accepting daemon forever. ``start()``
    must close itself before re-raising, so the server is already fully
    torn down by the time the caller's ``except`` block even runs -- no
    caller-side cleanup required."""
    server = _make_server()
    real_start = threading.Thread.start

    def _boom(self):
        if self.name == "work-coalescing-singleton-reaper":
            raise RuntimeError("simulated thread-creation failure")
        return real_start(self)

    threading.Thread.start = _boom
    try:
        with pytest.raises(RuntimeError):
            server.start()
    finally:
        threading.Thread.start = real_start

    # No explicit close() call here at all -- start() must have already
    # torn everything down on its own.
    assert server._closed is True
    assert not server._serve_thread.is_alive()


def test_close_racing_start_between_thread_launch_and_flag_assignment_is_serialized():
    """Copilot review finding: ``close()`` could previously run entirely
    between ``self._serve_thread.start()`` succeeding and the following
    ``self._serve_started = True`` -- observing ``_serve_started`` still
    ``False``, skipping shutdown/join, and closing the socket -- before
    ``start()`` resumed, set ``_serve_started = True``, launched the
    reaper, and marked ``_started = True``. That left a closed server with
    inconsistent lifecycle flags. ``_lifecycle_lock`` must serialize the
    two methods so ``close()`` only ever observes the fully-pre-start or
    fully-post-start state, never a state in between."""
    server = _make_server()
    real_start = threading.Thread.start
    entered_gap = threading.Event()
    release_gap = threading.Event()

    def _gated_start(self):
        result = real_start(self)
        if self.name == "work-coalescing-singleton":
            # Simulate close() racing exactly in the gap between the serve
            # thread's own successful .start() and start()'s following
            # `_serve_started = True` assignment.
            entered_gap.set()
            release_gap.wait(timeout=5)
        return result

    threading.Thread.start = _gated_start
    try:
        start_thread = threading.Thread(target=server.start)
        start_thread.start()
        assert entered_gap.wait(timeout=2)

        # close() must now block on _lifecycle_lock rather than running to
        # completion mid-gap.
        close_thread = threading.Thread(target=server.close)
        close_thread.start()
        close_thread.join(timeout=0.3)
        assert close_thread.is_alive(), (
            "close() ran through the gap instead of being serialized by _lifecycle_lock"
        )

        release_gap.set()
        start_thread.join(timeout=5)
        close_thread.join(timeout=5)
        assert not close_thread.is_alive()
        assert not start_thread.is_alive()

        # The server must land in a fully-consistent post-start,
        # post-close state -- never the inconsistent partial state the
        # race used to produce.
        assert server._serve_started is True
        assert server._reap_started is True
        assert server._started is True
        assert server._closed is True
        assert not server._serve_thread.is_alive()
        assert not server._reap_thread.is_alive()
    finally:
        threading.Thread.start = real_start
        release_gap.set()


@pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)
def test_close_does_not_call_shutdown_before_serve_forever_is_confirmed_running(monkeypatch):
    """Copilot review finding: ``Thread.start()`` returning successfully
    only proves the OS thread object was created, not that
    ``serve_forever()`` has actually begun executing yet -- and even once
    the thread's target callable is running, ``serve_forever()`` itself
    hasn't necessarily entered its accept loop. ``close()`` must gate
    ``self._server.shutdown()`` on ``_serve_running`` (set only from
    ``_Server.service_actions()``, a hook ``serve_forever()`` itself calls
    on every accept-loop iteration -- the earliest point that genuinely
    proves the loop entered), never on ``_serve_started`` (thread-creation)
    alone -- otherwise a ``close()`` racing an in-flight ``start()`` could
    call ``shutdown()`` before the loop is truly running.

    This test deliberately drives the pathological "loop never confirms
    running" path documented in ``close()``'s own docstring: since
    ``_serve_running`` is never set here, ``close()`` correctly skips
    ``shutdown()`` and never signals the real (still-running)
    ``serve_forever()`` loop to stop. That loop's own next
    ``selector.select()`` against the now-closed socket then raises an
    ``OSError`` and the daemon thread dies noisily but harmlessly --
    exactly the documented fallback for a thread that "does eventually
    run" after ``close()`` gave up waiting. Suppressing the resulting
    ``PytestUnhandledThreadExceptionWarning`` here is deliberate, not a
    swallowed real failure: it is intrinsic to this specific edge case,
    not present in the ordinary start/close path (see the sibling
    ``test_close_shuts_down_promptly_...`` test)."""
    server = _make_server()
    gate = threading.Event()
    real_service_actions = server._server.service_actions

    def _delayed_service_actions():
        gate.wait(timeout=5)  # simulates a real scheduling delay
        if server._closed:
            return
        real_service_actions()

    monkeypatch.setattr(server._server, "service_actions", _delayed_service_actions)
    server._serve_thread.start()
    server._serve_started = True
    assert not server._serve_running.is_set()

    try:
        close_thread = threading.Thread(target=server.close)
        close_thread.start()
        close_thread.join(timeout=_CLOSE_SERVE_WAIT_S + 3)
        assert not close_thread.is_alive(), (
            "close() blocked on shutdown() despite serve_forever() never confirmed running"
        )
    finally:
        gate.set()  # release the delayed thread so it can finish cleanly
        # Bound how long the now-orphaned real serve thread (see this
        # test's own docstring) lingers before this test function returns,
        # so its resulting exception is attributed here rather than
        # surfacing asynchronously during a later, unrelated test.
        server._serve_thread.join(timeout=1)


def test_close_shuts_down_promptly_once_serve_forever_is_confirmed_running():
    """The other side of the same guard: once ``_serve_running`` IS set (a
    real ``start()``), ``close()`` must still promptly call ``shutdown()``
    and actually stop the loop -- the bounded wait must never itself
    introduce a needless delay on the ordinary, fully-started path."""
    server = _make_server()
    server.start()
    try:
        # service_actions() (which sets this) only runs after
        # serve_forever()'s first selector.select(poll_interval) returns --
        # wait for it rather than asserting instantly.
        assert server._serve_running.wait(timeout=2.0)
    finally:
        started = time.time()
        server.close()
        elapsed = time.time() - started
    assert elapsed < 1.0, "close() took unexpectedly long on the ordinary running path"
    assert not server._serve_thread.is_alive()
