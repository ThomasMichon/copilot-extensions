"""The addressed, thread-safe mailbox feeding the Picker's render flow.

Per the Picker render-flow invariant (see ``architecture.md``'s "never block
on cross-process/IO" section): no cross-process call and no blocking IO may
ever run *on* Textual's single event-loop thread, so every producer of a UI
update runs off-thread. Before this module existed, each such producer
hand-rolled its own thread + ``app.call_from_thread`` + cancellation/error
bookkeeping (``_run_bg``, ``_apply_from_worker``, the setup-reload worker, ...)
-- several independent, subtly-different reimplementations of the same
shape, each a fresh place to get the thread-safety or the error handling
wrong.

``Inbox`` is the ONE sanctioned mechanism now: a background thread never
mutates a widget, never touches ``app.call_from_thread`` directly, and never
hand-rolls its own wake/flag -- it calls ``inbox.post(slot, value)``. The
owning screen/widget drains the inbox (on its own render tick, and/or its
``InboxUpdated`` message handler) and applies whatever is currently pending in
one batched pass. Multiple posts to the same slot between two drains coalesce
to the single latest value -- a fast-moving producer (a streaming loader, a
burst of near-simultaneous action results) never queues one apply per post.

Why ``post_message``, not ``app.call_from_thread``: Textual's own
``call_from_thread`` *raises* ``RuntimeError`` if called from the very thread
running the app's event loop -- a real footgun for a primitive meant to be
usable from literally anywhere, including synchronously from the owning
thread itself (e.g. a test, or a producer that sometimes short-circuits
inline). ``MessagePump.post_message`` has no such restriction: it is
unconditionally thread-safe and never raises on the caller's identity.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Mapping

from textual.message import Message

log = logging.getLogger("agent-worktrees.picker")

class InboxUpdated(Message):
    """Posted to the owning widget/screen when new data is ready to drain.

    Carries no payload -- a handler always calls ``Inbox.drain()`` (or
    ``Inbox.drain_apply()``) to retrieve what changed, never anything off the
    message itself.
    """


class Inbox:
    """A thread-safe, addressed mailbox that wakes a Textual render flow.

    Construct one per owning widget/screen (not shared across screens): its
    posted wake goes to exactly one ``MessagePump``.
    """

    def __init__(self, owner) -> None:
        """``owner`` is the Textual ``MessagePump`` (widget, screen, or app)
        this inbox wakes via ``post_message`` -- it needs no running App yet
        at construction time; the first ``post()`` is what actually requires
        one. ``MessagePump.post_message`` itself never raises against an
        unmounted/appless owner, but it CAN return ``False`` (an
        undeliverable wake) in that case -- ``Inbox.post()`` treats that
        exactly like any other failed wake (logs a warning, returns
        ``False``), it is not silently absorbed as a no-op.

        Records the constructing thread as the inbox's "home" thread --
        normally the owner's own render/event-loop thread, since a widget is
        constructed on it. See ``post()`` for why that matters.
        """
        self._owner = owner
        self._lock = threading.Lock()
        self._slots: dict[str, Any] = {}
        # A ``dict`` used as an insertion-ordered set (Python's own dict
        # preserves insertion order; values are unused placeholders) --
        # ``drain_apply()`` documents re-raising the *first* exception in
        # *posting order*, which requires a deterministic iteration order. A
        # plain ``set`` would make that guarantee (and which exception wins
        # when more than one closure raises) vary from run to run.
        self._pending: dict[str, None] = {}
        self._wake_queued = False
        # Reentrant: a test double's (or any other same-thread, inline)
        # ``post_message`` may synchronously call back into
        # ``drain()``/``drain_apply()`` -- which also needs this lock to
        # reset ``_wake_queued`` -- from within ``post()``'s own
        # wake-attempt critical section, on the SAME thread. A plain
        # ``Lock`` would deadlock there; cross-thread callers still block
        # normally, since reentrancy only ever applies to the thread
        # already holding it.
        self._wake_lock = threading.RLock()
        self._home_thread_id = threading.get_ident()

    def post(self, slot: str, value: Any) -> bool:
        """Record *value* under *slot*, coalescing with any prior,
        not-yet-drained value for the same slot (last write wins).

        Safe to call from ANY thread, including the owner's own, and
        **never raises** -- a wake failure (e.g. a torn-down or malformed
        owner) is logged, not propagated, since this is routinely called
        from a background worker thread where an uncaught exception is
        otherwise silently swallowed by the interpreter after printing a
        traceback nobody is watching for. Queues at most one
        ``InboxUpdated`` wake per batch of posts between two drains -- a
        burst of posts (even across many slots, even from many threads)
        never re-enters the event loop more than once for that batch.

        Called from the inbox's own **home thread** (the thread that
        constructed it -- normally the render/event-loop thread itself,
        e.g. a producer that short-circuits inline, or a synchronous unit
        test with no running event loop to ever post a wake INTO), posting
        drains and applies immediately instead of merely queuing: nothing
        else is going to pump an event loop that may not even be running,
        and there is no cross-thread race to guard against since this IS
        that thread.

        Returns ``True`` unless a *needed* wake failed to deliver (applied
        inline, or folded into an already-queued wake, both count as
        success). Most callers can ignore this -- the value is never lost
        either way, just possibly stuck until some other drain happens to
        run -- but a caller with its own diagnosability contract for "this
        update must become visible" (e.g. a first-load failure a poller
        elsewhere is watching for) can use a ``False`` return to fall back
        to a direct, off-thread diagnostic write of its own, the same
        documented, narrow exception already established for a producer
        with nowhere else to send an outcome.
        """
        with self._lock:
            self._slots[slot] = value
            self._pending[slot] = None
        if threading.get_ident() == self._home_thread_id:
            # ``drain_apply()`` deliberately re-raises an ordinary closure
            # exception (see its own docstring) -- but ``post()`` itself
            # promises callers it never raises. Mirror
            # ``engine_runtime._drain_inbox()``'s own boundary catch here
            # too: log an escaping ``Exception`` rather than letting this
            # "drains inline" path violate that promise, while still
            # letting a genuine process-control ``BaseException``
            # (``KeyboardInterrupt``/``SystemExit``) propagate.
            try:
                self.drain_apply()
            except Exception:
                log.warning(
                    "Inbox.post(%r): a closure raised while applying "
                    "inline on the home thread", slot, exc_info=True,
                )
            return True
        # ``_wake_lock`` is held across the WHOLE check-attempt-reset
        # sequence below (not just the flag read/write), so it fully
        # serializes concurrent wake attempts: a second post() can never
        # observe "a wake is already queued" while a first attempt is
        # actually in flight and about to fail -- it either sees a
        # genuinely still-queued (and so far undelivered-but-not-yet-
        # failed) wake, or it blocks until that attempt resolves and then
        # sees the true post-resolution state. Without this, a narrow
        # window existed between "mark queued" and "attempt delivery"
        # where a concurrent post() could wrongly believe some other
        # thread's wake would cover it, even though that wake was about to
        # fail.
        with self._wake_lock:
            if self._wake_queued:
                return True
            self._wake_queued = True
            delivered = False
            wake_exc: Exception | None = None
            try:
                # MessagePump.post_message's own contract: it returns
                # ``False`` (not a raise) when the pump is already
                # closing/closed -- an undeliverable wake that looks
                # identical to success unless the return value itself is
                # checked, not just "did it raise".
                delivered = self._owner.post_message(InboxUpdated())
            except Exception as exc:
                wake_exc = exc
            if delivered:
                return True
            self._wake_queued = False
        log.warning(
            "Inbox.post(%r): failed to wake the owning render flow "
            "(posted value is still recorded and will be picked up "
            "by the next proactive drain, if any)", slot,
            # Pass the captured exception object itself (or ``None``) --
            # by the time this runs, we're well past the `except` block
            # that caught it, so `exc_info=True` here would find no
            # active exception and log a useless "NoneType: None" instead
            # of the real cause.
            exc_info=wake_exc,
        )
        return False

    def drain(self) -> dict[str, Any]:
        """Return and clear every slot posted since the last drain.

        Call only from the owner's own thread (the ``on_inbox_updated``
        handler, or a render tick draining proactively). Idempotent: calling
        it with nothing pending returns ``{}``.
        """
        # Holding ``_wake_lock`` across BOTH the data snapshot and the flag
        # reset (not two separate critical sections) closes a lost-wake
        # race: without this, a post() arriving after this drain's data
        # snapshot but before its flag reset would see `_wake_queued`
        # still `True`, conclude a wake was already in flight, and return
        # success without ever actually posting one -- stranding its own
        # (not-yet-drained) slot until the next proactive drain, or
        # forever if ticks are paused. Serializing against post()'s own
        # `_wake_lock`-held check means such a post() instead blocks here
        # until the reset below has happened, then correctly sees
        # `_wake_queued == False` and attempts its own, real wake.
        with self._wake_lock:
            with self._lock:
                changed = {name: self._slots.pop(name) for name in self._pending}
                self._pending.clear()
            self._wake_queued = False
        return changed

    def drain_apply(self) -> int:
        """Drain, then call every value that is callable (zero-argument).

        The common shape for one-shot "apply this result" producers (see
        ``background.run_background``): the slot's value IS the closure to
        run on the render thread.

        This drains and discards EVERY pending slot, not just callable
        ones -- a non-callable value posted to an inbox that also uses
        ``drain_apply()`` would be silently consumed here without anyone
        reading it. An owner that mixes closure slots with named *data*
        slots (e.g. a streaming loader's latest batch, interpreted by the
        caller's own logic rather than invoked) must call ``drain()``
        directly and handle both shapes itself; it must not also call
        ``drain_apply()`` against the same inbox.

        ``drain()`` removes the whole batch up front, so every closure in
        it runs even if an earlier one raises -- a batch is independent
        producers' results, and one producer's failure must never cause a
        later, unrelated producer's own outcome to go silently unapplied.
        The first exception raised (in posting order -- ``_pending``
        preserves insertion order precisely so this is deterministic) is
        re-raised after every closure has had a chance to run; any further
        exception is logged, not swallowed, so it is at least diagnosable
        even though only one exception can propagate.

        Only ``Exception`` (not ``BaseException``) is caught here: a
        closure raising ``KeyboardInterrupt``/``SystemExit`` is a
        process-control signal, not an ordinary producer failure, and must
        propagate immediately like it would anywhere else -- never get
        captured, deferred past other closures, and re-raised later as if
        it were a regular exception. This also matches
        ``engine_runtime._drain_inbox()``'s own boundary catch, which only
        ever catches ``Exception``.

        Returns the number of closures actually invoked.
        """
        applied = 0
        first_exc: Exception | None = None
        for value in self.drain().values():
            if not callable(value):
                continue
            try:
                value()
            except Exception as exc:
                if first_exc is None:
                    first_exc = exc
                else:
                    log.warning(
                        "Inbox.drain_apply: a later closure also raised "
                        "(only the first exception in this batch "
                        "propagates)",
                        exc_info=True,
                    )
            applied += 1
        if first_exc is not None:
            raise first_exc
        return applied

    def peek(self, slot: str, default: Any = None) -> Any:
        """Non-destructive read of *slot*'s latest value -- for a tick-based
        consumer that wants "the current value", not "what changed"."""
        with self._lock:
            return self._slots.get(slot, default)

    def discard(self, slot: str) -> bool:
        """Remove *slot* without applying/returning its value, if pending.

        For a caller whose ``post()`` wake failed and that chose its own
        fallback handling of the outcome (e.g. the setup-reload worker's
        ``#5220`` diagnosability path): the closure/value it posted must not
        survive to be picked up -- and incorrectly re-applied, against
        state the fallback has since disposed -- by some later, unrelated
        drain. Returns ``True`` if *slot* was actually pending (and is now
        removed), ``False`` if it was already drained/never posted.
        """
        with self._lock:
            was_pending = slot in self._pending
            self._pending.pop(slot, None)
            self._slots.pop(slot, None)
            return was_pending

    def pending_slots(self) -> frozenset[str]:
        """Slots with an undrained value right now (diagnostics/tests)."""
        with self._lock:
            return frozenset(self._pending)

    def snapshot(self) -> Mapping[str, Any]:
        """A shallow copy of every slot's current value, drained or not
        (diagnostics/tests only -- never the render path's own read, which
        must go through ``drain``/``drain_apply`` to get coalesced-once
        semantics)."""
        with self._lock:
            return dict(self._slots)


def ensure_inbox(owner) -> Inbox:
    """Return ``owner.inbox``, lazily constructing one if missing.

    Production code always goes through ``PickerScreen.__init__`` (which
    constructs ``self.inbox`` directly), so this exists for the many
    lightweight test doubles across the suite that exercise one method
    (``_run_bg``, the setup-reload worker, ...) on a bare object that never
    ran a real ``__init__`` -- those must keep working without each
    individually wiring up an ``Inbox`` of their own.
    """
    inbox = getattr(owner, "inbox", None)
    if inbox is None:
        inbox = owner.inbox = Inbox(owner)
    return inbox
