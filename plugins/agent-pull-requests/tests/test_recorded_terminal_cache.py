"""Recorded body admission stays separate from original native observation custody."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from test_generation_associations import fresh, generation, publish, state
from test_retained_live_cache import selected, targets
from test_review_workspace import evidence as evidence

from agent_pull_requests import watch_replay_store
from agent_pull_requests import conversation_journal
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.terminal_retention import (
    TerminalAdmissionClosed, TerminalRetentionStore, bind_generation, replay_epoch, require_live,
)
from agent_pull_requests.watch_replay import ObservationTarget
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid

Document = dict[str, Any]


def terminal(root: Path) -> ReplayStore:
    return ReplayStore(
        root.resolve(), "owner", clock=lambda: 1, workspace_generation_associations=True,
        terminal_retention=True, terminal_recovery=True, terminal_cache_retirement=True,
        recorded_terminal_cache=True,
    )


def test_recorded_noncurrent_epochs_do_not_create_native_admission(
    tmp_path: Path, evidence: Document,
) -> None:
    original, cache = fresh(tmp_path)
    first = generation(evidence)
    second = generation(evidence, 2)
    publish(original, cache, first)
    publish(original, cache, second, first["id"])
    before = state(original)
    owner = terminal(tmp_path)
    TerminalRetentionStore(owner).adopt(
        retained_cache=cache, retained_targets=targets(first),
        lifecycle_checkpoint=lambda: None, expires_at=100,
    )
    TerminalRecoveryStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    TerminalCacheRetirementStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    document = state(owner)
    for field in ("replay", "conversations", "generation_associations"):
        assert document[field] == before[field]
    assert document["terminal_retention"]["admissions"] == []
    assert replay_epoch(document, ObservationTarget(
        "original-account", "example/project", first["scope"]["number"], frozenset(),
    )) is None
    for value in (first, second):
        assert require_live(
            document, value["scope"], generation_id=value["id"], recorded_body=True,
        ) == 1
        assert cache.lookup(value["scope"], generation_id=value["id"])["generation"] == value
        with pytest.raises(TerminalAdmissionClosed):
            bind_generation(document, value, {"epoch": 1, "sequence": 1})
        with pytest.raises(TerminalAdmissionClosed):
            require_live(document, value["scope"], generation_id=value["id"])
    with pytest.raises(ReplayStoreInvalid):
        original._review_transaction(lambda value: value, expires_at=100, persist=False)


@pytest.mark.parametrize("failure", ["pending", "missing", "incomplete", "wrong-scope"])
def test_unqualified_recorded_generation_refuses_without_changing_original_reads(
    tmp_path: Path, evidence: Document, failure: str,
) -> None:
    original, cache = fresh(tmp_path)
    value = generation(evidence)
    publish(original, cache, value)
    document = state(original)
    row = document["generation_associations"]["records"][0]
    if failure == "pending":
        row["publication"] = "pending"
    elif failure == "missing":
        document["generation_associations"]["records"] = []
    elif failure == "incomplete":
        row["complete"] = False
    else:
        row["scope"] = {**row["scope"], "principal_id": "different-principal"}
    # Wrong-scope corruption is rejected by the original envelope owner too.
    watch_replay_store._atomic_write_json(original.path, document)
    before = original.path.read_bytes()
    with pytest.raises(ValueError):
        TerminalRetentionStore(terminal(tmp_path)).adopt(
            retained_cache=cache, retained_targets=targets(value),
            lifecycle_checkpoint=lambda: None, expires_at=100,
        )
    assert original.path.read_bytes() == before
    assert cache.lookup(value["scope"])["generation"] == value


@pytest.mark.parametrize("uncertain", [False, True])
def test_recorded_adoption_retry_keeps_exact_partial_bindings(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    original, cache = fresh(tmp_path)
    value = generation(evidence)
    publish(original, cache, value)
    owner = terminal(tmp_path)
    write = watch_replay_store._atomic_write_json

    def interrupted(path: Path, document: Document) -> None:
        if uncertain:
            write(path, document)
        raise OSError("synthetic original adoption interruption")

    with monkeypatch.context() as patch:
        patch.setattr(watch_replay_store, "_atomic_write_json", interrupted)
        with pytest.raises(OSError):
            TerminalRetentionStore(owner).adopt(
                retained_cache=cache, retained_targets=targets(value),
                lifecycle_checkpoint=lambda: None, expires_at=100,
            )
    before = state(owner)
    TerminalRetentionStore(owner).adopt(
        retained_cache=cache, retained_targets=targets(value),
        lifecycle_checkpoint=lambda: None, expires_at=100,
    )
    after = state(owner)
    assert after["conversations"] == before["conversations"]
    assert after["terminal_retention"]["generations"] == [{
        "scope": value["scope"], "generation_id": value["id"], "epoch": 1,
    }]
    assert after["terminal_retention"]["admissions"] == []
    changed = copy.deepcopy(after)
    changed["conversations"]["records"][0]["epoch"] = 2
    with pytest.raises(TerminalAdmissionClosed):
        require_live(changed, value["scope"], generation_id=value["id"], recorded_body=True)


@pytest.mark.parametrize("history", ["terminal", "reopened", "native"])
def test_original_native_wait_or_closed_history_retains_its_original_reader(
    tmp_path: Path, evidence: Document, history: str,
) -> None:
    original, cache = fresh(tmp_path)
    value = generation(evidence)
    publish(original, cache, value)
    if history == "native":
        from agent_pull_requests.watch_replay import ConsumerScope
        original.execute(ConsumerScope("consumer", "original-account", "example/project"), "register", {
            "request_id": "original-native-wait", "issued_at": 1,
            "number": value["scope"]["number"], "until": ("head_changed",), "deadline_at": 50,
        }, expires_at=100)
    else:
        closed = copy.deepcopy(evidence)
        closed["sections"]["pull_request"]["data"]["status"] = "abandoned"
        ConversationStore(original).observe_workspace(generation(closed, 2), expires_at=100)
        if history == "reopened":
            ConversationStore(original).observe_workspace(generation(evidence, 4), expires_at=100)
    before = original.path.read_bytes()
    with pytest.raises(ValueError):
        TerminalRetentionStore(terminal(tmp_path)).adopt(
            retained_cache=cache, retained_targets=targets(value),
            lifecycle_checkpoint=lambda: None, expires_at=100,
        )
    assert original.path.read_bytes() == before
    assert cache.lookup(value["scope"])["generation"] == value


def test_completed_retry_indexes_full_cache_and_journal_once(
    tmp_path: Path, evidence: Document, monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 128
    original, cache = fresh(tmp_path, count=count)
    initial = generation(evidence)
    publish(original, cache, initial)
    document = state(original)
    association = document["generation_associations"]["records"][0]
    ids = [initial["id"]]
    previous = initial["id"]
    for index in range(count - 1):
        value = selected(evidence, offset=f"{200000 + index:06d}")
        assert cache.publish(value, expected_current=previous)["status"] == "published"
        row = {**copy.deepcopy(association), "generation_id": value["id"]}
        document["generation_associations"]["records"].append(row)
        ids.append(value["id"])
        previous = value["id"]
    record = document["conversations"]["records"][0]
    for index in range(count - 1):
        other = copy.deepcopy(record)
        other["current"]["key"]["number"] = index + 1000
        document["conversations"]["records"].append(other)
    watch_replay_store._atomic_write_json(original.path, document)
    owner = terminal(tmp_path)
    TerminalRetentionStore(owner).adopt(
        retained_cache=cache, retained_targets=targets(initial),
        lifecycle_checkpoint=lambda: None, expires_at=100,
    )
    TerminalRecoveryStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    TerminalCacheRetirementStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    before = owner.path.read_bytes()
    restore = conversation_journal.Observation.restore
    calls = 0

    def bounded(value: Document) -> Any:
        nonlocal calls
        calls += 1
        assert calls <= 8192, "completed retry repeated whole-journal validation per generation"
        return restore(value)

    monkeypatch.setattr(conversation_journal.Observation, "restore", staticmethod(bounded))
    TerminalRetentionStore(owner).adopt(
        retained_cache=cache, retained_targets=targets(initial),
        lifecycle_checkpoint=lambda: None, expires_at=100,
    )
    assert calls <= 8192
    assert owner.path.read_bytes() == before
    assert len(state(owner)["terminal_retention"]["generations"]) == len(ids) == cache.max_generations
