"""Completed repository reads remain useful when later work exhausts a cycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
import threading
import time
from typing import Any

import pytest

from agent_pull_requests import watch_observer as observation
from agent_pull_requests import repository_projection
from agent_pull_requests.repository_contract import RepositoryBackpressure, RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.repository_subscriptions import RepositorySubscriptions
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_observer import NativeObservationBinding, ReplayObserver
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayCallerExpired, ReplayStore

Document = dict[str, Any]
Key = tuple[str, str, int]


@dataclass
class Case:
    store: ReplayStore
    observer: ReplayObserver
    projection: RepositoryProjection
    identity: RepositoryIdentity
    clock: list[float]
    started: float
    numbers: list[int]
    reads: list[int]

    def snapshot(self, number: int) -> PRSnapshot:
        self.reads.append(number)
        return decode_snapshot({
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "provider": self.identity.provider, "api_base": self.identity.endpoint,
            "repo": self.identity.repo, "pr": number,
            "pr_state": "open" if number in self.numbers else "closed",
            "merged": False, "head_sha": "a" * 40, "base_sha": "b" * 40,
            "base_ref": "dev", "mergeable": True, "checks_state": "success",
            "checks_complete": True, "title": "Synthetic observation",
            "draft": False, "author": "writer", "reviews": [], "reviews_complete": True,
            "updated_at": datetime.fromtimestamp(self.started, timezone.utc).isoformat(),
        }, self.identity.repo, number)

    def query(self, number: int | None = None) -> Document:
        result: Document = self.projection.query(
            self.identity.key, number=number, expires_at=int(self.clock[0]) + 60,
        )
        return result


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Case:
    started = float(int(time.time()))
    clock = [started]
    identity = RepositoryIdentity("progress", "gitea", "https://forge.example", "reader", "owner/repo")
    store = ReplayStore(
        tmp_path, identity.namespace, clock=lambda: clock[0],
        repository_cache=True, repository_continuing=True, repository_activity=True,
    )
    store.provision(conversations=True)
    binding = NativeObservationBinding(
        identity.key, identity.repo, sys.executable, (sys.executable,),
        (sys.executable,), identity, str(tmp_path / "unused-native.json"),
    )
    observer = ReplayObserver(store, bindings=(binding,))
    assert isinstance(observer.repositories, RepositoryProjection)
    selected = Case(store, observer, observer.repositories, identity, clock, started, [1, 2], [])
    monkeypatch.setattr(observation, "qualify_native", lambda *args, **kwargs: None)
    monkeypatch.setattr(observation, "fetch_inventory", lambda *args, **kwargs: {
        "outcome": "complete", "numbers": list(selected.numbers), "pages": 1, "error": "",
    })
    monkeypatch.setattr(observation, "fetch_snapshot", lambda repo, number, command, **kwargs: (
        selected.snapshot(number)
    ))
    return selected


@pytest.mark.parametrize("registered", [False, True])
def test_completed_read_is_durable_before_a_later_read_expires(
    case: Case, monkeypatch: pytest.MonkeyPatch, registered: bool,
) -> None:
    deadline = int(case.started) + 60
    durable = threading.Event()
    threads: set[int] = set()
    original = case.projection.apply_facts
    if registered:
        original({case.identity.key: {
            "outcome": "complete", "numbers": case.numbers, "pages": 1, "error": "",
        }}, {}, observed_at=int(case.started) - 300, expires_at=deadline)
        for number in case.numbers:
            case.store.execute(ConsumerScope("watcher", case.identity.key, case.identity.repo), "register", {
                "request_id": f"watch-{number}", "issued_at": case.started, "number": number,
                "until": ("merged",), "deadline_at": case.started + 300,
            }, expires_at=deadline)

    def publish(
        inventories: dict[str, Document], snapshots: dict[Key, Document | None], **authority: Any,
    ) -> Document:
        if snapshots:
            assert case.clock[0] < deadline, "expired cycle was renewed to flush completed reads"
        result: Document = original(inventories, snapshots, **authority)
        if snapshots.get((case.identity.key, case.identity.repo, 1)) is not None:
            durable.set()
        return result

    def read(repo: str, number: int, command: str, **kwargs: Any) -> PRSnapshot | None:
        threads.add(threading.get_ident())
        if number == 1:
            return case.snapshot(number)
        durable.wait(5)
        case.clock[0] = deadline + 1
        return None

    monkeypatch.setattr(case.projection, "apply_facts", publish)
    monkeypatch.setattr(observation, "fetch_snapshot", read)
    with pytest.raises(ReplayCallerExpired):
        case.observer.cycle(expires_at=deadline)
    result = case.query()
    first = next((row for row in result["pulls"] if row["number"] == 1), None)
    assert result["inventory"]["outcome"] == "complete"
    assert result["inventory"]["observed_at"] == case.started - (300 if registered else 0)
    assert first is not None and first["facts"] is not None
    assert first["observed_at"] == case.started
    assert durable.is_set()
    assert not threads & {thread.ident for thread in threading.enumerate()}


def test_current_members_precede_bounded_unresolved_departures(case: Case) -> None:
    case.projection.apply_facts({case.identity.key: {
        "outcome": "complete", "numbers": list(range(1, 41)), "pages": 1, "error": "",
    }}, {}, expires_at=int(case.clock[0]) + 60)
    case.numbers[:] = [100, 101]
    case.observer.cycle(expires_at=int(case.clock[0]) + 60)
    assert all(case.query(number)["pulls"][0]["facts"] is not None for number in case.numbers)
    assert set(case.numbers) <= set(case.reads)
    assert len(case.reads) == len(case.numbers) + observation._MAX_READS
    unread = case.query(40)["pulls"][0]
    assert not unread["member"] and unread["facts"] is None


def test_successful_quiet_refresh_renews_facts_without_new_activity(case: Case) -> None:
    case.observer.cycle(expires_at=int(case.clock[0]) + 60)
    before = case.query()
    case.clock[0] += 300
    case.observer.cycle(expires_at=int(case.clock[0]) + 60)
    after = case.query()
    assert after["cursor"] == before["cursor"]
    assert [row["facts"] for row in after["pulls"]] == [row["facts"] for row in before["pulls"]]
    assert all(row["observed_at"] == case.clock[0] for row in after["pulls"])


def test_expired_first_read_does_not_restart_the_next_cycle_at_the_same_member(
    case: Case, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(observation, "_MAX_READS", 1)
    deadline = [int(case.clock[0]) + 60]

    def read(repo: str, number: int, command: str, **kwargs: Any) -> PRSnapshot:
        result = case.snapshot(number)
        if number == 1:
            case.clock[0] = deadline[0] + 1
        return result

    monkeypatch.setattr(observation, "fetch_snapshot", read)
    with pytest.raises(ReplayCallerExpired):
        case.observer.cycle(expires_at=deadline[0])
    assert case.reads == [1]
    deadline[0] = int(case.clock[0]) + 60
    with pytest.raises(ReplayCallerExpired):
        case.observer.cycle(expires_at=deadline[0])
    assert case.reads == [1, 2, 1]
    assert case.query(2)["pulls"][0]["facts"] is not None
    assert case.query(1)["pulls"][0]["facts"] is None


def test_member_and_departure_rotation_preserves_the_existing_read_bound(case: Case) -> None:
    case.projection.apply_facts({case.identity.key: {
        "outcome": "complete", "numbers": list(range(1, 41)), "pages": 1, "error": "",
    }}, {}, expires_at=int(case.clock[0]) + 60)
    case.numbers[:] = list(range(100, 140))
    for _ in range(2):
        case.observer.cycle(expires_at=int(case.clock[0]) + 60)
        case.clock[0] += 300
    assert len(case.reads) == 2 * observation._MAX_REPOSITORY_READS
    assert set(case.numbers) <= set(case.reads)
    departures = {number for number in case.reads if number < 100}
    assert len(departures) == 2 * observation._MAX_READS


def test_failed_refresh_keeps_old_facts_visibly_unavailable(
    case: Case, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case.observer.cycle(expires_at=int(case.clock[0]) + 60)
    before = case.query(1)["pulls"][0]
    case.clock[0] += 300
    monkeypatch.setattr(observation, "fetch_snapshot", lambda *args, **kwargs: None)
    result = case.observer.cycle(expires_at=int(case.clock[0]) + 60)
    after = case.query(1)["pulls"][0]
    assert result["unavailable"] == len(case.numbers)
    assert after["facts"] == before["facts"]
    assert after["observed_at"] == before["observed_at"]
    assert after["error"] == "snapshot_unavailable"


@pytest.mark.parametrize("inventoried", [False, True])
def test_snapshot_only_publication_cannot_invent_inventory_or_unknown_members(
    case: Case, inventoried: bool,
) -> None:
    deadline = int(case.clock[0]) + 60
    if inventoried:
        case.projection.apply_facts({case.identity.key: {
            "outcome": "complete", "numbers": case.numbers, "pages": 1, "error": "",
        }}, {}, expires_at=deadline)
    before = case.query()
    case.projection.apply({}, {
        (case.identity.key, case.identity.repo, 99): case.snapshot(99),
    }, expires_at=deadline)
    after = case.query()
    assert after["inventory"] == before["inventory"]
    assert after["pulls"] == before["pulls"]
    assert after["cursor"] == before["cursor"]


def test_pre_inventory_backpressure_does_not_starve_a_slower_terminal_wait(
    case: Case, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline = int(case.clock[0]) + 60
    case.projection.apply({case.identity.key: {
        "outcome": "complete", "numbers": case.numbers, "pages": 1, "error": "",
    }}, {
        (case.identity.key, case.identity.repo, number): case.snapshot(number)
        for number in case.numbers
    }, expires_at=deadline)
    original_cursor = case.query()["cursor"]
    monkeypatch.setattr(repository_projection, "MAX_EVENTS", original_cursor)
    RepositorySubscriptions(case.projection).execute(
        case.identity.key, "blocked-reader", "subscribe",
        {"request_id": "pinned-journal", "ttl": 600, "after": 0}, expires_at=deadline,
    )
    scope = ConsumerScope("watcher", case.identity.key, case.identity.repo)
    for number in case.numbers:
        case.store.execute(scope, "register", {
            "request_id": f"watch-{number}", "issued_at": case.started, "number": number,
            "until": ("merged",), "deadline_at": case.started + 300,
        }, expires_at=deadline)
    blocked = threading.Event()
    blocked_publications: list[bool] = []
    original = case.projection.apply_facts

    def publish(
        inventories: dict[str, Document], snapshots: dict[Key, Document | None], **authority: Any,
    ) -> Document:
        try:
            result: Document = original(inventories, snapshots, **authority)
            return result
        except RepositoryBackpressure:
            blocked_publications.append(bool(inventories))
            blocked.set()
            raise

    def read(repo: str, number: int, command: str, **kwargs: Any) -> PRSnapshot:
        if number == 2:
            assert blocked.wait(2)
        value = case.snapshot(number).native_snapshot
        assert value is not None
        return decode_snapshot({
            **value, "head_sha": "c" * 40,
            "pr_state": "closed" if number == 2 else "open", "merged": number == 2,
        }, repo, number)

    monkeypatch.setattr(case.projection, "apply_facts", publish)
    monkeypatch.setattr(observation, "fetch_snapshot", read)
    with pytest.raises(RepositoryBackpressure):
        case.observer.cycle(expires_at=deadline)
    pending = case.store.execute(scope, "pending", {}, expires_at=deadline)["events"]
    assert len(pending) == 1
    assert pending[0]["number"] == 2 and not pending[0]["timed_out"]
    assert pending[0]["transitions"] == ["merged"]
    assert blocked_publications == [False, True]
    assert case.query()["cursor"] == original_cursor
    assert case.query(2)["pulls"][0]["facts"]["pr_state"] == "open"


@pytest.mark.parametrize("outcome", ["partial", "unavailable"])
def test_incomplete_inventory_keeps_retained_members_in_the_full_snapshot_rotation(
    case: Case, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    case.numbers[:] = list(range(1, 41))
    case.projection.apply_facts({case.identity.key: {
        "outcome": "complete", "numbers": case.numbers, "pages": 1, "error": "",
    }}, {}, expires_at=int(case.clock[0]) + 60)
    monkeypatch.setattr(observation, "fetch_inventory", lambda *args, **kwargs: {
        "outcome": outcome, "numbers": [1, 2] if outcome == "partial" else [],
        "pages": 1 if outcome == "partial" else 0, "error": "read_unavailable",
    })
    for _ in range(2):
        case.observer.cycle(expires_at=int(case.clock[0]) + 60)
        case.clock[0] += 300
    assert len(case.reads) == 2 * observation._MAX_REPOSITORY_READS
    assert set(case.numbers) <= set(case.reads)
    retained = case.query(40)["pulls"][0]
    assert retained["member"] and retained["facts"] is not None
    assert case.query()["inventory"]["outcome"] == outcome
