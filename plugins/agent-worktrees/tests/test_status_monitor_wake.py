"""Tests for the resident status-monitor's push/backstop wake mechanics
(``status_monitor_cli.py``): the sweep loop's periodic ``interval`` sleep is
now interruptible, so an explicit status push (or any ``postToolUse``
mutation) wakes it early instead of waiting out the full backstop interval.

See ``test_resident_push.py`` for the bridge module's own unit tests, and
``test_status_monitor.py`` for the broader ``cmd_status_monitor``/
``_restart_status_monitor`` coverage this complements.
"""

from __future__ import annotations

import threading
import time

from agent_worktrees import status_monitor_cli as smc


def test_wake_interruptible_wait_returns_early_and_clears_the_event():
    event = threading.Event()
    event.set()

    started = time.monotonic()
    smc._wake_interruptible_wait(event, seconds=30)
    elapsed = time.monotonic() - started

    assert elapsed < 5  # returned immediately, not after the full 30s
    assert not event.is_set()  # consumed, so the NEXT wait blocks normally


def test_wake_interruptible_wait_honors_the_backstop_when_never_woken():
    event = threading.Event()

    started = time.monotonic()
    smc._wake_interruptible_wait(event, seconds=0.05)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.04  # genuinely waited out the interval, not a no-op
    assert not event.is_set()


def test_wake_interruptible_wait_set_from_another_thread_wakes_early():
    event = threading.Event()

    def _wake_soon():
        time.sleep(0.02)
        event.set()

    threading.Thread(target=_wake_soon, daemon=True).start()
    started = time.monotonic()
    smc._wake_interruptible_wait(event, seconds=30)
    elapsed = time.monotonic() - started

    assert elapsed < 5


def test_kind_wakes_sweep_only_for_post_tool_use_with_targets():
    assert smc._kind_wakes_sweep("postToolUse", None) is True  # invalidate-all
    assert smc._kind_wakes_sweep("postToolUse", ["/some/path"]) is True
    assert smc._kind_wakes_sweep("postToolUse", []) is False  # read-only tool call
    assert smc._kind_wakes_sweep("preToolUse", None) is False
    assert smc._kind_wakes_sweep("sessionStart", None) is False
    assert smc._kind_wakes_sweep("snapshot", None) is False
    assert smc._kind_wakes_sweep("statusPushed", None) is False


def test_kind_wakes_sweep_unconditionally_for_handoff_wake():
    # No targets of its own -- it exists purely to wake the loop for a
    # just-persisted handoff registration, never a cached status segment.
    assert smc._kind_wakes_sweep("handoffWake", None) is True
    assert smc._kind_wakes_sweep("handoffWake", []) is True
