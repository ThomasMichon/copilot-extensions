"""Original native evidence is the only bridge across retained adoption."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from test_generation_native_targets import capture, fresh, publish
from test_generation_associations import generation, state
from test_review_workspace import evidence as evidence

from agent_pull_requests import generation_native_targets as native, watch_replay_store
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.terminal_retention import TerminalRetentionStore, replay_epoch, require_live
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid

Document = dict[str, Any]


def terminal(root: Path) -> ReplayStore:
    return ReplayStore(
        root.resolve(), "owner", clock=lambda: 1, workspace_generation_associations=True,
        native_generation_associations=True, recorded_terminal_cache=True,
        terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True,
    )


def targets(value: Document, account: str = "account-a") -> list[Document]:
    return [{
        "scope": {key: item for key, item in value["scope"].items() if key != "number"},
        "account": account, "repo": "example/repository",
    }]


def register(owner: ReplayStore, value: Document, *, account: str = "account-a",
             consumer: str = "consumer") -> Document:
    return owner.execute(ConsumerScope(consumer, account, "example/repository"), "register", {
        "request_id": "original-wait-" + consumer, "issued_at": 1,
        "number": value["scope"]["number"], "until": ("head_changed",), "deadline_at": 50,
    }, expires_at=100)


def adopt(owner: ReplayStore, cache: GenerationCache, value: Document) -> None:
    TerminalRetentionStore(owner).adopt(
        retained_cache=cache, retained_targets=targets(value),
        lifecycle_checkpoint=lambda: None, expires_at=100,
    )


def test_original_coalesced_waits_import_cursor_without_replay_or_cache_changes(
    tmp_path: Path, evidence: Document,
) -> None:
    original, cache = fresh(tmp_path)
    values = [generation(evidence), generation(evidence, 2)]
    previous = None
    for value in values:
        publish(original, cache, value, capture(original, value), previous)
        previous = value["id"]
    for consumer in ("first", "second"):
        register(original, values[0], consumer=consumer)
    before = state(original)
    old_target = original.observation_targets(expires_at=100)[0]
    assert len(old_target.registration_ids) == 2 and old_target.retention_epoch is None
    owner = terminal(tmp_path)
    adopt(owner, cache, values[0])
    TerminalRecoveryStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    TerminalCacheRetirementStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    after = state(owner)
    for field in ("replay", "conversations", "generation_associations", "reviews"):
        assert after[field] == before[field]
    assert after["recorded_terminal_cache_version"] == 2
    assert after["terminal_retention"]["admissions"] == [{
        "scope": values[0]["scope"],
        "cursor": before["generation_associations"]["records"][0]["cursor"],
        "target": before["generation_associations"]["records"][0]["native_target"],
    }]
    for value in values:
        assert cache.lookup(value["scope"], generation_id=value["id"])["generation"] == value
        assert require_live(after, value["scope"], generation_id=value["id"]) == 1
    assert replay_epoch(after, old_target) is not None
    before_bytes = owner.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="original imported admission"):
        native.capture(
            owner, values[0]["scope"], ("account-b", "example/repository"),
            expires_at=100, checkpoint=lambda: None,
        )
    assert owner.path.read_bytes() == before_bytes
    assert owner.observe(old_target, PRSnapshot(head_sha="c" * 40), expires_at=100) == {
        "status": "stale_observation",
    }
    assert state(owner)["replay"] == before["replay"]
    adopt(terminal(tmp_path), cache, values[0])
    assert state(owner) == after
    with pytest.raises(ReplayStoreInvalid):
        original._review_transaction(lambda _document: None, expires_at=100, persist=False)


@pytest.mark.parametrize("failure", ["current-b", "unmatched-a", "history-b", "pending", "missing"])
def test_unqualified_wait_or_historical_target_refuses_before_adoption(
    tmp_path: Path, evidence: Document, failure: str,
) -> None:
    original, cache = fresh(tmp_path)
    value = generation(evidence)
    captured = capture(original, value)
    if failure == "current-b":
        from dataclasses import replace
        captured = replace(captured, account="account-b")
    publish(original, cache, value, captured)
    register(original, value, account="account-z" if failure == "unmatched-a" else "account-a")
    if failure == "history-b":
        other = generation(evidence, 2)
        changed = native.capture(
            original, other["scope"], ("account-b", "example/repository"),
            expires_at=100, checkpoint=lambda: None,
        )
        publish(original, cache, other, changed, value["id"])
    if failure in ("pending", "missing"):
        document = state(original)
        if failure == "pending":
            document["generation_associations"]["records"][0]["publication"] = "pending"
        else:
            document["generation_associations"]["records"] = []
        watch_replay_store._atomic_write_json(original.path, document)
    before = original.path.read_bytes()
    with pytest.raises(ValueError):
        TerminalRetentionStore(terminal(tmp_path)).adopt(
            retained_cache=cache,
            retained_targets=targets(value, "account-b" if failure == "current-b" else "account-a"),
            lifecycle_checkpoint=lambda: None, expires_at=100,
        )
    assert original.path.read_bytes() == before
    assert cache.lookup(value["scope"], generation_id=value["id"])["generation"] == value


def test_shared_native_writer_keeps_full_scopes_but_adoption_refuses_ambiguity(
    tmp_path: Path, evidence: Document,
) -> None:
    original, cache = fresh(tmp_path)
    first = generation(evidence)
    other = copy.deepcopy(evidence)
    other["scope"]["principal_id"] = "another-reader"
    second = generation(other, 2)
    for value in (first, second):
        publish(original, cache, value, capture(original, value))
    rows = state(original)["generation_associations"]["records"]
    assert rows[0]["native_target"] == rows[1]["native_target"]
    assert rows[0]["scope"] != rows[1]["scope"]
    assert all(row["publication"] == "confirmed" for row in rows)
    before = original.path.read_bytes()
    with pytest.raises(ValueError):
        TerminalRetentionStore(terminal(tmp_path)).adopt(
            retained_cache=cache, retained_targets=targets(first) + targets(second),
            lifecycle_checkpoint=lambda: None, expires_at=100,
        )
    assert original.path.read_bytes() == before
    for value in (first, second):
        assert cache.lookup(value["scope"])["generation"] == value


@pytest.mark.parametrize("failure", ["capacity", "expired"])
def test_native_admission_reserves_complete_capacity_and_original_deadline_before_commit(
    tmp_path: Path, evidence: Document, failure: str,
) -> None:
    original, cache = fresh(tmp_path)
    value = generation(evidence)
    publish(original, cache, value, capture(original, value))
    register(original, value)
    owner = terminal(tmp_path)
    before = original.path.read_bytes()
    calls = 0

    def current() -> None:
        nonlocal calls
        calls += 1
        if failure == "expired" and calls == 8:
            owner.clock = lambda: 101

    with pytest.raises((ValueError, PermissionError)):
        TerminalRetentionStore(owner).adopt(
            retained_cache=cache, retained_targets=targets(value),
            retention_bytes=128 if failure == "capacity" else 8 * 1024 * 1024,
            lifecycle_checkpoint=current, checkpoint=current, expires_at=100,
        )
    assert original.path.read_bytes() == before
    assert cache.lookup(value["scope"])["generation"] == value
