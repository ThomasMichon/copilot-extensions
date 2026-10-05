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

import pytest

pytest.importorskip("textual", reason="textual not installed (optional TUI dep)")

from worktree_manager.production_picker.picker_tui.inbox import (
    Inbox,
    InboxUpdated,
    ensure_inbox,
)


class _RecordingOwner:
    """A fake ``MessagePump`` owner that records every ``post_message`` call
    instead of actually waking a running Textual app."""

    def __init__(self):
        self.messages = []

    def post_message(self, message):
        self.messages.append(message)


class _BrokenOwner:
    """An owner whose ``post_message`` always raises, simulating a torn-down
    or otherwise unreachable render flow."""

    def post_message(self, message):
        raise RuntimeError("owner already torn down")


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


def test_inbox_updated_message_carries_no_payload_and_names_its_handler():
    message = InboxUpdated()
    assert message.handler_name == "on_inbox_updated"
