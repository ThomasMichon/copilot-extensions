"""Explicit data-only expansion; synthetic contexts never post live feedback."""

from __future__ import annotations

import copy
from collections.abc import Callable
import json
from pathlib import Path
import time

import pytest

from test_review_executor import environment as environment

from agent_pull_requests.attention import Interest
from agent_pull_requests.attention_acquisition import AttentionInputs
from agent_pull_requests.attention_store import AttentionStore
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.feedback_capture import FeedbackCaptures, PROTOCOL
from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore
from agent_pull_requests.review_executor import execute_review
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain


@pytest.fixture
def capture_owner(environment: tuple) -> tuple:
    store, selected, generation, attachment, backend, _ = environment
    expires = int(time.time()) + 600
    ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    AttentionStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    ConversationStore(store).observe_workspace(
        generation, expires_at=expires, attention=AttentionInputs(
            generation["id"], (Interest("maintainer", True, "consumer-a"),),
            True, False, False, False, False,
        ),
    )
    operation = store.admit_review(selected, expires_at=expires)["operation"]
    record = json.loads(store.path.read_bytes())["conversations"]["records"][0]
    capture = {
        "operation_id": selected["operation_id"], "intent_id": operation["intent_id"],
        "generation_id": generation["id"], "source_id": selected["source_id"],
        "key": record["current"]["key"], "candidate": record["current"]["candidate"],
        "cursor": {"epoch": record["epoch"], "sequence": record["sequence"]},
        "observed_at": record["current"]["observed_to"],
        "interests": [{"consumer": "consumer-a", "role": "maintainer"}],
    }
    return store, selected, generation, attachment, backend, expires, capture


def seed_capture(store: ReplayStore, capture: dict, expires: int) -> None:
    # Persisted fixture data, not a protected capture-admission or handled API.
    store._review_transaction(
        lambda document: document["feedback_captures"]["records"].append(copy.deepcopy(capture)),
        expires_at=expires,
    )


@pytest.mark.parametrize("environment", [False, True], indirect=True)
def test_expansion_preserves_complete_owner_and_later_writes_cannot_drop_captures(capture_owner: tuple) -> None:
    store, selected, generation, attachment, backend, expires, capture = capture_owner
    before = json.loads(store.path.read_bytes())
    captures = FeedbackCaptureStore(store)
    assert captures.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires) == {
        "status": "adopted", "version": 5,
    }
    adopted = json.loads(store.path.read_bytes())
    assert {key: adopted[key] for key in before if key != "version"} == {
        **{key: value for key, value in before.items() if key not in ("version", "capacities")},
        "capacities": {**before["capacities"], "feedback_capture_bytes": 8 * 1024 * 1024},
    }
    seed_capture(store, capture, expires)
    expected = captures.snapshot(expires_at=expires)
    result = execute_review(
        store, selected, generation, attachment, context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires, budget_seconds=10,
    )
    assert result["status"] == "complete"
    unknown = copy.deepcopy(selected)
    unknown["operation_id"] = "unsettled-original"
    store.admit_review(unknown, expires_at=expires)
    store.begin_review_action(unknown["operation_id"], unknown["scope"], "finding", expires_at=expires)
    reviews = json.loads(store.path.read_bytes())["reviews"]
    scope = ConsumerScope("consumer-a", "account-a", "example/project")
    store.execute(scope, "register", {
        "request_id": "original", "issued_at": time.time() - 20, "number": 7,
        "until": ("merged",), "deadline_at": time.time() - 1,
    }, expires_at=expires)
    assert store.execute(scope, "pending", {}, expires_at=expires)["events"][0]["timed_out"]
    restarted = ReplayStore(
        store.root, store.namespace, clock=store.clock, service_identity=store.service_identity,
    )
    assert ConversationStore(restarted).observe_workspace(generation, expires_at=expires)["status"] == "unchanged"
    for adopter in (restarted.adopt_review_operations, ConversationStore(restarted).adopt,
                    AttentionStore(restarted).adopt, FeedbackCaptureStore(restarted).adopt):
        assert adopter(lifecycle_checkpoint=lambda: None, expires_at=expires) == {
            "status": "already_adopted", "version": 5,
        }
    assert FeedbackCaptureStore(restarted).snapshot(expires_at=expires) == expected
    assert json.loads(store.path.read_bytes())["reviews"] == reviews
    assert AttentionStore(restarted).snapshot(expires_at=expires) == before["attention"]
    detached = captures.snapshot(expires_at=expires)
    detached["records"][0]["interests"].clear()
    assert captures.snapshot(expires_at=expires) == expected
    unchanged = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="serving contract unavailable"):
        ConversationStore(restarted).capabilities(expires_at=expires)
    assert store.path.read_bytes() == unchanged


@pytest.mark.parametrize("replaced", [False, True])
def test_uncertain_expansion_reconciles_without_resetting_original_state(
    capture_owner: tuple, monkeypatch: pytest.MonkeyPatch, replaced: bool,
) -> None:
    import agent_pull_requests.watch_replay_store as module

    store, _, _, _, _, expires, capture = capture_owner
    before = json.loads(store.path.read_bytes())
    write = module._atomic_write_json

    def fail(path: Path, document: dict) -> None:
        if replaced:
            write(path, document)
            raise StateCommitUncertain("synthetic response lost after replacement")
        raise OSError("synthetic failure before replacement")

    monkeypatch.setattr(module, "_atomic_write_json", fail)
    with pytest.raises(StateCommitUncertain if replaced else OSError):
        FeedbackCaptureStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    monkeypatch.setattr(module, "_atomic_write_json", write)
    restarted = ReplayStore(store.root, store.namespace, clock=store.clock)
    result = FeedbackCaptureStore(restarted).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    assert result["status"] == ("already_adopted" if replaced else "adopted")
    seed_capture(restarted, capture, expires)
    after = json.loads(store.path.read_bytes())
    for field in ("replay", "reviews", "conversations", "attention"):
        assert after[field] == before[field]
    unchanged = store.path.read_bytes()
    with pytest.raises(ValueError, match="capacity differs"):
        FeedbackCaptureStore(restarted).adopt(
            lifecycle_checkpoint=lambda: None, expires_at=expires, capture_bytes=65536,
        )
    assert store.path.read_bytes() == unchanged


@pytest.mark.parametrize("failure", ["first_checkpoint", "last_checkpoint", "capacity", "total_capacity"])
def test_refused_expansion_never_replaces_existing_bytes(capture_owner: tuple, failure: str) -> None:
    store, _, _, _, _, expires, _ = capture_owner
    calls = []

    def lifecycle() -> None:
        calls.append(True)
        if len(calls) == (1 if failure == "first_checkpoint" else 2) and "checkpoint" in failure:
            raise PermissionError("synthetic lifecycle withdrawal")

    before = store.path.read_bytes()
    with pytest.raises((PermissionError, ReplayStoreInvalid)):
        FeedbackCaptureStore(store).adopt(
            lifecycle_checkpoint=lifecycle, expires_at=expires,
            capture_bytes=1 if failure == "capacity" else 256 * 1024 * 1024 if failure == "total_capacity"
            else 8 * 1024 * 1024,
        )
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("version", [2, 3])
def test_prior_layouts_are_not_implicitly_adopted(environment: tuple, version: int) -> None:
    store, _, _, _, _, _ = environment
    expires = int(time.time()) + 600
    if version == 3:
        ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="attention adoption first"):
        FeedbackCaptureStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    with pytest.raises(ReplayStoreInvalid, match="explicit owner adoption"):
        FeedbackCaptureStore(store).snapshot(expires_at=expires)
    assert store.path.read_bytes() == before
    store.execute(ConsumerScope("consumer-a", "account-a", "example/project"),
                  "capabilities", {}, expires_at=expires)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("mutation", [
    lambda document: document.update(feedback_captures=None),
    lambda document: document["feedback_captures"]["records"].append(
        copy.deepcopy(document["feedback_captures"]["records"][0]),
    ),
    lambda document: document["feedback_captures"]["records"][0].update(body="not metadata"),
    lambda document: document["feedback_captures"]["records"][0].update(operation_id="missing-original"),
    lambda document: document["feedback_captures"]["records"][0].update(generation_id="f" * 64),
    lambda document: document["feedback_captures"]["records"][0]["key"].update(namespace="foreign"),
    lambda document: document["feedback_captures"]["records"][0]["candidate"].update(head="f" * 40),
    lambda document: document["feedback_captures"]["records"][0]["cursor"].update(epoch=True),
    lambda document: document["feedback_captures"]["records"][0].update(observed_at=True),
    lambda document: document["feedback_captures"]["records"][0]["interests"].append(
        {"consumer": "consumer-a", "role": "maintainer"},
    ),
    lambda document: document.update(version=4),
])
def test_corrupt_captures_refuse_every_complete_envelope_entrypoint(
    capture_owner: tuple, mutation: Callable[[dict], object],
) -> None:
    store, selected, _, _, _, expires, capture = capture_owner
    captures = FeedbackCaptureStore(store)
    captures.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    seed_capture(store, capture, expires)
    document = json.loads(store.path.read_bytes())
    mutation(document)
    raw = json.dumps(document).encode()
    store.path.write_bytes(raw)
    for operation in (
        lambda: captures.snapshot(expires_at=expires),
        lambda: captures.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires),
        lambda: AttentionStore(store).snapshot(expires_at=expires),
        lambda: ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires),
        lambda: store.inspect_review(selected["operation_id"], selected["scope"], expires_at=expires),
        lambda: store.execute(ConsumerScope("consumer-a", "account-a", "example/project"),
                              "pending", {}, expires_at=expires),
    ):
        with pytest.raises(ReplayStoreInvalid):
            operation()
        assert store.path.read_bytes() == raw


def test_capture_count_and_bytes_refuse_without_eviction(
    capture_owner: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_pull_requests.feedback_capture as module

    store, _, _, _, _, expires, capture = capture_owner
    captures = FeedbackCaptureStore(store)
    captures.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires, capture_bytes=256)
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="reserved capacity"):
        seed_capture(store, capture, expires)
    assert store.path.read_bytes() == before
    monkeypatch.setattr(module, "MAX_OPERATIONS", 0)
    with pytest.raises(ValueError, match="persisted feedback"):
        FeedbackCaptures({"protocol": PROTOCOL, "records": [capture]})
    monkeypatch.setattr(module, "MAX_OPERATIONS", 1024)
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", 1)
    with pytest.raises(ValueError, match="persisted feedback"):
        FeedbackCaptures({"protocol": PROTOCOL, "records": [capture]})
