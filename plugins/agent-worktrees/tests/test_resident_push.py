"""Tests for :mod:`agent_worktrees.resident_push`.

The bridge from a ``tracking_write`` verb handler running INSIDE the resident
status-monitor process to that process's own live segment-cache/wake-event,
so an explicit status write (or any other bound event) refreshes OTHER
sessions' status bars on the monitor's next loop iteration instead of
waiting out the full periodic sweep interval.
"""

from __future__ import annotations

import threading

import pytest

from agent_worktrees import resident_push


@pytest.fixture(autouse=True)
def _reset_bound_state():
    resident_push.reset()
    yield
    resident_push.reset()


def test_notify_is_a_safe_noop_when_unbound():
    # Never raises even though nothing has called bind() yet (e.g. the
    # in-process fallback path with no resident daemon reachable).
    resident_push.notify("/some/worktree/path")


def test_notify_invalidates_the_bound_segment_cache_and_wakes():
    invalidated = []

    class _FakeCache:
        def invalidate(self, path):
            invalidated.append(path)

    wake_event = threading.Event()
    resident_push.bind(wake_event, _FakeCache())

    resident_push.notify("/some/worktree/path")

    assert invalidated == ["/some/worktree/path"]
    assert wake_event.is_set()


def test_notify_with_no_path_still_wakes_but_skips_invalidation():
    invalidated = []

    class _FakeCache:
        def invalidate(self, path):
            invalidated.append(path)

    wake_event = threading.Event()
    resident_push.bind(wake_event, _FakeCache())

    resident_push.notify(None)

    assert invalidated == []
    assert wake_event.is_set()


def test_notify_never_raises_even_if_the_bound_cache_is_broken():
    class _BrokenCache:
        def invalidate(self, path):
            raise RuntimeError("boom")

    wake_event = threading.Event()
    resident_push.bind(wake_event, _BrokenCache())

    resident_push.notify("/some/worktree/path")  # must not raise

    # The wake-event set() never ran because invalidate() raised first --
    # best-effort means "don't crash the caller", not "guarantee delivery".
    assert not wake_event.is_set()


def test_reset_clears_bound_state():
    wake_event = threading.Event()
    resident_push.bind(wake_event, object())
    resident_push.reset()

    resident_push.notify("/some/worktree/path")  # safe no-op post-reset

    assert not wake_event.is_set()
