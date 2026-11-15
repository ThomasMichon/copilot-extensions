"""Actual record/pair release, nesting and finite deferred delivery."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
import yaml
from test_tracking_save_capture import saved as saved

from agent_worktrees import paired_claims, record_cache, tracking, worktree_notifications
from agent_worktrees import tracking_save_capture as capture
from agent_worktrees import tracking_save_notifications as notifications


def probe(path):
    def acquire():
        with tracking._RecordLock(path, blocking=False) as lock:
            return lock.acquired

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(acquire).result(timeout=2)


@pytest.mark.parametrize("kind", ["normal", "nested", "same-instance", "direct", "degraded"])
def test_notifications_follow_all_actual_record_holds(saved, monkeypatch, kind):
    record, path = saved
    seen = []
    native = []
    lock = tracking._RecordLock(path)
    if kind == "degraded":
        monkeypatch.setattr(lock, "_acquire_sidecar_blocking", lambda: False)

    def delivered(actual_path, snapshot):
        assert actual_path == path and probe(path)
        assert not lock.acquired
        assert yaml.safe_load(path.read_text())["title"] == "persisted"
        seen.append(snapshot.title)

    def native_changed(cwd):
        assert probe(path) and not lock.acquired
        native.append(cwd)

    monkeypatch.setattr(worktree_notifications, "notify_worktree_changed", native_changed)
    unsubscribe = record_cache.observe_writes(delivered)
    try:
        with lock:
            record.title = "persisted"
            if kind == "same-instance":
                with lock:
                    tracking.save_record(record, path)
            elif kind in {"direct", "degraded"}:
                tracking._save_record_unlocked(record, path)
            elif kind == "nested":
                with tracking._RecordLock(path, require_sidecar=True):
                    tracking.save_record(record, path)
            else:
                tracking.save_record(record, path)
            record.title = "not written after save"
            assert seen == [] and native == []
        assert seen == ["persisted"]
        assert native == [record.worktree_path]
    finally:
        unsubscribe()


def test_pair_guard_finishes_logical_cleanup_after_sibling_release_before_dispatch(
    saved, tmp_path, monkeypatch
):
    from agent_worktrees import config

    record, path = saved
    record.pair_id = "pair-one"
    record.pair_role = "harness"
    record.pair_kind = "worktree"
    record.pair_ref = tracking.format_claim_ref(record.execution_machine, record.repo, "sibling")
    tracking.save_record(record, path)
    monkeypatch.setattr(config, "project_dir", lambda project: tmp_path / project)
    sibling = tmp_path / record.repo / "worktrees" / "sibling.yaml"
    actual_guard = paired_claims._lifecycle_guard
    active = [False]

    @contextmanager
    def logical_guard(value):
        active[0] = True
        try:
            with actual_guard(value):
                yield
        finally:
            active[0] = False

    monkeypatch.setattr(paired_claims, "_lifecycle_guard", logical_guard)
    seen = []
    native = []

    def delivered(actual_path, snapshot):
        assert not active[0], "physical release ran before the pair's logical cleanup"
        assert probe(path) and probe(sibling)
        with paired_claims.lifecycle_guard(record):
            assert active[0]
        seen.append(snapshot.title)

    def native_changed(cwd):
        assert not active[0] and probe(path) and probe(sibling)
        native.append(cwd)

    monkeypatch.setattr(worktree_notifications, "notify_worktree_changed", native_changed)
    unsubscribe = record_cache.observe_writes(delivered)
    try:
        with paired_claims.lifecycle_guard(record):
            record.title = "paired save"
            tracking.save_record(record, path)
            assert seen == [] and native == []
        assert seen == ["paired save"] and not active[0]
        assert native == [record.worktree_path]
    finally:
        unsubscribe()


def test_native_change_bursts_coalesce_after_holds_without_selected_capture(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(worktree_notifications, "notify_worktree_changed", seen.append)
    monkeypatch.setattr(
        capture, "project", lambda *args: pytest.fail("native wake captured optional record evidence"),
    )
    with notifications.hold():
        for index in range(notifications.MAX_CHANGED_PATHS + 1):
            notifications.worktree_changed(str(tmp_path / f"worktree-{index}"))
        assert seen == []
    assert seen == [None]


def test_save_guard_encloses_entire_nested_guard_cleanup(saved, tmp_path, monkeypatch):
    record, path = saved
    active = [False]
    sibling = tmp_path / "sibling.yaml"
    monkeypatch.setattr(paired_claims, "_needs_finalized_proof", lambda *args: True)
    monkeypatch.setattr(paired_claims, "_proof_records", lambda *args: [record])
    monkeypatch.setattr(paired_claims, "validate_save", lambda *args: None)

    @contextmanager
    def guarded(value):
        active[0] = True
        try:
            with tracking._RecordLock(sibling):
                yield
        finally:
            active[0] = False

    monkeypatch.setattr(paired_claims, "lifecycle_guard", guarded)
    seen = []
    unsubscribe = record_cache.observe_writes(
        lambda p, r: seen.append((active[0], probe(sibling), r.title))
    )
    try:
        with tracking._RecordLock(path):
            record.title = "direct guarded save"
            tracking._save_record_unlocked(record, path)
            assert seen == []
        assert seen == [(False, True, "direct guarded save")]
    finally:
        unsubscribe()


@pytest.mark.parametrize("bound", ["count", "bytes"])
def test_queue_bounds_diagnose_incomplete_delivery_without_failing_saves(
    saved, monkeypatch, caplog, bound
):
    record, path = saved
    monkeypatch.setattr(
        notifications,
        "MAX_PENDING" if bound == "count" else "MAX_PENDING_BYTES",
        1,
    )
    events = []
    unsubscribe = notifications.observe_receipts(events.append)
    try:
        with tracking._RecordLock(path):
            for title in ("first", "second", "third"):
                record.title = title
                tracking.save_record(record, path)
                assert events == []
        assert yaml.safe_load(path.read_text())["title"] == "third"
    finally:
        unsubscribe()
    issues = [event for event in events if isinstance(event, capture.DeliveryIssue)]
    assert issues and issues[-1].omitted_count > 0
    assert issues[-1].history_completeness == "unknown"
    assert "queue-capacity" in caplog.text


def test_nonacquired_best_effort_lock_does_not_create_a_notification_hold(saved, monkeypatch):
    record, path = saved
    lock = tracking._RecordLock(path, blocking=False)
    monkeypatch.setattr(lock, "_sidecar_try", lambda: False)
    with lock:
        assert not lock.acquired
    seen = []
    unsubscribe = record_cache.observe_writes(lambda p, r: seen.append(r.title))
    try:
        record.title = "ordinary save"
        tracking.save_record(record, path)
    finally:
        unsubscribe()
    assert seen == ["ordinary save"]


def test_release_failure_does_not_decrement_or_dispatch_before_actual_release(
    saved, monkeypatch, caplog
):
    record, path = saved
    lock = tracking._RecordLock(path)
    lock.__enter__()
    actual_release = lock._release
    seen = []
    unsubscribe = record_cache.observe_writes(lambda p, r: seen.append(r.title))
    try:
        record.title = "saved with retained lock"
        tracking._save_record_unlocked(record, path)

        def failed_release():
            raise OSError("private release failure before physical unlock")

        monkeypatch.setattr(lock, "_release", failed_release)
        with pytest.raises(OSError):
            lock.__exit__(None, None, None)
        assert seen == [] and not probe(path)
        assert "notifications remain held" in caplog.text
    finally:
        monkeypatch.setattr(lock, "_release", actual_release)
        lock.__exit__(None, None, None)
        unsubscribe()
    assert seen == ["saved with retained lock"]


def test_canonical_error_in_outer_batch_still_delivers_prior_real_commit(saved):
    record, path = saved
    seen = []
    unsubscribe = record_cache.observe_writes(lambda p, r: seen.append(r.title))
    try:
        with pytest.raises(ValueError):
            with tracking._RecordLock(path):
                record.title = "first committed"
                tracking.save_record(record, path)
                record._loaded_record_subject = ("different-ledger",)
                tracking.save_record(record, path)
    finally:
        unsubscribe()
    assert seen == ["first committed"]
    assert yaml.safe_load(path.read_text())["title"] == "first committed"


def test_reentrant_subscriber_delivery_has_a_finite_drain_budget(saved, monkeypatch, caplog):
    record, path = saved
    monkeypatch.setattr(notifications, "MAX_DRAIN", 3)
    calls = []

    def reenter(actual_path, snapshot):
        assert probe(path)
        calls.append(snapshot.title)
        latest = tracking.load_record(path)
        latest.title = f"reentered-{len(calls)}"
        tracking.save_record(latest, path)

    unsubscribe = record_cache.observe_writes(reenter)
    try:
        record.title = "start"
        tracking.save_record(record, path)
    finally:
        unsubscribe()
    assert len(calls) == 3
    assert yaml.safe_load(path.read_text())["title"] == "reentered-3"
    assert "reentrant-drain-bound" in caplog.text


def test_partial_acquisition_cannot_dispatch_while_its_inprocess_lock_is_held(saved, monkeypatch):
    record, path = saved
    seen = []
    lock = tracking._RecordLock(path, blocking=False)

    def attempt():
        record_cache.store(path, record)
        assert seen == []
        return False

    def delivered(actual_path, snapshot):
        assert probe(path) and not lock.acquired
        seen.append(snapshot.title)

    monkeypatch.setattr(lock, "_sidecar_try", attempt)
    unsubscribe = record_cache.observe_writes(delivered)
    try:
        with lock:
            assert not lock.acquired
    finally:
        unsubscribe()
    assert seen == [record.title]


def test_later_subscriber_waits_for_a_hold_created_by_an_earlier_subscriber(saved):
    record, path = saved
    held, seen = [], []

    def first(actual_path, snapshot):
        lock = tracking._RecordLock(path)
        lock.__enter__()
        held.append(lock)

    def later(actual_path, snapshot):
        assert probe(path)
        seen.append(snapshot.title)

    first_unsubscribe = record_cache.observe_writes(first)
    later_unsubscribe = record_cache.observe_writes(later)
    try:
        record.title = "saved once"
        tracking.save_record(record, path)
        assert seen == [] and len(held) == 1
        held[0].__exit__(None, None, None)
        assert seen == ["saved once"]
    finally:
        first_unsubscribe()
        later_unsubscribe()
        for lock in held:
            if lock.acquired:
                lock.__exit__(None, None, None)
