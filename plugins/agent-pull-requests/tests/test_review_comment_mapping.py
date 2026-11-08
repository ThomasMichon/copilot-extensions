"""Explicit acquired metadata in the real retained operation owner, with no IO."""

from __future__ import annotations

import copy
import json
import threading

import pytest
from single_instance_lease import OperationLockedError

from agent_pull_requests import watch_replay_store as storage
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.review_comment_mapping import CommentMappingStore, EXTENSION, FIELD, MAX_SLOT_BYTES
from agent_pull_requests.review_workspace import build_generation
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain
from test_review_comment_observation import acquired
from test_review_edit_binding import allocate, edits as edits, refresh, submit
from test_review_executor import environment as environment
from test_review_owner_store import expires, response
from test_workspace_comment_preparation import comment_preparer as comment_preparer


def enable(store: ReplayStore) -> CommentMappingStore:
    ConversationStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    owner = CommentMappingStore(store)
    owner.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    return owner


def generation(edits: tuple, second: int = 0, *, complete: bool = True) -> dict:
    (_, _, baseline, _, backend, _), _, _, _ = edits
    evidence = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": copy.deepcopy(baseline["scope"]),
        "sections": {name: copy.deepcopy(section) for name, section in baseline["sections"].items()
                     if section["data"] is not None},
    }
    for section in evidence["sections"].values():
        section.update(
            observed_from=f"2026-10-30T00:02:{second:02d}Z",
            observed_to=f"2026-10-30T00:02:{second + 1:02d}Z",
        )
    evidence["sections"]["discussions"].update(
        data=copy.deepcopy(backend.threads), complete=complete, errors=[] if complete else ["page_unavailable"],
    )
    return build_generation(evidence)["generation"]


def ingest(store: ReplayStore, value: dict, **kwargs: object) -> dict:
    return ConversationStore(store).observe_workspace(
        value, expires_at=expires(), comment_mappings=True, **kwargs,
    )


def read(owner: CommentMappingStore, chosen: dict) -> dict:
    return owner.read(
        chosen["operation_id"], chosen["scope"], chosen["actions"][0]["id"], expires_at=expires(),
    )


@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
@pytest.mark.parametrize("environment", [False, True], indirect=True)
def test_actual_preparation_receipt_acquisition_and_restart_preserve_originals(edits: tuple, kind: str) -> None:
    (store, selected, _, _, backend, _), _, _, _ = edits
    first = allocate(edits)
    assert submit(edits, first)["status"] == "complete"
    owner = enable(store)
    chosen = first
    if kind != "comment":
        refresh(edits, "next-selected-write")
        if kind == "reply":
            selected["actions"] = [{"id": "finding", "kind": "reply", "thread_id": 100, "body": "Reply."}]
        chosen = allocate(edits)
        assert submit(edits, chosen)["status"] == "complete"
    original = store.prepared_review_receipt(
        chosen["operation_id"], chosen["scope"], "finding", expires_at=expires(),
    )
    value = generation(edits)
    calls = list(backend.calls)
    assert ingest(store, value)["status"] == "observed"
    stored = read(owner, chosen)
    assert stored["mapping"]["status"] == "correlated"
    assert stored["mapping"]["generation_id"] == value["id"]
    assert stored["identity"] == original["identity"] and stored["receipt"] == original["receipt"]
    assert stored["original_version_proven"] is False and stored["feedback_progress"] is False
    assert chosen["actions"][0]["body"] not in json.dumps(stored)
    assert len(json.dumps(stored["mapping"]).encode()) <= MAX_SLOT_BYTES
    before = store.path.read_bytes()
    restarted = CommentMappingStore(ReplayStore(store.root, store.namespace, service_identity=store.service_identity))
    assert read(restarted, chosen) == stored and store.path.read_bytes() == before
    assert ingest(store, value)["status"] == "unchanged" and store.path.read_bytes() == before
    assert backend.calls == calls
    # The retained read has no dependency on a cache or native credential source.
    assert store.prepared_review_receipt(
        chosen["operation_id"], chosen["scope"], "finding", expires_at=expires(),
    ) == original


@pytest.mark.parametrize("change,status", [("content", "conflict"), ("actor", "conflict"), ("partial", "unavailable")])
def test_later_divergence_and_stale_acquisition_do_not_rewrite_confirmation(
    edits: tuple, change: str, status: str,
) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    owner = enable(store)
    old = generation(edits)
    first = ingest(store, old)
    if change == "content":
        backend.threads[0]["comments"][0]["content"] = "Edited; original marker removed."
    elif change == "actor":
        backend.threads[0]["comments"][0]["author"]["id"] = "another-native-actor"
    newer = generation(edits, 4, complete=change != "partial")
    assert ingest(store, newer)["status"] == "observed"
    result = read(owner, chosen)
    assert result["mapping"]["status"] == status and result["action_state"] == "confirmed"
    before = store.path.read_bytes()
    assert ingest(store, old)["status"] == "stale_observation"
    assert read(owner, chosen) == result and store.path.read_bytes() == before
    assert first["cursor"]["sequence"] + 1 == json.loads(before)["conversations"]["records"][0]["sequence"]


def test_later_intent_retains_logical_identity_but_does_not_merge_old_write(edits: tuple) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    owner = enable(store)
    first = allocate(edits)
    submit(edits, first)
    ingest(store, generation(edits))
    refresh(edits, "intentional-edit")
    edited = allocate(edits)
    submit(edits, edited)
    ingest(store, generation(edits, 4))
    original, current = read(owner, first), read(owner, edited)
    assert original["identity"]["comment_id"] == current["identity"]["comment_id"]
    assert original["identity"]["write_id"] != current["identity"]["write_id"]
    assert original["mapping"]["status"] == "conflict" and current["mapping"]["status"] == "correlated"
    assert original["action_state"] == current["action_state"] == "confirmed"


def test_unknown_receipt_change_invalidates_mapping_without_promoting_matching_content(edits: tuple) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    owner = enable(store)
    chosen = allocate(edits)
    backend.lose_reply = True
    submit(edits, chosen)
    value = generation(edits)
    ingest(store, value)
    result = read(owner, chosen)
    assert result["action_state"] == "unknown"
    assert result["mapping"]["reason"] == "original_action_not_confirmed"
    operation = store.inspect_review(chosen["operation_id"], chosen["scope"], expires_at=expires())
    store.record_review_receipt(
        chosen["operation_id"], chosen["scope"], "finding", operation["actions"][0]["attempt_id"],
        response(thread_id=100), expires_at=expires(), confirm=False,
    )
    assert read(owner, chosen)["mapping"] is None
    before = json.loads(store.path.read_bytes())["conversations"]
    assert ingest(store, value)["status"] == "unchanged"
    assert read(owner, chosen)["action_state"] == "unknown"
    assert json.loads(store.path.read_bytes())["conversations"] == before


def test_adoption_is_explicit_atomic_capacity_funded_and_old_reader_refuses(edits: tuple) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    journal = ConversationStore(store)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    before = store.path.read_bytes()
    with pytest.raises(ReplayStoreInvalid, match="explicit storage adoption"):
        ingest(store, generation(edits))
    assert store.path.read_bytes() == before
    owner = CommentMappingStore(store)
    def held() -> None:
        raise RuntimeError("incompatible reader has not drained")
    with pytest.raises(RuntimeError, match="drained"):
        owner.adopt(lifecycle_checkpoint=held, expires_at=expires())
    assert store.path.read_bytes() == before
    original = json.loads(before)["reviews"][0]
    with store._lock():
        document = store._read_document()
        document["capacities"]["review_bytes"] = original["reserved_bytes"] + 1024
        storage._atomic_write_json(store.path, document)
    limited = store.path.read_bytes()
    with pytest.raises(ValueError, match="capacity"):
        owner.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    assert store.path.read_bytes() == limited
    with store._lock():
        storage._atomic_write_json(store.path, json.loads(before))
    enable(store)
    adopted = json.loads(store.path.read_bytes())["reviews"][0]
    assert adopted["intent"] == original["intent"] and adopted["actions"] == original["actions"]
    assert adopted["reserved_bytes"] >= original["reserved_bytes"] + MAX_SLOT_BYTES
    class OldReader(ReplayStore):
        def _validate_owner(self, document: dict) -> None:
            if EXTENSION in document:
                raise ReplayStoreInvalid("old strict reader refuses mapping")
            super()._validate_owner(document)
    with pytest.raises(ReplayStoreInvalid, match="old strict reader"):
        OldReader(store.root, store.namespace).inspect_review(
            chosen["operation_id"], chosen["scope"], expires_at=expires(),
        )


@pytest.mark.parametrize("uncertain", [False, True])
def test_cache_published_then_failed_mapping_transaction_reconciles_same_generation(
    edits: tuple, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    owner = enable(store)
    cache, value = acquired(edits)
    calls = list(backend.calls)
    original = storage._atomic_write_json
    before = store.path.read_bytes()
    def fail(path: object, document: dict) -> None:
        if uncertain:
            original(path, document)
            raise StateCommitUncertain("controlled acquired mapping uncertainty")
        raise OSError("controlled before mapping replacement")
    with monkeypatch.context() as patch:
        patch.setattr(storage, "_atomic_write_json", fail)
        with pytest.raises(OSError):
            ingest(store, value)
    assert (store.path.read_bytes() == before) is not uncertain
    assert cache.lookup(value["scope"])["current_id"] == value["id"]
    assert ingest(store, value)["status"] == ("unchanged" if uncertain else "observed")
    assert read(owner, chosen)["mapping"]["status"] == "correlated" and backend.calls == calls
    assert json.loads(store.path.read_bytes())["conversations"]["records"][0]["sequence"] == 1


def test_owner_lock_contention_and_withdrawal_leave_no_partial_mapping(edits: tuple) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    enable(store)
    value = generation(edits)
    before = store.path.read_bytes()
    locked, release = threading.Event(), threading.Event()
    def hold() -> None:
        with store._lock():
            locked.set()
            release.wait(5)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert locked.wait(2)
        with pytest.raises(OperationLockedError):
            ingest(store, value)
    finally:
        release.set()
        thread.join(5)
    def denied() -> None:
        raise RuntimeError("actual owner withdrawn")
    with pytest.raises(RuntimeError, match="withdrawn"):
        ingest(store, value, checkpoint=denied)
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("change", ["flag", "unknown-field", "interval", "underfunded"])
def test_malformed_mapping_layout_refuses_before_replacement(edits: tuple, change: str) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    enable(store)
    ingest(store, generation(edits))
    before = store.path.read_bytes()
    def corrupt(document: dict) -> dict:
        operation = document["reviews"][0]
        if change == "flag":
            document[EXTENSION] = True
        elif change == "unknown-field":
            operation[FIELD]["finding"]["body"] = "Not a second body archive."
        elif change == "interval":
            operation[FIELD]["finding"]["observation"]["observed_from"] = "invalid"
        else:
            operation["reserved_bytes"] -= MAX_SLOT_BYTES
        return {}
    with pytest.raises((ValueError, ReplayStoreInvalid)):
        store._review_transaction(corrupt, expires_at=expires())
    assert store.path.read_bytes() == before


def test_default_ingest_and_foreign_scope_never_fill_slots(edits: tuple) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    owner = enable(store)
    chosen = allocate(edits)
    submit(edits, chosen)
    value = generation(edits)
    assert ConversationStore(store).observe_workspace(value, expires_at=expires())["status"] == "observed"
    assert read(owner, chosen)["mapping"] is None
    foreign = copy.deepcopy(value)
    foreign["scope"]["number"] += 1
    from agent_pull_requests.review_workspace import _digest
    foreign["id"] = _digest({key: value for key, value in foreign.items() if key != "id"})
    assert ingest(store, foreign)["status"] == "observed"
    assert read(owner, chosen)["mapping"] is None
    backend.threads[0]["id"] = 999  # Copied content cannot replace the receipt's native target.
    ingest(store, generation(edits, 4))
    assert read(owner, chosen)["mapping"]["reason"] == "native_thread_missing"


def test_acquisition_normalizes_discussions_once_for_all_retained_actions(
    edits: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import review_comment_observation

    (store, _, _, _, _, _), _, _, _ = edits
    owner = enable(store)
    first = allocate(edits)
    submit(edits, first)
    refresh(edits, "later-intent")
    later = allocate(edits)
    submit(edits, later)
    normalize = review_comment_observation._acquired
    digest = review_comment_observation._digest
    calls = []
    hashes = []
    def counted(value: dict, scope: dict) -> object:
        calls.append(value["id"])
        return normalize(value, scope)
    def hashed(value: dict) -> str:
        hashes.append(value["comment"]["id"])
        return digest(value)
    monkeypatch.setattr(review_comment_observation, "_acquired", counted)
    monkeypatch.setattr(review_comment_observation, "_digest", hashed)
    value = generation(edits)
    ingest(store, value)
    assert calls == [value["id"]]
    assert hashes == [1]
    assert read(owner, first)["mapping"]["status"] == "conflict"
    assert read(owner, later)["mapping"]["status"] == "correlated"


@pytest.mark.parametrize("duplicate", ["thread", "comment"])
def test_newer_duplicate_native_ids_replace_old_positive_correlation(edits: tuple, duplicate: str) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    submit(edits, chosen)
    owner = enable(store)
    ingest(store, generation(edits))
    original = read(owner, chosen)
    assert original["mapping"]["status"] == "correlated"
    if duplicate == "thread":
        backend.threads.append(copy.deepcopy(backend.threads[0]))
    else:
        comments = backend.threads[0]["comments"]
        comments.append(copy.deepcopy(comments[0]))
    newer = generation(edits, 4)
    assert ingest(store, newer)["status"] == "observed"
    current = read(owner, chosen)
    assert current["mapping"]["status"] == "unavailable"
    assert current["mapping"]["reason"] == "discussions_invalid"
    assert current["mapping"]["generation_id"] == newer["id"]
    assert current["mapping"]["observation"]["observed_from"] == "2026-10-30T00:02:04Z"
    assert current["mapping"]["current_object_id"] is None
    assert current["receipt"] == original["receipt"] and current["action_state"] == "confirmed"


def test_full_mapping_budget_preserves_pending_receipt_capacity(edits: tuple) -> None:
    (store, selected, _, _, _, _), _, _, _ = edits
    owner = enable(store)
    chosen = allocate(edits)
    submit(edits, chosen)
    selected["operation_id"] = "pending-second-selection"
    pending = allocate(edits)
    store.admit_review(pending, expires_at=expires())
    def limit(document: dict) -> dict:
        document["capacities"]["review_bytes"] = 1024 + sum(
            operation["reserved_bytes"] for operation in document["reviews"]
        )
        return {}
    store._review_transaction(limit, expires_at=expires())
    ingest(store, generation(edits))
    assert read(owner, chosen)["mapping"]["status"] == "correlated"
    assert read(owner, pending)["mapping"]["status"] == "unavailable"
    begun = store.begin_review_action(pending["operation_id"], pending["scope"], "finding", expires_at=expires())
    assert read(owner, pending)["mapping"] is None
    result = store.record_review_receipt(
        pending["operation_id"], pending["scope"], "finding", begun["action"]["attempt_id"],
        response(thread_id=200, native_version="v" * 256), expires_at=expires(), confirm=False,
    )
    assert result["state"] == "unknown" and result["receipt"]["platform_ids"]["thread_id"] == 200
    before = store.path.read_bytes()
    another = copy.deepcopy(selected)
    another["operation_id"] = "over-capacity"
    with pytest.raises(ValueError, match="capacity"):
        store.admit_review(another, expires_at=expires())
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["legacy", "verdict"])
def test_legacy_and_bodyless_operations_do_not_allocate_mapping_slots(edits: tuple, kind: str) -> None:
    (store, selected, _, _, _, _), _, _, _ = edits
    owner = enable(store)
    selected["operation_id"] = "unprepared"
    if kind == "verdict":
        selected["version"] = 2
        selected["actions"] = [{"id": "finding", "kind": "verdict", "verdict": "APPROVED"}]
    operation = store.admit_review(selected, expires_at=expires())["operation"]
    assert FIELD not in operation
    ingest(store, generation(edits))
    assert read(owner, selected)["mapping"] is None and read(owner, selected)["identity"] is None
