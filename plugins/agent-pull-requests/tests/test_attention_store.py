"""Explicit data adoption in isolated owned state; no service or provider adoption."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import asdict, replace
import json
from pathlib import Path
import time

import pytest

from test_conversation_journal import observation, owner as owner
from test_review_executor import environment as environment

from agent_pull_requests.attention import Candidate, Cursor, Facts, Handled, Interest
from agent_pull_requests.attention_state import AttentionState, PROTOCOL
from agent_pull_requests.attention_store import AttentionStore
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.review_executor import execute_review
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain


def record() -> dict:
    value = observation()
    return {
        "facts": asdict(Facts(value.key, value.candidate, Cursor(1, 1), 101, 100, True)),
        "interest": asdict(Interest("maintainer", True, "consumer-a")),
        "handled": None,
        "provenance": None,
    }


def state(*records: dict) -> dict:
    return {"protocol": PROTOCOL, "records": list(records)}


@pytest.mark.parametrize("environment", [False, True], indirect=True)
def test_adoption_preserves_actual_confirmed_unknown_replay_and_journal_state(environment: tuple) -> None:
    store, selected, generation, attachment, backend, _ = environment
    expires = int(time.time()) + 600
    result = execute_review(
        store, selected, generation, attachment, context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires, budget_seconds=10,
    )
    assert result["status"] == "complete"
    unknown = copy.deepcopy(selected)
    unknown["operation_id"] = "unsettled-original"
    store.admit_review(unknown, expires_at=expires)
    store.begin_review_action(
        unknown["operation_id"], unknown["scope"], "finding", expires_at=expires,
    )
    scope = ConsumerScope("consumer-a", "account-a", "example/project")
    store.execute(scope, "register", {
        "request_id": "original", "issued_at": time.time() - 20, "number": 7,
        "until": ("merged",), "deadline_at": time.time() - 1,
    }, expires_at=expires)
    conversations = ConversationStore(store)
    conversations.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    value = observation(key=replace(observation().key, namespace=store.namespace))
    conversations.observe(value, expected=None, expires_at=expires)
    before = json.loads(store.path.read_bytes())
    attention = AttentionStore(store)
    assert attention.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires) == {
        "status": "adopted", "version": 4,
    }
    after = json.loads(store.path.read_bytes())
    for field in ("namespace", "service", "replay", "reviews", "conversations"):
        assert after[field] == before[field]
    assert after["capacities"] == {**before["capacities"], "attention_bytes": 8 * 1024 * 1024}
    assert attention.snapshot(expires_at=expires) == state()
    assert store.execute(scope, "pending", {}, expires_at=expires)["events"][0]["timed_out"]
    conversations.observe(
        replace(value, observed_from=102, observed_to=103), expected=Cursor(1, 1),
        expires_at=expires,
    )
    assert conversations.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires) == {
        "status": "already_adopted", "version": 4,
    }
    assert json.loads(store.path.read_bytes())["reviews"] == before["reviews"]
    assert store.inspect_review(
        unknown["operation_id"], unknown["scope"], expires_at=expires,
    )["actions"][0]["state"] == "unknown"
    unchanged = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="serving contract unavailable"):
        conversations.capabilities(expires_at=expires)
    assert store.path.read_bytes() == unchanged


def test_attention_reads_never_adopt_and_v2_requires_prior_explicit_conversation_adoption(owner: ReplayStore) -> None:
    before = owner.path.read_bytes()
    attention = AttentionStore(owner)
    with pytest.raises(ReplayStoreInvalid, match="explicit owner adoption"):
        attention.snapshot(expires_at=200)
    with pytest.raises(ReplayStoreInvalid, match="conversation adoption first"):
        attention.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    assert owner.path.read_bytes() == before
    ConversationStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    original = owner.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="explicit owner adoption"):
        attention.snapshot(expires_at=200)
    assert owner.path.read_bytes() == original


@pytest.mark.parametrize("fail_at", [1, 2])
def test_lifecycle_withdrawal_never_publishes_attention_adoption(owner: ReplayStore, fail_at: int) -> None:
    ConversationStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    before = owner.path.read_bytes()
    calls = []

    def lifecycle() -> None:
        calls.append(True)
        if len(calls) == fail_at:
            raise PermissionError("synthetic custody withdrawal")

    with pytest.raises(PermissionError):
        AttentionStore(owner).adopt(lifecycle_checkpoint=lifecycle, expires_at=200)
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("replaced", [False, True])
def test_uncertain_adoption_reconciles_original_layout_without_reset(
    owner: ReplayStore, monkeypatch: pytest.MonkeyPatch, replaced: bool,
) -> None:
    import agent_pull_requests.watch_replay_store as module

    ConversationStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    original = json.loads(owner.path.read_bytes())
    write = module._atomic_write_json

    def fail(path: Path, document: dict) -> None:
        if replaced:
            write(path, document)
            raise StateCommitUncertain("synthetic post-replace failure")
        raise OSError("synthetic pre-replace failure")

    monkeypatch.setattr(module, "_atomic_write_json", fail)
    with pytest.raises(StateCommitUncertain if replaced else OSError):
        AttentionStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    monkeypatch.setattr(module, "_atomic_write_json", write)
    restarted = ReplayStore(owner.root, owner.namespace, clock=owner.clock)
    result = AttentionStore(restarted).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    assert result["status"] == ("already_adopted" if replaced else "adopted")
    assert json.loads(owner.path.read_bytes())["conversations"] == original["conversations"]
    before = owner.path.read_bytes()
    assert AttentionStore(restarted).snapshot(expires_at=200) == state()
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("capacity", [True, 1, 2**40])
def test_invalid_or_unreserved_capacity_cannot_replace_the_existing_owner(owner: ReplayStore, capacity: int) -> None:
    ConversationStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    before = owner.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="capacity"):
        AttentionStore(owner).adopt(
            lifecycle_checkpoint=lambda: None, expires_at=200, attention_bytes=capacity,
        )
    assert owner.path.read_bytes() == before


def test_existing_adoption_refuses_capacity_change_and_snapshot_is_detached(owner: ReplayStore) -> None:
    ConversationStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    attention = AttentionStore(owner)
    attention.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    owner._review_transaction(
        lambda document: document.update(attention=state(record())), expires_at=200,
    )
    before = owner.path.read_bytes()
    detached = attention.snapshot(expires_at=200)
    detached["records"][0]["interest"]["consumer"] = "changed"
    assert attention.snapshot(expires_at=200) == state(record())
    with pytest.raises(ValueError, match="capacity differs"):
        attention.adopt(lifecycle_checkpoint=lambda: None, expires_at=200, attention_bytes=65536)
    assert owner.path.read_bytes() == before


@pytest.mark.parametrize("mutation", [
    lambda document: document.update(attention=None),
    lambda document: document["attention"]["records"].append(record()),
    lambda document: document["attention"]["records"][0]["facts"]["key"].update(namespace="foreign"),
    lambda document: document["attention"]["records"][0].update(body="not metadata"),
    lambda document: document["attention"]["records"][0]["facts"]["cursor"].update(epoch=True),
    lambda document: document.update(version=5),
])
def test_corrupt_attention_blocks_all_owner_paths_without_reinitializing(
    owner: ReplayStore, mutation: Callable[[dict], object],
) -> None:
    conversations = ConversationStore(owner)
    conversations.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    attention = AttentionStore(owner)
    attention.adopt(lifecycle_checkpoint=lambda: None, expires_at=200)
    document = json.loads(owner.path.read_bytes())
    document["attention"] = state(record())
    mutation(document)
    raw = json.dumps(document).encode()
    owner.path.write_bytes(raw)
    for operation in (
        lambda: attention.snapshot(expires_at=200),
        lambda: attention.adopt(lifecycle_checkpoint=lambda: None, expires_at=200),
        lambda: conversations.events(observation().key, after=None, limit=1, expires_at=200),
        lambda: owner.execute(
            ConsumerScope("consumer-a", "account-a", "example/project"),
            "pending", {}, expires_at=200,
        ),
    ):
        with pytest.raises(ReplayStoreInvalid):
            operation()
        assert owner.path.read_bytes() == raw


def test_handled_data_requires_matching_interest_candidate_and_original_receipt_references() -> None:
    row = record()
    value = observation()
    row["handled"] = asdict(Handled(
        value.key, value.candidate, Cursor(1, 1), "consumer-a", "maintainer",
    ))
    row["provenance"] = {
        "operation_id": "original", "generation_id": "a" * 64,
        "receipt_refs": [{"action_id": "finding", "attempt_id": "original-attempt"}],
    }
    assert AttentionState(state(row)).snapshot() == state(row)
    for changed in (
        {**row, "provenance": None},
        {**row, "handled": {**row["handled"], "consumer": "other"}},
        {**row, "handled": {**row["handled"], "cursor": {"epoch": 1, "sequence": 2}}},
        {**row, "handled": {**row["handled"], "candidate": asdict(Candidate("c" * 40, "b" * 40))}},
        {**row, "provenance": {**row["provenance"], "receipt_refs": []}},
    ):
        with pytest.raises(ValueError, match="persisted attention"):
            AttentionState(state(changed))


def test_record_count_and_byte_limits_refuse_instead_of_evicting(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_pull_requests.attention_state as module

    first, second = record(), record()
    second["interest"]["consumer"] = "consumer-b"
    monkeypatch.setattr(module, "MAX_INTERESTS", 1)
    with pytest.raises(ValueError):
        AttentionState(state(first, second))
    monkeypatch.setattr(module, "MAX_RECORD_BYTES", 1)
    with pytest.raises(ValueError):
        AttentionState(state(first))
