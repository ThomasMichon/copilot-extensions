"""Committed disk outcomes, independent subscribers and bounded selected evidence."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from agent_worktrees import record_cache, tracking
from agent_worktrees import tracking_save_capture as capture
from agent_worktrees import tracking_save_notifications as notifications
from agent_worktrees.effort_focus import ActiveEffort


@pytest.fixture
def saved(tmp_path):
    record_cache.clear()
    record = tracking.create_new_record(
        "capture-one",
        "worktree/capture-one",
        str(tmp_path / "checkout"),
        "test-chamber",
        "capture-machine",
        "wsl",
        tmp_path,
    )
    yield record, tmp_path / "capture-one.yaml"
    record_cache.clear()


def receipt(events):
    return next(event for event in events if isinstance(event, capture.SaveReceipt))


def test_actual_save_failure_isolation_and_independent_legacy_snapshots(saved, caplog):
    record, path = saved
    seen = []

    def failed(actual_path, snapshot):
        assert actual_path == path
        assert yaml.safe_load(path.read_text())["title"] == "committed"
        snapshot.title = "subscriber mutation"
        snapshot.resources.append(tracking.ResourceClaim(ref="not-written"))
        raise RuntimeError("optional subscriber failed")

    def later(actual_path, snapshot):
        seen.append((actual_path, snapshot.title, list(snapshot.resources), snapshot._loaded_from))

    with ExitStack() as stack:
        stack.callback(record_cache.observe_writes(failed))
        stack.callback(record_cache.observe_writes(later))
        record.title = "committed"
        tracking.save_record(record, path)
    assert seen == [(path, "committed", [], path)]
    assert tracking.load_record(path).title == "committed"
    assert tracking.load_record(path).resources == []
    assert "subscriber failed after canonical persistence" in caplog.text


def test_receipt_is_opt_in_immutable_and_fenced_to_actual_saved_bytes(saved, caplog):
    record, path = saved
    first, later = [], []

    def change(event):
        if isinstance(event, capture.SaveReceipt):
            first.append(event)
            event.after.domains[0].value().append({"ref": "not-real"})
            with pytest.raises(FrozenInstanceError):
                event.actor_id = "fabricated"
            raise ValueError("receipt subscriber failed")

    with ExitStack() as stack:
        stack.callback(notifications.observe_receipts(change))
        stack.callback(notifications.observe_receipts(later.append))
        record.summary = "actual saved disposition"
        tracking.save_record(record, path)
    event = receipt(later)
    assert first[0] == event
    assert event.after.domains[0].value() == []
    assert event.saved_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert event.saved_bytes == len(path.read_bytes())
    assert event.actor_id is None and event.actor_qualification == "unknown"
    assert event.lock_qualification == "record-lock-held"
    assert event.history_completeness == "unknown"
    assert event.changed_domains == ("disposition",)
    assert event.after.identity.incarnation == record.creation_nonce
    assert event.encoded_bytes <= capture.MAX_RECEIPT_BYTES
    assert "subscriber failed after canonical persistence" in caplog.text


def test_stale_requested_binding_is_not_a_saved_transition(saved):
    record, path = saved
    stale = tracking.load_record(path)
    record.active_effort = ActiveEffort("efforts/active/actual/README.md", "owner", "one")
    record.effort_revision = 3
    tracking.save_record(record, path)
    events = []
    unsubscribe = notifications.observe_receipts(events.append)
    try:
        stale.title = "new disposition only"
        tracking.save_record(stale, path)
    finally:
        unsubscribe()
    event = receipt(events)
    before = next(item for item in event.before.domains if item.domain == "binding").value()
    after = next(item for item in event.after.domains if item.domain == "binding").value()
    assert before == after
    assert after["effort_revision"] == 3
    assert "binding" not in event.changed_domains
    assert yaml.safe_load(path.read_text())["effort_revision"] == 3


@pytest.mark.parametrize("failure", ["stat", "acceleration"])
def test_cache_failure_after_real_persistence_cannot_suppress_delivery(
    saved, monkeypatch, caplog, failure
):
    record, path = saved
    actual_warm = record_cache._warm

    def fail_stat(*args, **kwargs):
        raise OSError("private cache stat failure")

    def warm(actual_path, content):
        if failure == "acceleration":
            raise RuntimeError("private cache accelerator failure")
        with monkeypatch.context() as patch:
            patch.setattr(Path, "stat", fail_stat)
            actual_warm(actual_path, content)

    monkeypatch.setattr(record_cache, "_warm", warm)
    events, legacy = [], []
    with ExitStack() as stack:
        stack.callback(notifications.observe_receipts(events.append))
        stack.callback(record_cache.observe_writes(lambda p, r: legacy.append(r.title)))
        record.title = "saved despite cache failure"
        tracking.save_record(record, path)
    assert yaml.safe_load(path.read_text())["title"] == "saved despite cache failure"
    assert legacy == ["saved despite cache failure"]
    assert receipt(events).saved_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "cache warming failed after canonical persistence" in caplog.text


def test_cache_warming_no_longer_deepcopies_caller_owned_full_records(saved, monkeypatch):
    from agent_worktrees import pr_authority

    record, path = saved
    actual_write = pr_authority.write_record

    def forbidden(*args, **kwargs):
        raise RuntimeError("unbounded deepcopy forbidden")

    def persisted(*args, **kwargs):
        actual_write(*args, **kwargs)
        monkeypatch.setattr(record_cache.copy, "deepcopy", forbidden)

    monkeypatch.setattr(pr_authority, "write_record", persisted)
    seen = []
    unsubscribe = record_cache.observe_writes(lambda p, r: seen.append(r.title))
    try:
        record.title = "actual canonical bytes"
        tracking.save_record(record, path)
    finally:
        unsubscribe()
    assert seen == ["actual canonical bytes"]
    assert yaml.safe_load(path.read_text())["title"] == "actual canonical bytes"


def test_unchanged_and_failed_canonical_writes_do_not_invent_transitions(saved):
    record, path = saved
    events, legacy = [], []
    with ExitStack() as stack:
        stack.callback(notifications.observe_receipts(events.append))
        stack.callback(record_cache.observe_writes(lambda p, r: legacy.append(r.title)))
        tracking.save_record(record, path)
        event = receipt(events)
        assert event.changed_domains == ()
        before = path.read_bytes()
        events.clear()
        legacy.clear()
        record._loaded_record_subject = ("different-ledger",)
        with pytest.raises(ValueError, match="record subject changed"):
            tracking.save_record(record, path)
        assert path.read_bytes() == before
    assert events == legacy == []


def test_actual_atomic_replace_failure_preserves_canonical_error_and_emits_nothing(
    saved, monkeypatch
):
    record, path = saved
    before = path.read_bytes()
    events, legacy = [], []

    def failed_replace(*args, **kwargs):
        raise OSError("private canonical replace failure")

    monkeypatch.setattr(tracking, "_replace_with_retry", failed_replace)
    with ExitStack() as stack:
        stack.callback(notifications.observe_receipts(events.append))
        stack.callback(record_cache.observe_writes(lambda p, r: legacy.append(r)))
        record.title = "must not appear on disk"
        with pytest.raises(OSError, match="canonical replace failure"):
            tracking.save_record(record, path)
    assert path.read_bytes() == before and events == legacy == []


def test_oversized_saved_evidence_is_explicit_partial_not_partial_legacy_object(saved, caplog):
    record, path = saved
    events, legacy = [], []
    with ExitStack() as stack:
        stack.callback(notifications.observe_receipts(events.append))
        stack.callback(record_cache.observe_writes(lambda p, r: legacy.append(r)))
        record.summary = "x" * (capture.MAX_CONTENT_BYTES + 1)
        tracking.save_record(record, path)
    assert len(yaml.safe_load(path.read_text())["summary"]) > capture.MAX_CONTENT_BYTES
    event = receipt(events)
    assert event.coverage == "partial" and event.saved_sha256 is None
    assert event.after.omission == "saved-content-unavailable-or-oversized"
    assert legacy == []
    assert any(
        isinstance(item, capture.DeliveryIssue) and item.omitted_bytes is None for item in events
    )
    assert "delivery incomplete" in caplog.text


def test_large_claim_projection_is_omitted_not_an_empty_or_count_derived_event(saved):
    record, path = saved
    events = []
    unsubscribe = notifications.observe_receipts(events.append)
    try:
        record.resources = [
            tracking.ResourceClaim(kind="worktree", ref=f"child-{number}")
            for number in range(capture.MAX_ITEMS + 1)
        ]
        tracking.save_record(record, path)
    finally:
        unsubscribe()
    event = receipt(events)
    claims = next(item for item in event.after.domains if item.domain == "claims")
    assert claims.data is None and claims.omission == "claim-item-bound"
    assert event.coverage == "partial" and "claims" not in event.changed_domains
    assert len(yaml.safe_load(path.read_text())["resources"]) == capture.MAX_ITEMS + 1


def test_subscriber_registration_is_bounded_and_capture_is_not_implicit(saved):
    callbacks = [lambda event: None for _ in range(notifications.MAX_SUBSCRIBERS + 1)]
    with ExitStack() as stack:
        for callback in callbacks[:-1]:
            stack.callback(notifications.observe_receipts(callback))
        with pytest.raises(ValueError, match="subscriber bound"):
            notifications.observe_receipts(callbacks[-1])


def test_legacy_observation_does_not_implicitly_enable_selected_receipts(saved, monkeypatch):
    record, path = saved
    seen = []

    def forbidden(*args, **kwargs):
        pytest.fail("selected receipt capture was not subscribed")

    monkeypatch.setattr(capture, "project", forbidden)
    unsubscribe = record_cache.observe_writes(lambda p, r: seen.append(r.title))
    try:
        record.title = "legacy only"
        tracking.save_record(record, path)
    finally:
        unsubscribe()
    assert seen == ["legacy only"]


def test_utf8_domain_byte_bound_is_not_a_character_limit(saved):
    record, path = saved
    events = []
    unsubscribe = notifications.observe_receipts(events.append)
    try:
        record.summary = "\u6f22" * 1500
        tracking.save_record(record, path)
    finally:
        unsubscribe()
    event = receipt(events)
    disposition = next(item for item in event.after.domains if item.domain == "disposition")
    assert disposition.data is None and event.coverage == "partial"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["summary"] == record.summary
    assert event.saved_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_alias_expansion_is_bounded_before_constructing_legacy_record_copies():
    shared = {"payload": ["x"] * 40}
    content = yaml.safe_dump({"resources": [shared] * 300})
    assert len(content.encode()) < capture.MAX_CONTENT_BYTES
    with pytest.raises(capture.CaptureOmitted, match="structure-bound"):
        capture.parse(content, Path("bounded.yaml"))
