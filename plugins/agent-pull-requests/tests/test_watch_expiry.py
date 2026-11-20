"""Expiry crosses the real persistence owner before either delivery contract."""

from __future__ import annotations

import copy
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_watch_delivery import DaemonFactory, spec, wait
from test_watch_delivery import make as make

from agent_pull_requests import watch_daemon
from agent_pull_requests.watch_contract import MERGED, REVIEW_CHANGED, PRSnapshot
from agent_pull_requests.watch_notification import event_payload
from agent_pull_requests.watch_registry import FiredEvent, WatchKey, WatchRegistry
from agent_pull_requests.watch_storage import (
    StateCommitUncertain, read_subscriptions_state, watch_state_paths,
)

pytestmark = pytest.mark.contract("agent_pull_requests.watch.acknowledged_delivery")


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
    registry.register(
        key, "later", until=(REVIEW_CHANGED,), notify={}, timeout=600,
        acknowledged=acknowledged,
    )
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
            published.set()
            committed.append(copy.deepcopy(entries))
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
