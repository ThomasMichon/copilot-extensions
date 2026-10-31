"""Actual single-store adoption/intent/receipt consumers, with no backend effects."""

import copy
import json
import threading
import time

import pytest

from agent_pull_requests import watch_replay_store as storage
from agent_pull_requests.review_workspace import _digest
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid, ReplayServiceIdentity
from agent_pull_requests.watch_storage import StateCommitUncertain

SCOPE = {
    "provider": "azure-devops", "endpoint": "https://dev.azure.com/example",
    "project_id": "project", "repository_id": "repository", "principal_id": "native-principal",
    "number": 42,
}
CONSUMER = ConsumerScope("synthetic-consumer", "synthetic-account", "example/project")


def expires():
    return int(time.time()) + 600


def selected(operation_id="selected-review"):
    return {
        "schema": "agent-pull-requests.review-intent", "version": 1,
        "operation_id": operation_id, "scope": copy.deepcopy(SCOPE),
        "generation_id": "1" * 64, "source_id": "2" * 64,
        "revision": {"head_sha": "a" * 40, "common_sha": "b" * 40, "iteration": 3},
        "actions": [{"id": "comment-a", "kind": "comment", "body": "Synthetic selected finding"},
                    {"id": "verdict-a", "kind": "verdict", "verdict": "APPROVED"}],
    }


@pytest.fixture
def store(tmp_path):
    result = ReplayStore(tmp_path.resolve(), "existing-owner-namespace")
    result.provision()
    result.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=expires())
    return result


def response(**ids):
    return {"origin": "response", "platform_ids": ids, "proof": None}


@pytest.mark.parametrize("service", [False, True])
def test_explicit_adoption_preserves_replay_and_owner_binding(tmp_path, service):
    identity = ReplayServiceIdentity("fleet", "gateway", "slot") if service else None
    store = ReplayStore(tmp_path.resolve(), "existing-owner-namespace", service_identity=identity)
    store.provision()
    before = store._decode_legacy(json.loads(store.path.read_text()))
    with pytest.raises(ReplayStoreInvalid, match="adoption"):
        store.admit_review(selected(), expires_at=expires())
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=expires())
    assert store._decode(json.loads(store.path.read_text())) == before
    store.admit_review(selected(), expires_at=expires())
    restarted = ReplayStore(tmp_path.resolve(), store.namespace, service_identity=identity)
    assert restarted.inspect_review("selected-review", SCOPE, expires_at=expires())["intent"] == selected()
    assert restarted.adopt_review_operations(
        lifecycle_checkpoint=lambda: None, expires_at=expires(),
    )["status"] == "already_adopted"


def test_live_v1_reader_prevents_conversion_until_existing_owner_drains(tmp_path):
    class LegacyReader(ReplayStore):
        def _decode(self, state):
            return self._decode_legacy(state)

    store = ReplayStore(tmp_path.resolve(), "existing-owner-namespace")
    store.provision()
    legacy = LegacyReader(tmp_path.resolve(), store.namespace)
    active, stop = threading.Event(), threading.Event()
    failures = []

    def reader():
        try:
            legacy.observation_targets(expires_at=expires())
            active.set()
            stop.wait(10)
            legacy.observation_targets(expires_at=expires())
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=reader)
    worker.start()
    try:
        assert active.wait(5)
        before = store.path.read_bytes()

        def lifecycle():
            if worker.is_alive():
                raise RuntimeError("existing v1 reader has not drained")

        with pytest.raises(RuntimeError, match="drained"):
            store.adopt_review_operations(lifecycle_checkpoint=lifecycle, expires_at=expires())
        assert store.path.read_bytes() == before
    finally:
        stop.set()
        worker.join(10)
    assert not worker.is_alive() and failures == []
    store.adopt_review_operations(lifecycle_checkpoint=lifecycle, expires_at=expires())
    with pytest.raises(ReplayStoreInvalid):
        legacy.observation_targets(expires_at=expires())
    assert store.observation_targets(expires_at=expires()) == ()


def test_registration_transactions_preserve_admitted_review_and_vice_versa(store):
    store.admit_review(selected(), expires_at=expires())
    before = store.inspect_review("selected-review", SCOPE, expires_at=expires())
    store.execute(CONSUMER, "register", {
        "request_id": "watch-request", "issued_at": time.time(), "number": 42, "until": ("merged",),
    }, expires_at=expires())
    assert store.inspect_review("selected-review", SCOPE, expires_at=expires()) == before
    replay = json.loads(store.path.read_text())["replay"]
    state = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    store.record_review_receipt("selected-review", SCOPE, "comment-a", state["action"]["attempt_id"],
                                response(thread_id=20, comment_id=1), expires_at=expires())
    assert json.loads(store.path.read_text())["replay"] == replay
    assert store.observation_targets(expires_at=expires())


def test_idempotent_intent_and_response_receipt_never_repeat_a_selected_effect(store):
    assert store.admit_review(selected(), expires_at=expires())["status"] == "admitted"
    assert store.admit_review(selected(), expires_at=expires())["status"] == "existing"
    altered = selected()
    altered["actions"][0]["body"] = "Different selection"
    with pytest.raises(ValueError, match="different immutable"):
        store.admit_review(altered, expires_at=expires())
    effects = []
    begun = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    assert begun["new_attempt"]
    assert store.inspect_review("selected-review", SCOPE, expires_at=expires())["actions"][0]["state"] == "unknown"
    effects.append("synthetic-backend-comment")
    store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                response(thread_id=20, comment_id=1), expires_at=expires())
    repeated = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    assert not repeated["new_attempt"] and repeated["status"] == "confirmed"
    assert effects == ["synthetic-backend-comment"]
    assert store.begin_review_action("selected-review", SCOPE, "verdict-a", expires_at=expires())["new_attempt"]


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_inflight_persistence_performs_no_effect_or_blind_retry(store, monkeypatch, uncertain):
    store.admit_review(selected(), expires_at=expires())
    original = storage._atomic_write_json

    def fail(path, value):
        if uncertain:
            original(path, value)
            raise StateCommitUncertain("controlled persistence uncertainty")
        raise OSError("controlled pre-replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_atomic_write_json", fail)
        with pytest.raises(OSError):
            store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    state = store.inspect_review("selected-review", SCOPE, expires_at=expires())["actions"][0]
    assert state["state"] == ("unknown" if uncertain else "unattempted")
    result = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    assert result["new_attempt"] is not uncertain


def test_unknown_vote_value_is_not_attributed_but_matching_native_event_can_be_retained(store):
    store.admit_review(selected(), expires_at=expires())
    begun = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    operation = store.inspect_review("selected-review", SCOPE, expires_at=expires())
    proof = {"operation_id": "selected-review", "action_id": "comment-a",
             "scope_id": _digest(SCOPE), "intent_id": operation["intent_id"]}
    current_value = {"origin": "attributable_readback", "platform_ids": {"reviewer_id": "native-principal"},
                     "proof": proof}
    with pytest.raises(ValueError, match="attribution"):
        store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                    current_value, expires_at=expires())
    assert not store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())["new_attempt"]
    current_value["platform_ids"]["event_id"] = "synthetic-action-event"
    current_value["platform_ids"].update(thread_id=20, comment_id=1)
    confirmed = store.record_review_receipt("selected-review", SCOPE, "comment-a",
                                           begun["action"]["attempt_id"], current_value, expires_at=expires())
    assert confirmed["state"] == "confirmed"


def test_reserved_receipts_and_replay_keep_working_when_review_admission_is_full(tmp_path):
    store = ReplayStore(tmp_path.resolve(), "existing-owner-namespace")
    store.provision()
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=expires(), review_bytes=13000)
    store.admit_review(selected(), expires_at=expires())
    with pytest.raises(ValueError, match="capacity"):
        store.admit_review(selected("another-review"), expires_at=expires())
    begun = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    result = store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                        response(thread_id=20, comment_id=1), expires_at=expires())
    assert result["state"] == "confirmed"
    assert store.execute(CONSUMER, "pending", {}, expires_at=expires()) == {"events": [], "more": False}


def test_owner_loss_after_synthetic_effect_preserves_unknown_and_original_denial(store):
    store.admit_review(selected(), expires_at=expires())
    begun = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    denied = PermissionError("existing current owner withdrawn")

    def checkpoint():
        raise denied

    with pytest.raises(PermissionError) as error:
        store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                    response(thread_id=20, comment_id=1), expires_at=expires(),
                                    checkpoint=checkpoint)
    assert error.value is denied
    assert store.inspect_review("selected-review", SCOPE, expires_at=expires())["actions"][0]["state"] == "unknown"


def test_receipt_cannot_confirm_an_action_without_its_durable_inflight_fence(store):
    store.admit_review(selected(), expires_at=expires())
    with pytest.raises(ValueError, match="admitted attempt"):
        store.record_review_receipt("selected-review", SCOPE, "comment-a", None,
                                    response(thread_id=20, comment_id=1), expires_at=expires())
    assert store.inspect_review("selected-review", SCOPE, expires_at=expires())["actions"][0]["state"] == "unattempted"


def test_partial_native_response_retains_known_ids_without_confirming_or_repeating(store):
    store.admit_review(selected(), expires_at=expires())
    begun = store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    result = store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                        response(thread_id=20), expires_at=expires())
    assert result["state"] == "unknown" and result["receipt"]["platform_ids"] == {"thread_id": 20}
    assert not store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())["new_attempt"]
    assert store.begin_review_action("selected-review", SCOPE, "verdict-a", expires_at=expires())["status"] == "blocked_by_previous"
    with pytest.raises(ValueError, match="retained native"):
        store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                    response(thread_id=21, comment_id=1), expires_at=expires())
    completed = store.record_review_receipt("selected-review", SCOPE, "comment-a", begun["action"]["attempt_id"],
                                           response(comment_id=1), expires_at=expires())
    assert completed["state"] == "confirmed"
    assert completed["receipt"]["platform_ids"] == {"thread_id": 20, "comment_id": 1}


def test_replay_admission_reserves_large_pending_growth_before_acceptance(tmp_path):
    from agent_pull_requests.watch_contract import PRSnapshot
    store = ReplayStore(tmp_path.resolve(), "existing-owner-namespace")
    store.provision()
    store.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=expires(), replay_bytes=30000)
    request = {"request_id": "watch", "issued_at": time.time(), "number": 42, "until": ("merged",)}
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="reserved capacity"):
        store.execute(CONSUMER, "register", request, expires_at=expires())
    assert store.path.read_bytes() == before
    larger = ReplayStore((tmp_path / "larger").resolve(), "existing-owner-namespace")
    larger.provision()
    larger.adopt_review_operations(lifecycle_checkpoint=lambda: None, expires_at=expires(),
                                   replay_bytes=90000, review_bytes=13000)
    larger.admit_review(selected(), expires_at=expires())
    with pytest.raises(ValueError, match="capacity"):
        larger.admit_review(selected("capacity-refusal"), expires_at=expires())
    larger.execute(CONSUMER, "register", request, expires_at=expires())
    target = larger.observation_targets(expires_at=expires())[0]
    larger.observe(target, PRSnapshot(merged=True, review_decision="x" * 18000), expires_at=expires())
    pending = larger.execute(CONSUMER, "pending", {}, expires_at=expires())
    assert len(pending["events"]) == 1
    event = pending["events"][0]
    larger.execute(CONSUMER, "acknowledge", {
        "request_id": request["request_id"], "issued_at": request["issued_at"],
        "registration_id": event["registration_id"], "event_id": event["event_id"],
    }, expires_at=expires())
    assert larger.execute(CONSUMER, "pending", {}, expires_at=expires())["events"] == []
    assert larger.inspect_review("selected-review", SCOPE, expires_at=expires())["intent"] == selected()


def test_comment_only_selection_does_not_admit_a_nonexistent_vote_action(store):
    selection = selected()
    before = store.path.read_bytes()
    selection["actions"][1]["verdict"] = "COMMENTED"
    with pytest.raises(ValueError, match="no vote action"):
        store.admit_review(selection, expires_at=expires())
    assert store.path.read_bytes() == before
    selection["actions"].pop()
    assert store.admit_review(selection, expires_at=expires())["status"] == "admitted"


def test_cache_rebuild_cannot_erase_admitted_unknown_review_in_same_owner_root(store):
    from pathlib import Path
    from agent_pull_requests.generation_cache import GenerationCache
    from agent_pull_requests.review_workspace import build_generation
    store.admit_review(selected(), expires_at=expires())
    store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    before = store.path.read_bytes()
    cache = GenerationCache(store.root, store.namespace, max_bytes=100000, max_generations=10)
    cache.provision()
    example = Path(__file__).resolve().parents[1] / "docs" / "review-evidence.example.json"
    generation = build_generation(json.loads(example.read_text()))["generation"]
    assert cache.publish(generation, expected_current=None)["status"] == "published"
    cache.rebuild()
    assert store.path.read_bytes() == before
    assert store.inspect_review("selected-review", SCOPE, expires_at=expires())["actions"][0]["state"] == "unknown"
