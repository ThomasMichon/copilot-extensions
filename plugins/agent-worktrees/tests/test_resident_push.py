"""Tests for :mod:`agent_worktrees.resident_push`.

The bridge from a ``tracking_write`` verb handler running INSIDE the resident
status-monitor process to that process's own live segment-cache/wake-event,
so an explicit status write (or any other bound event) refreshes OTHER
sessions' status bars on the monitor's next loop iteration instead of
waiting out the full periodic sweep interval.
"""

from __future__ import annotations

import os
import threading
import types

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

    changed = []
    wake_event = threading.Event()
    resident_push.bind(wake_event, _BrokenCache(), changed=changed.append)

    resident_push.notify("/some/worktree/path")  # must not raise

    # A broken cache must not swallow the event: the roster still learns of
    # the change and the loop still wakes.
    assert changed == ["/some/worktree/path"]
    assert wake_event.is_set()


def test_notify_forwards_each_change_to_the_bound_roster_queue():
    changed = []

    def _broken_queue(path):
        changed.append(path)
        raise RuntimeError("queue fixture broken")

    wake_event = threading.Event()
    resident_push.bind(wake_event, object(), changed=_broken_queue)

    resident_push.notify("/some/worktree/path")
    resident_push.notify("")

    assert changed == ["/some/worktree/path", None]
    assert wake_event.is_set()


def test_reset_clears_bound_state():
    wake_event = threading.Event()
    resident_push.bind(wake_event, object())
    resident_push.reset()

    resident_push.notify("/some/worktree/path")  # safe no-op post-reset

    assert not wake_event.is_set()


def test_project_resolver_maps_paths_from_caches_then_registry(tmp_path, monkeypatch):
    from agent_worktrees import status_updater_cli

    served = tmp_path / "served"
    observed = tmp_path / "observed"
    anchor = tmp_path / "repo"
    looked_up = []

    def reverse_lookup(path):
        looked_up.append(path)
        return "anchored" if path == anchor else None

    monkeypatch.setattr(status_updater_cli, "_reverse_lookup_project", reverse_lookup)
    cache = types.SimpleNamespace(
        project_for_observation_path=lambda path: "observed" if path == str(observed) else None,
    )
    resolve = resident_push.project_resolver(
        {os.path.normcase(os.path.realpath(served)): "served"}, cache,
    )

    assert resolve(str(served)) == "served"
    assert resolve(str(observed)) == "observed"
    assert resolve(str(tmp_path / "repo.worktrees" / "wt-1")) == "anchored"
    assert resolve(str(tmp_path / "elsewhere")) is None
    # A segment cache without the observation lookup falls through to the registry.
    assert resident_push.project_resolver({}, object())(str(observed)) is None
    assert looked_up[0] == anchor
