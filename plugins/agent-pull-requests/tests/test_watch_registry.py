"""Tests for the pure subscriber-multiplexing registry (``watch_registry``)."""

from __future__ import annotations

import time

from agent_pull_requests.watch_contract import CLOSED, MERGED, REVIEW_CHANGED, PRSnapshot
from agent_pull_requests.watch_registry import WatchKey, WatchRegistry


def _key(number: int = 1) -> WatchKey:
    return WatchKey(repo="o/n", number=number)


def test_register_then_apply_snapshot_fires_on_merge():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED, CLOSED), notify={})
    # First poll: still open -- auto-baseline adopts it, no fire.
    fired = reg.apply_snapshot(key, PRSnapshot(pr_state="open"))
    assert fired == ()
    assert reg.subscriber_count(key) == 1
    # Second poll: merged -- fires, and the subscriber is removed.
    fired = reg.apply_snapshot(key, PRSnapshot(pr_state="closed", merged=True))
    assert len(fired) == 1
    assert fired[0].subscriber.subscriber_id == "sub-1"
    assert fired[0].transitions == (MERGED,)
    assert reg.subscriber_count(key) == 0


def test_already_terminal_at_first_poll_fires_immediately():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED, CLOSED), notify={})
    fired = reg.apply_snapshot(key, PRSnapshot(pr_state="closed", merged=True))
    assert len(fired) == 1
    assert fired[0].transitions == (MERGED,)
    assert reg.subscriber_count(key) == 0


def test_multiple_subscribers_on_same_key_share_one_poll():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED, CLOSED), notify={})
    reg.register(key, "sub-2", until=(REVIEW_CHANGED,), notify={})
    reg.apply_snapshot(key, PRSnapshot(pr_state="open", review_decision="REVIEW_REQUIRED"))
    assert reg.subscriber_count(key) == 2
    # A review-decision change fires only sub-2; sub-1 (only cares about
    # merge/close) stays registered for the same shared snapshot stream.
    fired = reg.apply_snapshot(
        key, PRSnapshot(pr_state="open", review_decision="APPROVED")
    )
    assert len(fired) == 1
    assert fired[0].subscriber.subscriber_id == "sub-2"
    assert reg.subscriber_count(key) == 1
    # sub-1 still fires later on the actual merge.
    fired = reg.apply_snapshot(key, PRSnapshot(pr_state="closed", merged=True))
    assert len(fired) == 1
    assert fired[0].subscriber.subscriber_id == "sub-1"
    assert reg.subscriber_count(key) == 0


def test_different_keys_are_fully_independent():
    reg = WatchRegistry()
    k1, k2 = _key(1), _key(2)
    reg.register(k1, "a", until=(MERGED, CLOSED), notify={})
    reg.register(k2, "b", until=(MERGED, CLOSED), notify={})
    fired = reg.apply_snapshot(k1, PRSnapshot(pr_state="closed", merged=True))
    assert len(fired) == 1
    assert fired[0].key == k1
    assert reg.subscriber_count(k1) == 0
    assert reg.subscriber_count(k2) == 1  # untouched


def test_unregister_removes_before_any_fire():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED,), notify={})
    assert reg.unregister(key, "sub-1") is True
    assert reg.subscriber_count(key) == 0
    assert key not in reg.active_keys()
    assert reg.unregister(key, "sub-1") is False


def test_timeout_fires_without_a_transition():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED, CLOSED), notify={}, timeout=0.01)
    # Prime the baseline on a non-firing poll.
    reg.apply_snapshot(key, PRSnapshot(pr_state="open"))
    time.sleep(0.02)
    fired = reg.apply_snapshot(key, PRSnapshot(pr_state="open"))
    assert len(fired) == 1
    assert fired[0].timed_out is True
    assert fired[0].transitions == ()
    assert reg.subscriber_count(key) == 0


def test_sweep_timeouts_fires_without_requiring_a_poll():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED,), notify={}, timeout=0.01)
    time.sleep(0.02)
    fired = reg.sweep_timeouts()
    assert len(fired) == 1
    assert fired[0].timed_out is True
    assert reg.subscriber_count(key) == 0


def test_status_reports_active_subscribers():
    reg = WatchRegistry()
    key = _key()
    reg.register(key, "sub-1", until=(MERGED,), notify={})
    reg.register(key, "sub-2", until=(MERGED,), notify={})
    assert reg.status() == {str(key): ["sub-1", "sub-2"]}


def test_snapshot_state_round_trips_through_restore_state():
    reg = WatchRegistry()
    key1, key2 = _key(1), _key(2)
    reg.register(key1, "a", until=(MERGED, CLOSED), notify={"argv": ["echo", "hi"]})
    # Prime a real baseline (not auto-None) so the round trip exercises it.
    reg.apply_snapshot(key1, PRSnapshot(pr_state="open", review_decision="REVIEW_REQUIRED"))
    reg.register(key2, "b", until=(REVIEW_CHANGED,), notify={}, timeout=120.0)

    dump = reg.snapshot_state()
    assert len(dump) == 2

    restored = WatchRegistry()
    count = restored.restore_state(dump)
    assert count == 2
    assert restored.subscriber_count(key1) == 1
    assert restored.subscriber_count(key2) == 1
    assert sorted(restored.status().keys()) == sorted(reg.status().keys())

    # The restored subscriber's baseline survived -- a review-decision
    # transition fires against it exactly as it would have pre-restart.
    fired = restored.apply_snapshot(
        key1, PRSnapshot(pr_state="open", review_decision="APPROVED")
    )
    # key1's subscriber only cares about MERGED/CLOSED, so a review change
    # alone must not fire it -- confirms until/baseline were both preserved
    # distinctly, not conflated.
    assert fired == ()

    # key2's subscriber (REVIEW_CHANGED, no baseline yet -- auto-baseline)
    # still has its ~120s timeout preserved (not reset to None/expired).
    sub = restored._subscribers[key2]["b"]
    assert sub.deadline is not None and sub.deadline > time.monotonic()


def test_restore_state_skips_malformed_entries_without_raising():
    reg = WatchRegistry()
    count = reg.restore_state(
        [
            {"repo": "o/n", "number": 1, "subscriber_id": "ok", "until": ["merged"], "notify": {}},
            {"repo": "o/n"},  # missing required fields
            "not-a-dict",  # wrong type entirely
        ]
    )
    assert count == 1
    assert reg.subscriber_count(_key(1)) == 1
