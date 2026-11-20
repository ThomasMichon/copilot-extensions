"""Expiry crosses the real persistence owner before either delivery contract."""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any

import pytest
from test_watch_delivery import DaemonFactory, spec, wait
from test_watch_delivery import make as make

from agent_pull_requests import watch_daemon
from agent_pull_requests.watch_contract import MERGED, REVIEW_CHANGED, Baseline, PRSnapshot
from agent_pull_requests.watch_notification import event_payload
from agent_pull_requests.watch_registry import FiredEvent, WatchKey, WatchRegistry
from agent_pull_requests.watch_storage import (
    StateCommitUncertain, read_subscriptions_state, watch_state_paths,
)

pytestmark = pytest.mark.contract("agent_pull_requests.watch.acknowledged_delivery")


@pytest.mark.parametrize("acknowledged", [False, True], ids=["legacy", "acknowledged"])
@pytest.mark.parametrize("known_baseline", [False, True], ids=["unknown", "known"])
def test_outage_expiry_preserves_shared_poller_and_other_subscriber_through_recovery(
    make: DaemonFactory, acknowledged: bool, known_baseline: bool,
) -> None:
    observations: Queue[PRSnapshot | None] = Queue()
    entered: Queue[int] = Queue()
    calls: list[int] = []
    delivered: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    unchanged = PRSnapshot(pr_state="open", review_decision="REVIEW_REQUIRED")
    key = WatchKey("example/project", 1)

    def fetch(*_: object) -> PRSnapshot:
        calls.append(len(calls) + 1)
        entered.put(calls[-1])
        snapshot = observations.get(timeout=2)
        if snapshot is None:
            raise OSError("controlled repeated provider outage")
        return snapshot

    def notify(event: FiredEvent) -> int:
        delivered.append((event_payload(event), read_subscriptions_state()))
        return 0

    daemon = make(fetch=fetch, notify=notify)
    try:
        for identity, until in (("expired", MERGED), ("survivor", REVIEW_CHANGED)):
            registration = spec(identity, until=[until], timeout=600)
            if not acknowledged:
                registration.pop("notification_protocol")
            daemon.compute("register", registration)
        assert entered.get(timeout=2) == 1
        expired = daemon._registry.subscriber(key, "expired")
        survivor = daemon._registry.subscriber(key, "survivor")
        assert expired is not None and survivor is not None
        offset = 0
        if known_baseline:
            observations.put(unchanged)
            assert entered.get(timeout=2) == 2
            assert expired.baseline == survivor.baseline == Baseline.from_snapshot(unchanged)
            offset = 1
        else:
            assert expired.baseline is None and survivor.baseline is None
        baseline = survivor.baseline
        deadline = survivor.deadline, survivor.deadline_at
        with daemon._pollers_lock:
            poller = daemon._pollers[key]
            assert tuple(daemon._native_pollers) == (poller,)
        for cycle in range(1, 4):
            observations.put(None)
            assert entered.get(timeout=2) == cycle + 1 + offset
            assert delivered == [] and daemon._registry.subscriber_count(key) == 2
            assert survivor.baseline == baseline
        with daemon._persist_lock:
            expired.deadline = 0
        observations.put(None)
        assert entered.get(timeout=2) == 5 + offset
        wait(lambda: len(delivered) == 1 and daemon._registry.subscriber_count(key) == 1)
        timeout_payload, durable = delivered[0]
        assert timeout_payload["subscriber_id"] == "expired"
        assert timeout_payload["timed_out"] is True and timeout_payload["transitions"] == []
        assert "pr_state" not in timeout_payload
        assert any(entry["subscriber_id"] == "survivor" for entry in durable)
        if acknowledged:
            pending = next(entry for entry in durable if entry["subscriber_id"] == "expired")
            assert pending["pending"]["payload"] == timeout_payload
        else:
            assert [entry["subscriber_id"] for entry in durable] == ["survivor"]
        assert survivor.baseline == baseline
        assert (survivor.deadline, survivor.deadline_at) == deadline
        assert daemon._pollers[key] is poller and poller.is_alive()
        for cycle in range(5, 7):
            observations.put(unchanged)
            assert entered.get(timeout=2) == cycle + 1 + offset
            assert len(delivered) == 1
            assert survivor.baseline == Baseline.from_snapshot(unchanged)
            assert (survivor.deadline, survivor.deadline_at) == deadline
        observations.put(PRSnapshot(pr_state="open", review_decision="APPROVED"))
        wait(lambda: len(delivered) == 2 and not daemon.status()["subscribers"])
        wait(lambda: not daemon._pollers and not daemon.status()["in_flight"])
        wait(lambda: not poller.is_alive())
        recovered, durable = delivered[1]
        assert recovered["subscriber_id"] == "survivor" and not recovered["timed_out"]
        assert recovered["transitions"] == [REVIEW_CHANGED]
        assert recovered["review_decision"] == "APPROVED"
        if acknowledged:
            assert len(durable) == 1 and durable[0]["pending"]["payload"] == recovered
        else:
            assert durable == []
        assert len(calls) == 7 + offset and len(delivered) == 2
        assert read_subscriptions_state() == []
        assert daemon._registry.active_keys() == () and not daemon._registry.pending_events()
        assert not daemon._shutdown_event.is_set()
    finally:
        observations.put(None)
        daemon.close()


@pytest.mark.parametrize("acknowledged", [False, True], ids=["legacy", "acknowledged"])
@pytest.mark.parametrize("terminal", [False, True], ids=["outage-timeout", "terminal"])
def test_expiry_keeps_other_filters_and_deadlines_and_emits_one_terminal_outcome(
    acknowledged: bool, terminal: bool,
) -> None:
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    expired = registry.register(
        key, "expired", until=(MERGED,), notify={}, timeout=1, acknowledged=acknowledged,
    )
    later_subscriber = registry.register(
        key, "later", until=(REVIEW_CHANGED,), notify={}, timeout=600,
        acknowledged=acknowledged,
    )
    later_deadline = later_subscriber.deadline, later_subscriber.deadline_at
    unchanged = PRSnapshot(pr_state="open", review_decision="REVIEW_REQUIRED")
    assert registry.apply_snapshot(key, unchanged) == ()
    expired.deadline = 0
    observed = PRSnapshot(pr_state="closed", merged=True, review_decision="REVIEW_REQUIRED")
    fired = registry.apply_snapshot(key, observed) if terminal else registry.sweep_timeouts((key,))
    assert len(fired) == 1 and fired[0].subscriber is expired
    assert fired[0].timed_out is not terminal
    assert fired[0].transitions == ((MERGED,) if terminal else ())
    assert registry.sweep_timeouts((key,)) == ()
    assert registry.apply_snapshot(key, observed if terminal else unchanged) == ()
    if acknowledged:
        registry.acknowledge(fired[0])
    assert registry.subscriber_count(key) == 1
    remaining = registry.subscriber(key, "later")
    assert remaining is not None
    assert (remaining.deadline, remaining.deadline_at) == later_deadline
    later = registry.apply_snapshot(key, PRSnapshot(pr_state="open", review_decision="APPROVED"))
    assert len(later) == 1 and later[0].subscriber.subscriber_id == "later"
    assert later[0].transitions == (REVIEW_CHANGED,) and not later[0].timed_out
    if acknowledged:
        registry.acknowledge(later[0])
    assert registry.active_keys() == () and registry.sweep_timeouts() == ()


@pytest.mark.parametrize("acknowledged", [False, True], ids=["legacy", "acknowledged"])
@pytest.mark.parametrize("failure", ["none", "definite", "uncertain"])
def test_outage_expiry_persists_before_one_notification_and_retires_its_poller(
    make: DaemonFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    acknowledged: bool, failure: str,
) -> None:
    entered, observe, failed = threading.Event(), threading.Event(), threading.Event()
    publishing, publish_allowed = threading.Event(), threading.Event()
    published = threading.Event()
    callback_entries: list[dict[str, Any]] = []
    callback_publication: list[bool] = []
    enqueued: list[dict[str, Any]] = []
    enqueue_publication: list[bool] = []
    committed: list[list[dict[str, Any]]] = []
    forbidden_rewrites: list[list[dict[str, Any]]] = []
    delivered: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    original_write = watch_daemon.write_subscriptions_state
    armed = False

    def unavailable(*_: object) -> PRSnapshot:
        entered.set()
        if not observe.wait(2):
            raise AssertionError("expiry fixture was not released")
        raise OSError("controlled provider outage")

    def write(entries: list[dict[str, Any]]) -> None:
        if not armed:
            original_write(entries)
            return
        if not failed.is_set() and failure == "definite":
            failed.set()
            raise OSError("controlled expiry write refusal")
        if not committed:
            publishing.set()
            if not publish_allowed.wait(2):
                raise AssertionError("expiry publication fixture was not released")
            original_write(entries)
            first_committed = read_subscriptions_state()
            assert first_committed == entries
            committed.append(first_committed)
            published.set()
            if failure == "uncertain":
                failed.set()
                raise StateCommitUncertain("controlled expiry replacement acknowledgement loss")
            return
        if failure == "uncertain" and not acknowledged:
            forbidden_rewrites.append(copy.deepcopy(entries))
            raise OSError("legacy expiry must not require another write after replacement")
        original_write(entries)

    def notify(event: FiredEvent) -> int:
        callback_publication.append(published.is_set())
        payload = event_payload(event)
        callback_entries.append(payload)
        delivered.append((payload, read_subscriptions_state()))
        return 0

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", write)
    daemon = make(fetch=unavailable, notify=notify)
    registration = spec(timeout=0.1)
    if not acknowledged:
        registration.pop("notification_protocol")
    try:
        daemon.compute("register", registration)
        assert entered.wait(2)
        with daemon._pollers_lock:
            pollers = tuple(daemon._native_pollers)
        assert len(pollers) == 1
        paths = watch_state_paths(tmp_path)
        original = paths.legacy_subscriptions.read_bytes()
        subscriber = daemon._registry.subscriber(WatchKey("example/project", 1), "one")
        assert subscriber is not None and subscriber.deadline is not None
        legacy = daemon._deliveries.legacy

        def enqueue(event: FiredEvent) -> None:
            enqueue_publication.append(published.is_set())
            enqueued.append(event_payload(event))
            legacy(event)

        monkeypatch.setattr(daemon._deliveries, "legacy", enqueue)
        deadline = subscriber.deadline
        wait(lambda: time.monotonic() >= deadline)
        armed = True
        observe.set()
        if failure == "definite":
            assert failed.wait(2)
        assert publishing.wait(2)
        assert callback_entries == [] and enqueued == []
        assert paths.legacy_subscriptions.read_bytes() == original
        if failure == "definite":
            assert daemon._persistence_error == "state_write_failed"
        publish_allowed.set()
        wait(lambda: len(delivered) == 1 and not daemon.status()["subscribers"])
        wait(lambda: not daemon._pollers and not daemon.status()["in_flight"])
        wait(lambda: all(not thread.is_alive() for thread in pollers))
        payload, durable_at_delivery = delivered[0]
        assert callback_entries == [payload] and forbidden_rewrites == []
        assert callback_publication == [True]
        assert enqueue_publication == ([] if acknowledged else [True])
        assert not daemon._shutdown_event.is_set()
        assert len(committed) == 1
        assert payload["timed_out"] is True and payload["transitions"] == []
        assert "pr_state" not in payload
        if acknowledged:
            assert len(durable_at_delivery) == 1
            assert durable_at_delivery[0]["pending"]["payload"] == payload
            assert committed[0][0]["pending"]["payload"] == payload
            assert payload["registration_id"] == subscriber.registration_id
            assert enqueued == []
        else:
            assert durable_at_delivery == committed[0] == []
            assert enqueued == [payload]
            assert "event_id" not in payload and "notification_protocol" not in payload
        assert len(delivered) == 1 and read_subscriptions_state() == []
        assert not daemon._registry.active_keys() and not daemon._registry.pending_events()
        assert failed.is_set() == (failure != "none")
    finally:
        armed = False
        observe.set()
        publish_allowed.set()
