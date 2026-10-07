"""Direct unit coverage for the ``Inbox`` primitive itself.

The existing picker suite exercises ``Inbox`` indirectly, through
``_run_bg``/``_apply_from_worker``/the setup-reload worker. This module
covers ``Inbox`` in isolation: posting, coalescing, draining, the
home-thread immediate-apply shortcut, the ``post()`` boolean wake-success
contract, and the lazy ``ensure_inbox`` helper.
"""
from __future__ import annotations

import logging
import threading
import time

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from worktree_manager.production_picker.picker_tui.inbox import (
    Inbox,
    InboxUpdated,
    ensure_inbox,
)
from worktree_manager.production_picker.picker_tui.engine_runtime import (
    PickerScreenRuntimeMixin,
)
from worktree_manager.production_picker.picker_tui.engine_loading import (
    PickerScreenLoadingMixin,
)


class _RecordingOwner:
    """A fake ``MessagePump`` owner that records every ``post_message`` call
    instead of actually waking a running Textual app.

    Mirrors ``MessagePump.post_message``'s real return contract (``True`` =
    queued, ``False`` = undeliverable) so ``Inbox.post()``'s own handling of
    that return value is exercised the same way it would be in production.
    """

    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(message)
        return True


class _BrokenOwner:
    """An owner whose ``post_message`` always raises, simulating a torn-down
    or otherwise unreachable render flow."""

    def post_message(self, message):
        raise RuntimeError("owner already torn down")


class _ClosingOwner:
    """An owner whose ``post_message`` returns ``False`` without raising --
    ``MessagePump``'s own contract for an already-closing/closed pump."""

    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(message)
        return False


def _inbox_with_foreign_home(owner=None):
    """Construct an ``Inbox`` whose "home thread" is NOT this test's own
    thread -- so posting from the test body exercises the ordinary
    queue-and-wake path, not the home-thread immediate-apply shortcut."""
    owner = owner if owner is not None else _RecordingOwner()
    result = {}

    def _build():
        result["inbox"] = Inbox(owner)

    t = threading.Thread(target=_build)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    return result["inbox"], owner


def _post_from_other_thread(inbox, slot, value):
    """Post from a brand-new thread so the "home thread" shortcut never
    fires -- proving the cross-thread wake path specifically."""
    result = {}

    def _run():
        result["ok"] = inbox.post(slot, value)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "post() from a background thread must not hang"
    return result["ok"]


def test_post_then_drain_round_trips_the_value():
    inbox, owner = _inbox_with_foreign_home()
    inbox.post("status", "hello")
    assert inbox.drain() == {"status": "hello"}
    # Draining clears it -- a second drain with nothing new posted is empty.
    assert inbox.drain() == {}


def test_drain_is_empty_with_nothing_pending():
    inbox = Inbox(_RecordingOwner())
    assert inbox.drain() == {}
    assert inbox.pending_slots() == frozenset()


def test_same_slot_posted_twice_coalesces_to_the_latest_value():
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("progress", 1)
    inbox.post("progress", 2)
    inbox.post("progress", 3)
    assert inbox.drain() == {"progress": 3}


def test_different_slots_are_independent():
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("a", 1)
    inbox.post("b", 2)
    assert inbox.drain() == {"a": 1, "b": 2}


def test_drain_apply_invokes_every_callable_value_exactly_once():
    calls = []
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("one", lambda: calls.append("one"))
    inbox.post("two", lambda: calls.append("two"))
    applied = inbox.drain_apply()
    assert applied == 2
    assert sorted(calls) == ["one", "two"]
    # Already drained -- a second call does nothing and invokes nothing new.
    assert inbox.drain_apply() == 0
    assert sorted(calls) == ["one", "two"]


def test_drain_apply_discards_non_callable_values_without_invoking_them():
    """``drain_apply`` drains (and discards) every slot, not just callable
    ones -- a non-callable value posted alongside closures is silently
    consumed here, never raised, and never returned to the caller."""
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("data", {"not": "callable"})
    inbox.post("closure", lambda: None)
    applied = inbox.drain_apply()
    assert applied == 1
    # Both slots are gone either way -- the non-callable one was discarded.
    assert inbox.pending_slots() == frozenset()
    assert inbox.snapshot() == {}


def test_peek_is_non_destructive():
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("status", "ready")
    assert inbox.peek("status") == "ready"
    # Still there -- peek never drains.
    assert inbox.peek("status") == "ready"
    assert inbox.drain() == {"status": "ready"}


def test_peek_missing_slot_returns_default():
    inbox, _ = _inbox_with_foreign_home()
    assert inbox.peek("absent") is None
    assert inbox.peek("absent", "fallback") == "fallback"


def test_pending_slots_reflects_undrained_posts():
    inbox, _ = _inbox_with_foreign_home()
    assert inbox.pending_slots() == frozenset()
    inbox.post("x", 1)
    inbox.post("y", 2)
    assert inbox.pending_slots() == frozenset({"x", "y"})
    inbox.drain()
    assert inbox.pending_slots() == frozenset()


def test_snapshot_is_a_shallow_copy_independent_of_internal_state():
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("x", 1)
    snap = inbox.snapshot()
    assert snap == {"x": 1}
    snap["x"] = 999
    # Mutating the returned copy must not affect the inbox's own state.
    assert inbox.snapshot() == {"x": 1}


def test_posting_from_the_home_thread_applies_immediately_without_a_wake():
    """The thread that constructs an ``Inbox`` is its "home" thread (normally
    the render/event-loop thread). Posting from that same thread drains and
    applies inline instead of merely queuing a wake -- this is what lets a
    producer that short-circuits inline, or a synchronous unit test with no
    running event loop, still observe its own post take effect immediately.
    """
    owner = _RecordingOwner()
    inbox = Inbox(owner)  # constructed on this (the test's) thread
    calls = []
    ok = inbox.post("apply-me", lambda: calls.append("applied"))
    assert ok is True
    assert calls == ["applied"]
    # Applied inline -- nothing left pending, and no wake was ever posted.
    assert inbox.pending_slots() == frozenset()
    assert owner.messages == []


def test_posting_a_data_value_from_the_home_thread_preserves_it_for_drain():
    """A non-callable (pure data) value posted on the home thread must
    survive for a later ``peek()``/``drain()`` consumer -- the home-thread
    shortcut must NOT route it through ``drain_apply()`` (which only ever
    invokes callables, silently discarding everything else per its own
    documented contract). Without this, the exact same value posted from
    a background thread round-trips correctly, but posted on the home
    thread it would vanish before anyone ever read it."""
    owner = _RecordingOwner()
    inbox = Inbox(owner)  # home thread == this test's own thread
    ok = inbox.post("status", "ready")
    assert ok is True
    assert inbox.peek("status") == "ready"
    assert inbox.drain() == {"status": "ready"}


def test_posting_a_closure_from_the_home_thread_does_not_disturb_an_unrelated_pending_data_slot():
    """Applying a just-posted closure inline on the home thread must only
    ever touch ITS OWN slot -- never drain the whole inbox, which could
    silently discard a different producer's still-pending data value
    (never auto-invoked, but drained and dropped all the same by
    ``drain_apply()``) or claim a different, unrelated closure this call
    has no business taking credit/blame for."""
    owner = _RecordingOwner()
    inbox = Inbox(owner)  # home thread == this test's own thread
    # A different producer's data, not yet drained.
    inbox.post("other-data", "untouched")
    calls = []
    ok = inbox.post("apply-me", lambda: calls.append("applied"))
    assert ok is True
    assert calls == ["applied"]
    # The unrelated data slot must still be there for its own consumer.
    assert inbox.peek("other-data") == "untouched"
    assert inbox.drain() == {"other-data": "untouched"}


def test_home_thread_post_never_invokes_a_stale_closure_a_racing_post_already_replaced():
    """A regression for a lost-update race: the home-thread path releases
    ``_lock`` (after writing the posted value) before claiming and
    invoking it -- a background thread's own ``post()`` to the SAME slot
    can legitimately race in during that window with a NEWER value
    (ordinary coalescing). The home-thread path must detect this and
    never invoke the now-stale closure it captured, nor silently discard
    the replacement value out from under whoever posted it."""
    owner = _RecordingOwner()
    inbox = Inbox(owner)  # home thread == this test's own thread
    real_lock = inbox._lock
    triggered = {"done": False}
    replaced = threading.Event()

    class _RacyLock:
        """Wraps the real lock, but -- only the first time it is released
        -- lets a background thread race in and replace the slot's value
        before the home-thread path's own later claim-check proceeds."""

        def __enter__(self):
            real_lock.acquire()
            return self

        def __exit__(self, *exc_info):
            real_lock.release()
            if not triggered["done"]:
                # Flip the flag BEFORE spawning -- the background post
                # below reuses this same lock for its own (non-home-
                # thread) write, and must not re-trigger this branch.
                triggered["done"] = True
                t = threading.Thread(
                    target=_post_from_other_thread,
                    args=(inbox, "slot", "newer"),
                )
                t.start()
                t.join(timeout=5)
                replaced.set()
            return False

    inbox._lock = _RacyLock()
    calls = []
    ok = inbox.post("slot", lambda: calls.append("stale"))
    assert ok is True
    assert replaced.is_set()
    # The stale closure this call posted was never invoked -- the slot
    # already held a different (newer) value by the time this call tried
    # to claim it.
    assert calls == []
    # The racing background post's own value survives, exactly as a
    # normal coalesced post would -- it was never silently discarded.
    assert inbox.drain() == {"slot": "newer"}


def test_posting_from_a_background_thread_queues_exactly_one_wake():
    owner = _RecordingOwner()
    inbox = Inbox(owner)
    ok = _post_from_other_thread(inbox, "slot", "value")
    assert ok is True
    assert len(owner.messages) == 1
    assert isinstance(owner.messages[0], InboxUpdated)
    # The value is still pending -- nothing applied it automatically; that
    # is the owning screen's job once it handles ``InboxUpdated``.
    assert inbox.drain() == {"slot": "value"}


def test_a_burst_of_cross_thread_posts_only_wakes_once_per_batch():
    """Many posts (even across different slots, even from different
    threads) between two drains must never queue more than one wake -- a
    fast-moving producer must not re-enter the event loop once per post."""
    owner = _RecordingOwner()
    inbox = Inbox(owner)
    threads = []
    for i in range(5):
        t = threading.Thread(target=inbox.post, args=(f"slot-{i}", i))
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    assert len(owner.messages) == 1
    assert inbox.drain() == {f"slot-{i}": i for i in range(5)}
    # After a drain, the next cross-thread post queues a fresh wake again.
    ok = _post_from_other_thread(inbox, "slot-again", "x")
    assert ok is True
    assert len(owner.messages) == 2


def test_post_from_background_thread_returns_false_and_logs_on_wake_failure(caplog):
    inbox = Inbox(_BrokenOwner())
    with caplog.at_level(logging.WARNING, logger="agent-worktrees.picker"):
        ok = _post_from_other_thread(inbox, "slot", "value")
    assert ok is False
    assert any(
        "failed to wake the owning render flow" in r.message
        for r in caplog.records
    )
    # The value itself is never lost even though the wake failed -- it sits
    # in the inbox for whatever next drains it.
    assert inbox.drain() == {"slot": "value"}


def test_post_message_returning_false_without_raising_still_counts_as_a_failed_wake(caplog):
    """``MessagePump.post_message``'s own contract: it returns ``False`` --
    not a raise -- when the pump is already closing/closed. ``post()`` must
    treat that exactly like a raised wake failure, not silently report
    success while nothing is actually going to drain the posted value."""
    owner = _ClosingOwner()
    inbox = Inbox(owner)
    with caplog.at_level(logging.WARNING, logger="agent-worktrees.picker"):
        ok = _post_from_other_thread(inbox, "slot", "value")
    assert ok is False
    assert len(owner.messages) == 1
    assert any(
        "failed to wake the owning render flow" in r.message
        for r in caplog.records
    )
    assert inbox.drain() == {"slot": "value"}


def _post_from_other_thread_with_callback(inbox, slot, value, on_wake_failed):
    result = {}

    def _run():
        result["ok"] = inbox.post(slot, value, on_wake_failed=on_wake_failed)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    return result["ok"]


def test_on_wake_failed_receives_the_real_underlying_exception():
    """A caller with its own diagnosability contract (the setup-reload
    worker's #5220 fix) needs the REAL underlying exception, not just a
    generic ``False`` -- ``on_wake_failed`` is how it recovers that detail
    without changing what every other, ordinary caller of ``post()``
    receives."""
    inbox = Inbox(_BrokenOwner())
    received: list[Exception | None] = []
    ok = _post_from_other_thread_with_callback(
        inbox, "slot", "value", received.append
    )
    assert ok is False
    assert len(received) == 1
    assert isinstance(received[0], RuntimeError)
    assert "owner already torn down" in str(received[0])


def test_on_wake_failed_receives_none_when_post_message_returns_false_without_raising():
    inbox = Inbox(_ClosingOwner())
    received: list[Exception | None] = []
    ok = _post_from_other_thread_with_callback(
        inbox, "slot", "value", received.append
    )
    assert ok is False
    assert received == [None]


def test_on_wake_failed_is_never_called_on_a_successful_wake():
    inbox = Inbox(_RecordingOwner())
    received: list[Exception | None] = []
    ok = _post_from_other_thread_with_callback(
        inbox, "slot", "value", received.append
    )
    assert ok is True
    assert received == []


def test_on_wake_failed_is_never_called_on_the_home_thread_path():
    """The home-thread path never queues a wake message at all (the value
    is applied directly, or recorded for later) -- ``on_wake_failed`` is
    specifically about a FAILED WAKE, which can't happen there."""
    inbox = Inbox(_BrokenOwner())  # home thread == this test's own thread
    received: list[Exception | None] = []
    ok = inbox.post("status", "ready", on_wake_failed=received.append)
    assert ok is True
    assert received == []


def test_a_raising_on_wake_failed_callback_does_not_mask_the_original_failure(caplog):
    inbox = Inbox(_BrokenOwner())

    def _bad_callback(exc):
        raise ValueError("callback bug")

    with caplog.at_level(logging.WARNING, logger="agent-worktrees.picker"):
        ok = _post_from_other_thread_with_callback(
            inbox, "slot", "value", _bad_callback
        )
    assert ok is False
    assert any(
        "on_wake_failed callback itself raised" in r.message
        for r in caplog.records
    )
    # The value is still recorded -- the callback's own bug didn't lose it.
    assert inbox.drain() == {"slot": "value"}


def test_wake_failure_resets_wake_state_so_a_later_post_retries_the_wake():
    """A raised/false wake must not leave ``_wake_queued`` stuck ``True`` --
    otherwise every later post in the same batch (even for an unrelated
    slot, even a different epoch's setup/reload outcome) takes the
    "already queued" branch and returns ``True`` without ever actually
    retrying the wake, so it can hang forever believing it already
    succeeded."""
    owner = _ClosingOwner()
    inbox = Inbox(owner)
    first_ok = _post_from_other_thread(inbox, "slot-a", "a")
    assert first_ok is False
    assert len(owner.messages) == 1
    # A second, later post must attempt its OWN wake rather than silently
    # folding into the failed one.
    second_ok = _post_from_other_thread(inbox, "slot-b", "b")
    assert second_ok is False
    assert len(owner.messages) == 2
    assert inbox.drain() == {"slot-a": "a", "slot-b": "b"}


def test_a_post_racing_a_failing_wake_still_gets_its_own_retry():
    """The narrower race the reset above exists for: a second post()
    arriving WHILE the first attempt is still in flight (not after it has
    already failed) must not observe a stale "wake already queued" and
    skip its own attempt -- it must either genuinely share a wake that
    goes on to succeed, or (this case) block until the first attempt
    resolves and then see the correct, failed state and retry for real."""
    release_first_attempt = threading.Event()
    second_about_to_post = threading.Event()

    # A thin owner whose post_message blocks until released -- so the
    # second post() below has a real window to try to observe the first
    # attempt's in-progress (not-yet-resolved) state.
    class _StallingOwner:
        def __init__(self):
            self.messages = []

        def post_message(self, message):
            self.messages.append(message)
            second_about_to_post.set()
            release_first_attempt.wait(timeout=5)
            return False  # fails once released, like a closing pump

    owner = _StallingOwner()
    inbox = Inbox(owner)
    results = {}

    def _first():
        results["first"] = _post_from_other_thread(inbox, "slot-a", "a")

    t_first = threading.Thread(target=_first)
    t_first.start()
    assert second_about_to_post.wait(timeout=5)

    def _second():
        results["second"] = _post_from_other_thread(inbox, "slot-b", "b")

    t_second = threading.Thread(target=_second)
    t_second.start()
    # Give the second post a real chance to run concurrently before
    # releasing the first -- it must block on the wake lock, not race past
    # it believing a wake is already safely in flight.
    time.sleep(0.05)
    release_first_attempt.set()
    t_first.join(timeout=5)
    t_second.join(timeout=5)
    assert not t_first.is_alive() and not t_second.is_alive()
    assert results["first"] is False
    # The real point of this test: the second post must have made its OWN
    # delivery attempt (a second post_message call), not silently folded
    # into the first (failing) one.
    assert len(owner.messages) == 2
    assert results["second"] is False
    assert inbox.drain() == {"slot-a": "a", "slot-b": "b"}


def test_a_post_racing_a_concurrent_drain_still_gets_a_genuine_wake_attempt():
    """A regression for the lost-wake race: ``drain()`` must hold
    ``_wake_lock`` across BOTH its data snapshot (under ``_lock``) and its
    flag reset as one atomic section -- never two separate critical
    sections -- so a post() racing a drain can never observe a stale
    ``_wake_queued == True`` in the gap between them and skip its own real
    wake attempt, stranding its slot until some unrelated future drain (or
    forever, if nothing else ever drains again).

    This hooks the RELEASE of the inner ``_lock`` specifically -- the
    exact seam a buggy two-critical-section ``drain()`` would have a gap
    at -- and forces a racing post() to attempt its own wake exactly
    during that window. Under the real (fixed) implementation, ``_lock``
    is nested inside an outer, still-held ``_wake_lock``, so the racing
    post() must block instead of proceeding on stale state (and does
    attempt a genuine, new delivery once the drain completes). Verified
    against a deliberately reverted two-critical-section ``drain()``
    (matching the pre-fix shape) to confirm this test actually fails
    there: the racing post observes the stale flag, returns success
    without ever calling ``post_message`` again, and its slot is left
    unresolved by this exchange.
    """
    owner = _RecordingOwner()
    inbox = Inbox(owner)  # home thread == this test's own thread

    data_lock_released = threading.Event()
    resume_drain = threading.Event()
    real_lock = inbox._lock

    class _SlowLock:
        """Wraps the real data lock, pausing (holding nothing, but not
        yet letting the wrapped ``with`` block that used it return
        control to its caller) right after its first release -- the
        precise point a buggy ``drain()`` would separately, later,
        acquire ``_wake_lock`` to reset the flag, with nothing held in
        between."""

        def __init__(self):
            self._first = True

        def __enter__(self):
            real_lock.acquire()
            return self

        def __exit__(self, *exc_info):
            is_first, self._first = self._first, False
            real_lock.release()
            if is_first:
                data_lock_released.set()
                resume_drain.wait(timeout=5)
            return False

    inbox._lock = _SlowLock()

    # Seed one pending slot directly -- bypassing post() itself, which
    # would hit the home-thread shortcut on this test's own thread and
    # apply inline rather than leaving something for drain() to snapshot.
    inbox._slots["seed"] = "seed-value"
    inbox._pending["seed"] = None

    drain_result = {}

    def _drain():
        drain_result["batch"] = inbox.drain()

    t_drain = threading.Thread(target=_drain)
    t_drain.start()
    # drain()'s data snapshot has just completed and released `_lock` --
    # but (under the fix) it is still inside the OUTER `with
    # self._wake_lock:` block, paused before resetting the flag.
    assert data_lock_released.wait(timeout=5)

    post_result = {}

    def _post():
        post_result["ok"] = _post_from_other_thread(inbox, "slot", "value")

    t_post = threading.Thread(target=_post)
    t_post.start()
    time.sleep(0.05)
    assert t_post.is_alive(), (
        "post() must still be blocked (on the still-held outer wake "
        "lock) here -- proceeding past this point on stale state, "
        "before the drain has reset the flag, is exactly the lost-wake "
        "bug this test guards against"
    )

    resume_drain.set()
    t_drain.join(timeout=5)
    t_post.join(timeout=5)
    assert not t_drain.is_alive()
    assert not t_post.is_alive()

    assert drain_result["batch"] == {"seed": "seed-value"}
    assert post_result["ok"] is True
    # The real point: the racing post must have made its OWN genuine
    # delivery attempt (a real `post_message` call), never silently folded
    # into state the drain had already resolved.
    assert len(owner.messages) == 1
    assert inbox.drain() == {"slot": "value"}


def test_home_thread_post_logs_a_raising_closure_instead_of_escaping(caplog):
    """``post()`` documents that it never raises -- but the home-thread
    shortcut calls ``drain_apply()`` directly, which deliberately
    re-raises an ordinary closure exception. Without its own catch, that
    would escape straight out of ``post()`` itself on the home thread,
    breaking the documented contract. Confirm it is logged, not left to
    escape, and ``post()`` still returns ``True`` (applied inline, which
    is what "never raises" promises)."""
    owner = _RecordingOwner()
    inbox = Inbox(owner)  # home thread == this test's own thread

    def _boom():
        raise RuntimeError("closure bug")

    with caplog.at_level(logging.WARNING, logger="agent-worktrees.picker"):
        ok = inbox.post("broken", _boom)
    assert ok is True
    assert any(
        "a closure raised while applying inline" in r.message
        for r in caplog.records
    )


def test_discard_removes_a_pending_slot_without_applying_it():
    inbox, _ = _inbox_with_foreign_home()
    calls = []
    inbox.post("doomed", lambda: calls.append("ran"))
    assert inbox.discard("doomed") is True
    assert inbox.pending_slots() == frozenset()
    assert inbox.snapshot() == {}
    # Drained (and discarded) -- the closure must never run.
    assert inbox.drain_apply() == 0
    assert calls == []


def test_discard_on_an_absent_or_already_drained_slot_returns_false():
    inbox, _ = _inbox_with_foreign_home()
    assert inbox.discard("never-posted") is False
    inbox.post("x", 1)
    inbox.drain()
    assert inbox.discard("x") is False


def test_drain_apply_runs_every_closure_even_when_one_raises():
    """A batch is independent producers' outcomes -- one producer's closure
    raising must never cause a different, unrelated producer's own posted
    closure (in the same batch) to go silently un-invoked."""
    ran = []
    inbox, _ = _inbox_with_foreign_home()
    inbox.post("first", lambda: ran.append("first"))

    def _boom():
        ran.append("boom")
        raise ValueError("first failure")

    inbox.post("raiser", _boom)
    inbox.post("last", lambda: ran.append("last"))
    with pytest.raises(ValueError, match="first failure"):
        inbox.drain_apply()
    assert sorted(ran) == ["boom", "first", "last"]


def test_drain_apply_propagates_the_exception_in_deterministic_posting_order():
    """``_pending`` is insertion-ordered specifically so that, when more
    than one closure in a batch raises, WHICH exception propagates is
    deterministic (the first one posted), never varying run to run the
    way it would under an unordered ``set``."""
    inbox, _ = _inbox_with_foreign_home()

    def _raise(msg):
        def _inner():
            raise ValueError(msg)
        return _inner

    inbox.post("first", _raise("first failure"))
    inbox.post("second", _raise("second failure"))
    with pytest.raises(ValueError, match="first failure"):
        inbox.drain_apply()


def test_coalescing_an_existing_slot_moves_it_to_the_end_of_posting_order():
    """A re-post to an already-pending slot is a COALESCE, not a no-op --
    its effective posting time is the latest post, not the first. Without
    moving it, a plain dict reassignment leaves it at its original
    position, silently breaking the documented posting-order guarantee:
    post a, then b, then re-post (coalesce) a -- a's effective post is now
    AFTER b's, so if both still-pending closures raise, b's exception must
    win, not a's stale original-position one."""
    inbox, _ = _inbox_with_foreign_home()

    def _raise(msg):
        def _inner():
            raise ValueError(msg)
        return _inner

    inbox.post("a", _raise("a failure (first version)"))
    inbox.post("b", _raise("b failure"))
    # Coalesce "a" -- a new value for an already-pending slot, posted
    # after "b".
    inbox.post("a", _raise("a failure (coalesced, now last)"))
    with pytest.raises(ValueError, match="b failure"):
        inbox.drain_apply()


def test_drain_apply_does_not_catch_keyboard_interrupt_or_system_exit():
    """A closure raising a process-control exception must propagate
    immediately, like it would anywhere else -- never get captured,
    deferred behind other closures, and re-raised later as if it were an
    ordinary producer failure."""
    inbox, _ = _inbox_with_foreign_home()
    ran = []

    def _interrupt():
        raise KeyboardInterrupt()

    inbox.post("ctrl-c", _interrupt)
    inbox.post("after", lambda: ran.append("after"))
    with pytest.raises(KeyboardInterrupt):
        inbox.drain_apply()
    # Unlike an ordinary Exception, this one is NOT required to let later
    # closures in the batch run first -- it's a process-control signal.
    assert ran == []


def test_home_thread_post_never_consults_post_message_even_if_it_would_raise():
    """The home-thread shortcut applies inline unconditionally -- it must not
    depend on (or be defeated by) a broken ``post_message``, since the whole
    point is that nothing needs to pump an event loop in this case."""
    owner = _BrokenOwner()
    inbox = Inbox(owner)
    ok = inbox.post("slot", lambda: None)
    assert ok is True


def test_ensure_inbox_lazily_constructs_and_then_reuses_the_same_instance():
    class _Owner:
        pass

    owner = _Owner()
    assert not hasattr(owner, "inbox")
    first = ensure_inbox(owner)
    assert isinstance(first, Inbox)
    assert owner.inbox is first
    second = ensure_inbox(owner)
    assert second is first


def test_ensure_inbox_returns_an_already_constructed_inbox_untouched():
    class _Owner:
        pass

    owner = _Owner()
    owner.inbox = Inbox(owner)
    assert ensure_inbox(owner) is owner.inbox


def test_drain_inbox_swallows_a_raising_closure_instead_of_crashing_the_render_flow(caplog):
    """``PickerScreenRuntimeMixin._drain_inbox`` is called from ``_tick()``
    and the ``InboxUpdated`` message handler -- squarely on the render
    flow. ``Inbox.drain_apply()`` deliberately re-raises a closure's own
    exception, but letting that escape ``_drain_inbox`` itself would
    terminate rendering entirely over one producer's bug. This is the
    last-resort net: a raising closure is logged, not left to crash the
    screen, and `_drain_inbox` itself never raises."""

    class _Screen(PickerScreenRuntimeMixin):
        def post_message(self, message):
            return True

    screen = _Screen()
    # Post directly via ``drain()``'s underlying storage (bypassing the
    # home-thread immediate-apply shortcut, which would otherwise invoke
    # the closure synchronously inside ``post()`` itself, not inside
    # ``_drain_inbox``) -- construct the Inbox on a foreign thread so this
    # test's own thread is an ordinary, non-home poster.
    inbox, _ = _inbox_with_foreign_home(screen)
    screen.inbox = inbox
    ran = []

    def _boom():
        ran.append("boom")
        raise RuntimeError("producer bug")

    screen.inbox.post("broken", _boom)
    with caplog.at_level(logging.WARNING, logger="agent-worktrees.picker"):
        result = screen._drain_inbox()
    assert ran == ["boom"]
    assert result == 0
    assert any(
        "a posted closure raised" in r.message for r in caplog.records
    )


def test_apply_from_worker_posts_distinct_slots_without_uuid_overhead():
    """``_apply_from_worker`` names each posted slot from a cheap,
    module-level counter rather than a fresh ``uuid4()`` per call -- confirm
    repeated calls still get distinct slots (no coalescing two unrelated
    outcomes together) and both callbacks survive a drain."""

    class _Screen(PickerScreenLoadingMixin):
        pass

    screen = _Screen()
    # Give the screen an Inbox whose home thread is NOT this test's own --
    # otherwise each post below would apply immediately inline (the
    # home-thread shortcut), leaving nothing pending to assert on.
    inbox, _ = _inbox_with_foreign_home(screen)
    screen.inbox = inbox
    ran = []
    screen._apply_from_worker(lambda: ran.append("first"))
    screen._apply_from_worker(lambda: ran.append("second"))
    assert len(screen.inbox.pending_slots()) == 2
    assert screen.inbox.drain_apply() == 2
    assert sorted(ran) == ["first", "second"]


def test_apply_from_worker_drops_quietly_when_the_picker_already_cancelled_it(
    caplog,
):
    """Regression: a mount-time setup thread (``_setup_live_async``,
    ``_setup_live_pivots``, the prewarm/reload workers, ...) still finishing
    its work when ``on_unmount`` sets ``_bg_cancel`` (the picker is tearing
    down -- e.g. a launch decision such as resuming a worktree) must NOT post
    into the (already torn-down) Inbox at all, and must NOT log a WARNING.
    Before this fix, ``_apply_from_worker`` unconditionally posted, which
    ``Inbox.post`` could only report as a "failed to wake the owning render
    flow" warning -- noisy, and indistinguishable from a genuine, unexpected
    wake failure -- for what is actually this expected, intentional exit.
    Mirrors ``test_run_bg_drops_quietly_when_the_picker_already_cancelled_it``
    for the sibling ``_run_bg`` path."""

    class _Screen(PickerScreenLoadingMixin):
        pass

    screen = _Screen()
    inbox, _owner = _inbox_with_foreign_home(screen)
    screen.inbox = inbox
    screen._bg_cancel = threading.Event()
    screen._bg_cancel.set()  # picker already tore down

    ran = []
    with caplog.at_level(logging.WARNING, logger="agent-worktrees.picker"):
        screen._apply_from_worker(lambda: ran.append("applied"))

    assert ran == []
    assert screen.inbox.pending_slots() == frozenset()
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_apply_from_worker_still_posts_without_bg_cancel_attribute():
    """Lightweight test doubles across the suite construct a bare object with
    no ``_bg_cancel`` of its own (no real ``__init__`` ran) -- the cancel
    check must degrade safely (never crash, never silently drop) for them."""

    class _Screen(PickerScreenLoadingMixin):
        pass

    screen = _Screen()
    inbox, _owner = _inbox_with_foreign_home(screen)
    screen.inbox = inbox
    screen._apply_from_worker(lambda: None)
    assert len(screen.inbox.pending_slots()) == 1


def test_inbox_updated_message_carries_no_payload_and_names_its_handler():
    message = InboxUpdated()
    assert message.handler_name == "on_inbox_updated"
