"""Finite wait for accepted tracking writes during monitor shutdown."""

from __future__ import annotations

from collections.abc import Callable

#: Bounded grace period a shutdown/handoff (runtime superseded, a newer
#: monitor taking ownership, or the empty-strike idle-exit path) waits for
#: an in-flight tracking-write compute to finish before closing the
#: tracking_write server anyway. Generous relative to a single verb
#: transaction's own cost (a lock/load/mutate/save, comparable to one
#: worktree_status fact), bounded so a genuinely wedged compute can never
#: block a shutdown indefinitely (2026-09-26 PR review finding).
_TRACKING_WRITE_SHUTDOWN_GRACE_S = 10.0


def _wait_for_tracking_write_idle(
    is_busy: Callable[[], bool],
    *,
    grace_s: float = _TRACKING_WRITE_SHUTDOWN_GRACE_S,
    poll_interval_s: float = 0.1,
    now: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Block until ``is_busy()`` is false or ``grace_s`` elapses.

    ``is_busy`` should be a caller's combined busy predicate (e.g. this
    module's own ``_tracking_write_busy``, not ``tracking_write.
    has_inflight_write`` alone) -- a request already accepted (registered as
    a ``CoalescingServer`` subscriber) but not yet inside its ``compute()``
    call (where the in-flight counter increments) would otherwise slip past
    a check that only looked at the in-flight counter (2026-09-26 PR review
    finding).

    Extracted as its own function (rather than inlined in the shutdown
    ``finally`` block) so the wait/deadline logic itself is directly unit-
    testable without needing to drive the full ``cmd_status_monitor``
    lifecycle. ``now``/``sleep`` default to the real ``time`` module;
    overridden by tests to make the deadline/poll behavior deterministic
    without a real wall-clock wait.
    """
    import time as _time

    now = now or _time.time
    sleep = sleep or _time.sleep
    deadline = now() + grace_s
    while is_busy() and now() < deadline:
        sleep(poll_interval_s)
