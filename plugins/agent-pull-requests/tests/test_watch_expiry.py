"""Expiry crosses the real persistence owner before either delivery contract."""

from __future__ import annotations

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
    retry_waiting, retry_allowed = threading.Event(), threading.Event()
    delivered: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    original_write = watch_daemon.write_subscriptions_state
    armed = False

    def unavailable(*_: object) -> PRSnapshot:
        entered.set()
        if not observe.wait(2):
            raise AssertionError("expiry fixture was not released")
        raise OSError("controlled provider outage")

    def write(entries: list[dict[str, Any]]) -> None:
        if armed and not failed.is_set() and failure != "none":
            if failure == "uncertain":
                original_write(entries)
            failed.set()
            if failure == "uncertain":
                raise StateCommitUncertain("controlled expiry replacement acknowledgement loss")
            raise OSError("controlled expiry write refusal")
        if failed.is_set() and failure == "definite":
            retry_waiting.set()
            if not retry_allowed.wait(2):
                raise AssertionError("expiry retry fixture was not released")
        original_write(entries)

    def notify(event: FiredEvent) -> int:
        delivered.append((event_payload(event), read_subscriptions_state()))
        return 0

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", write)
    daemon = make(fetch=unavailable, notify=notify)
    registration = spec(timeout=0.1)
    if not acknowledged:
        registration.pop("notification_protocol")
    try:
        daemon.compute("register", registration)
        assert entered.wait(2)
        paths = watch_state_paths(tmp_path)
        original = paths.legacy_subscriptions.read_bytes()
        subscriber = daemon._registry.subscriber(WatchKey("example/project", 1), "one")
        assert subscriber is not None and subscriber.deadline is not None
        deadline = subscriber.deadline
        wait(lambda: time.monotonic() >= deadline)
        armed = True
        observe.set()
        if failure == "definite":
            assert failed.wait(2) and retry_waiting.wait(2)
            assert delivered == [] and paths.legacy_subscriptions.read_bytes() == original
            assert daemon._persistence_error == "state_write_failed"
            retry_allowed.set()
        wait(lambda: len(delivered) == 1 and not daemon.status()["subscribers"])
        wait(lambda: not daemon._pollers and not daemon.status()["in_flight"])
        payload, durable_at_delivery = delivered[0]
        assert payload["timed_out"] is True and payload["transitions"] == []
        assert "pr_state" not in payload
        if acknowledged:
            assert len(durable_at_delivery) == 1
            assert durable_at_delivery[0]["pending"]["payload"] == payload
            assert payload["registration_id"] == subscriber.registration_id
        else:
            assert durable_at_delivery == []
            assert "event_id" not in payload and "notification_protocol" not in payload
        assert len(delivered) == 1 and read_subscriptions_state() == []
        assert not daemon._registry.active_keys() and not daemon._registry.pending_events()
        assert failed.is_set() == (failure != "none")
    finally:
        observe.set()
        retry_allowed.set()
