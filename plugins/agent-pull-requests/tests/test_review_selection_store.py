"""Real-file pre-approval retention, distinct from execution and native effects."""

from __future__ import annotations

import copy
import importlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from single_instance_lease import OperationLockedError

from agent_pull_requests import review_intent, watch_replay_store as storage
from agent_pull_requests.review_selection_store import ReviewSelectionStore
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain
from test_review_owner_store import (
    SCOPE, expires, response, selected,
    prepared_intent as prepared_intent, store as store,
)


def original(prepared: dict) -> dict:
    value = copy.deepcopy(prepared)
    value["version"] = 1
    for action in value["actions"]:
        action.pop("preparation", None)
        if "body" in action:
            action["body"] = "Synthetic selected finding"
    return value


def adopt(store: ReplayStore) -> ReviewSelectionStore:
    store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires())
    selections = ReviewSelectionStore(store)
    selections.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    return selections


def test_retention_recovery_and_exact_admission_remain_distinct(store: ReplayStore, prepared_intent: dict) -> None:
    selections = adopt(store)
    draft = original(prepared_intent)
    assert selections.recover(draft, expires_at=expires()) is None
    retained = selections.retain(draft, prepared_intent, expires_at=expires())["operation"]
    before = store.path.read_bytes()
    restarted = ReplayStore(store.root, store.namespace)
    assert ReviewSelectionStore(restarted).recover(draft, expires_at=expires()) == retained
    assert store.path.read_bytes() == before
    for read_or_write in (
        lambda: restarted.inspect_review(draft["operation_id"], SCOPE, expires_at=expires()),
        lambda: restarted.begin_review_action(draft["operation_id"], SCOPE, "comment-a", expires_at=expires()),
        lambda: restarted.prepared_review_receipt(draft["operation_id"], SCOPE, "comment-a", expires_at=expires()),
        lambda: restarted.record_review_receipt(
            draft["operation_id"], SCOPE, "comment-a", "invented",
            response(thread_id=20, comment_id=1), expires_at=expires(),
        ),
    ):
        with pytest.raises(ValueError, match="not execution-admitted"):
            read_or_write()
    assert store.path.read_bytes() == before
    admitted = restarted.admit_review(prepared_intent, expires_at=expires())["operation"]
    assert admitted == {**retained, "selection": {**retained["selection"], "stage": "admitted"}}
    begun = restarted.begin_review_action(draft["operation_id"], SCOPE, "comment-a", expires_at=expires())
    restarted.record_review_receipt(
        draft["operation_id"], SCOPE, "comment-a", begun["action"]["attempt_id"],
        response(thread_id=20, comment_id=1), expires_at=expires(),
    )
    recovered = selections.recover(draft, expires_at=expires())
    assert recovered is not None
    assert recovered["actions"][0]["state"] == "confirmed"
    assert recovered["intent"] == prepared_intent
    assert not restarted.begin_review_action(
        draft["operation_id"], SCOPE, "comment-a", expires_at=expires(),
    )["new_attempt"]


@pytest.mark.parametrize("change", ["body", "target", "source", "scope", "action", "revision"])
def test_changed_original_or_admission_cannot_replace_retained_selection(
    store: ReplayStore, prepared_intent: dict, change: str,
) -> None:
    selections = adopt(store)
    draft = original(prepared_intent)
    selections.retain(draft, prepared_intent, expires_at=expires())
    changed = copy.deepcopy(draft)
    if change == "body":
        changed["actions"][0]["body"] += " Changed."
    elif change == "target":
        changed["actions"][0].update(kind="reply", thread_id=21)
    elif change == "source":
        changed["source_id"] = "9" * 64
    elif change == "scope":
        changed["scope"]["number"] += 1
    elif change == "revision":
        changed["revision"]["iteration"] += 1
    else:
        changed["actions"][0]["id"] = "another-action"
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="different original selection"):
        selections.recover(changed, expires_at=expires())
    changed_final = copy.deepcopy(prepared_intent)
    changed_final["source_id"] = "9" * 64
    with pytest.raises(ValueError, match="different immutable intent"):
        store.admit_review(changed_final, expires_at=expires())
    assert store.path.read_bytes() == before


def test_same_key_race_keeps_one_final_body_and_original_origin(
    store: ReplayStore, prepared_intent: dict,
) -> None:
    selections = adopt(store)
    draft = original(prepared_intent)
    alternate = copy.deepcopy(prepared_intent)
    identity = copy.deepcopy(alternate["actions"][0]["preparation"]["identity"])
    identity["source"]["origin"]["codename"] = "changed-origin"
    preparation = importlib.import_module("comment_preparation").prepare(
        "Different profile presentation.", importlib.import_module("attribution_profile").validate({"version": 1}),
        {"domain": "forge", "visibility": "public", "activity": "comment", "required": True},
        identity["source"], comment_id=identity["comment_id"], write_id=identity["write_id"],
    )
    alternate["actions"][0].update(
        body=preparation["body"],
        preparation={"identity": preparation["identity"], "body_id": preparation["body_id"]},
    )
    def retain(final: dict) -> dict:
        try:
            return selections.retain(draft, final, expires_at=expires())
        except OperationLockedError:
            return {"status": "contended"}

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(retain, (prepared_intent, alternate)))
    assert sorted(result["status"] for result in results) in (
        ["contended", "prepared"], ["existing", "prepared"],
    )
    committed = next(result["operation"] for result in results if result["status"] == "prepared")
    assert all(result.get("operation", committed) == committed for result in results)
    assert selections.retain(draft, alternate, expires_at=expires()) == {"status": "existing", "operation": committed}
    assert len(json.loads(store.path.read_bytes())["reviews"]) == 1
    assert selections.recover(draft, expires_at=expires()) == committed


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_or_lost_retention_response_reconciles_original_key(
    store: ReplayStore, prepared_intent: dict, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    selections = adopt(store)
    draft = original(prepared_intent)
    write = storage._atomic_write_json
    before = store.path.read_bytes()

    def fail(path, value) -> None:
        if uncertain:
            write(path, value)
            raise StateCommitUncertain("synthetic lost result")
        raise OSError("synthetic pre-replace failure")

    monkeypatch.setattr(storage, "_atomic_write_json", fail)
    with pytest.raises((OSError, StateCommitUncertain)):
        selections.retain(draft, prepared_intent, expires_at=expires())
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    recovered = ReviewSelectionStore(ReplayStore(store.root, store.namespace)).recover(draft, expires_at=expires())
    assert (recovered is not None) is uncertain
    if not uncertain:
        assert store.path.read_bytes() == before
    result = selections.retain(draft, prepared_intent, expires_at=expires())
    assert result["status"] == ("existing" if uncertain else "prepared")
    assert result["operation"]["intent"] == prepared_intent
    assert all(row["state"] == "unattempted" for row in result["operation"]["actions"])


def test_lifecycle_and_old_reader_refusal_preserve_existing_unknowns(
    store: ReplayStore, prepared_intent: dict,
) -> None:
    store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires())
    store.admit_review(selected(), expires_at=expires())
    store.begin_review_action("selected-review", SCOPE, "comment-a", expires_at=expires())
    before = store.path.read_bytes()
    selections = ReviewSelectionStore(store)
    with pytest.raises(ReplayStoreInvalid, match="explicit storage adoption"):
        selections.retain(original(prepared_intent), prepared_intent, expires_at=expires())

    def held() -> None:
        raise RuntimeError("incompatible reader still held")

    with pytest.raises(RuntimeError, match="still held"):
        selections.adopt(lifecycle_checkpoint=held, expires_at=expires())
    assert store.path.read_bytes() == before
    selections.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    document = json.loads(store.path.read_bytes())
    assert document["reviews"] == json.loads(before)["reviews"]
    class OldReader(ReplayStore):
        def _validate_owner(self, document: dict) -> None:
            if "review_selection_version" in document:
                raise ReplayStoreInvalid("unsupported pre-approval envelope")
            super()._validate_owner(document)

    adopted = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="invalid persisted replay state"):
        OldReader(store.root, store.namespace).observation_targets(expires_at=expires())
    assert store.path.read_bytes() == adopted
    selections.retain(original(prepared_intent), prepared_intent, expires_at=expires())
    operation = selections.recover(original(prepared_intent), expires_at=expires())
    with pytest.raises(ValueError, match="invalid persisted review operation"):
        review_intent.validate_operations(
            [operation], receipt_bytes=4096, review_bytes=64 * 1024 * 1024, versions=(1, 2),
        )
    assert store.inspect_review("selected-review", SCOPE, expires_at=expires())["actions"][0]["state"] == "unknown"


def test_prepared_state_cannot_forge_attempts_and_capacity_refuses_without_discard(
    store: ReplayStore, prepared_intent: dict,
) -> None:
    selections = adopt(store)
    draft = original(prepared_intent)
    operation = selections.retain(draft, prepared_intent, expires_at=expires())["operation"]
    before = store.path.read_bytes()
    invalid = copy.deepcopy(operation)
    invalid["actions"][0].update(state="unknown", attempt_id="forged-attempt")
    with pytest.raises(ValueError, match="cannot contain an execution attempt"):
        review_intent.validate_operations(
            [invalid], receipt_bytes=4096, review_bytes=64 * 1024 * 1024, versions=(1, 2), selections=True,
        )
    document = json.loads(before)
    document["capacities"]["review_bytes"] = operation["reserved_bytes"] + 1024
    storage._atomic_write_json(store.path, document)
    full = store.path.read_bytes()
    other = copy.deepcopy(prepared_intent)
    other["operation_id"] = "new-operation"
    # A bodyless vote needs no GUID; capacity still reserves its eventual receipt.
    other["actions"] = other["actions"][1:]
    other_draft = original(other)
    with pytest.raises(ValueError, match="capacity"):
        selections.retain(other_draft, other, expires_at=expires())
    assert store.path.read_bytes() == full
    assert selections.recover(draft, expires_at=expires()) == operation


def test_other_layout_adoptions_preserve_pending_selection_and_receipt_funding(
    store: ReplayStore, prepared_intent: dict,
) -> None:
    from agent_pull_requests.attention_store import AttentionStore
    from agent_pull_requests.conversation_store import ConversationStore
    from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore

    selections = adopt(store)
    draft = original(prepared_intent)
    retained = selections.retain(draft, prepared_intent, expires_at=expires())["operation"]
    for adopter in (
        ConversationStore(store).adopt, AttentionStore(store).adopt,
        FeedbackCaptureStore(store).adopt, FeedbackCaptureStore(store).adopt_readback,
    ):
        adopter(lifecycle_checkpoint=lambda: None, expires_at=expires())
        assert selections.recover(draft, expires_at=expires()) == retained
    assert store.admit_review(prepared_intent, expires_at=expires())["operation"]["reserved_bytes"] == retained["reserved_bytes"]
