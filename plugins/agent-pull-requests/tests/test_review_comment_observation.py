"""Actual preparation/native receipts joined to disk-cached synthetic acquisition."""

from __future__ import annotations

import copy
import json
import threading

import pytest
from single_instance_lease import OperationLockedError

from agent_pull_requests.generation_cache import CacheInvalid, GenerationCache
from agent_pull_requests.review_comment_observation import observe_prepared_comment
from agent_pull_requests.review_workspace import _digest, build_generation
from agent_pull_requests.watch_replay_store import ReplayCallerExpired, ReplayStore
from test_review_edit_binding import allocate, edits as edits, refresh, submit
from test_review_executor import environment as environment
from test_review_owner_store import expires, response
from test_workspace_comment_preparation import comment_preparer as comment_preparer


def acquired(edits: tuple, *, threads: list | None = None, complete: bool = True) -> tuple:
    (store, _, baseline, _, backend, _), _, _, _ = edits
    evidence = {
        "schema": "agent-pull-requests.review-evidence", "version": 1,
        "scope": copy.deepcopy(baseline["scope"]),
        "sections": {name: copy.deepcopy(section) for name, section in baseline["sections"].items()
                     if section["data"] is not None},
    }
    evidence["sections"]["discussions"].update(
        data=copy.deepcopy(backend.threads if threads is None else threads),
        complete=complete, errors=[] if complete else ["page_unavailable"],
        observed_from="2026-10-30T00:02:00Z", observed_to="2026-10-30T00:02:01Z",
    )
    generation = build_generation(evidence)["generation"]
    cache = GenerationCache(
        store.root, store.namespace, max_bytes=8 * 1024 * 1024, max_generations=20,
        service_identity=store.service_identity,
    )
    if not cache.root.exists():
        cache.provision()
    current = cache.lookup(generation["scope"])["current_id"]
    cache.publish(generation, expected_current=current)
    return cache, generation


def observe(store: ReplayStore, cache: GenerationCache, chosen: dict, generation: dict, **kwargs: object) -> dict:
    return observe_prepared_comment(
        store, cache, chosen["operation_id"], chosen["scope"], chosen["actions"][0]["id"],
        generation["id"], expires_at=expires(), **kwargs,
    )


def retained_bytes(store: ReplayStore) -> dict:
    return {str(path.relative_to(store.root)): path.read_bytes()
            for path in store.root.rglob("*.json")}


@pytest.mark.parametrize("kind", ["comment", "reply", "edit_owned_comment"])
@pytest.mark.parametrize("environment", [False, True], indirect=True)
def test_actual_native_receipt_and_cached_generation_survive_restart_without_io_or_mutation(
    edits: tuple, kind: str,
) -> None:
    (store, selected, _, _, backend, _), _, _, _ = edits
    first = allocate(edits)
    assert submit(edits, first)["status"] == "complete"
    chosen = first
    if kind != "comment":
        refresh(edits, "next-selected-write")
        if kind == "reply":
            selected["actions"] = [{
                "id": "finding", "kind": "reply", "thread_id": 100, "body": "Selected reply.",
            }]
        chosen = allocate(edits)
        assert submit(edits, chosen)["status"] == "complete"
    cache, generation = acquired(edits)
    before, calls, policy_calls = retained_bytes(store), list(backend.calls), list(backend.policy_calls)
    restarted = ReplayStore(store.root, store.namespace, service_identity=store.service_identity)
    result = observe(restarted, cache, chosen, generation)
    assert result["status"] == "correlated"
    assert result["action_state"] == "confirmed"
    assert result["identity"] == chosen["actions"][0]["preparation"]["identity"]
    assert result["body_id"] == chosen["actions"][0]["preparation"]["body_id"]
    assert result["native_target"] == {"thread_id": 100, "comment_id": 2 if kind == "reply" else 1}
    assert result["native_actor_id"] == chosen["scope"]["principal_id"]
    assert result["observation"]["observed_from"] == "2026-10-30T00:02:00Z"
    assert result["observation"]["observed_to"] == "2026-10-30T00:02:01Z"
    assert result["observation"]["freshness"] == "retained"
    assert result["original_version_proven"] is False and result["feedback_progress"] is False
    assert chosen["actions"][0]["body"] not in json.dumps(result)
    assert observe(restarted, cache, chosen, generation) == result
    assert backend.calls == calls and backend.policy_calls == policy_calls
    assert retained_bytes(store) == before
    assert submit(edits, chosen)["entered_writes"] == 0
    # A confirmed retry may read the provider; the projection above must not.
    assert backend.calls[:len(calls)] == calls
    assert all(call[0] == "GET" for call in backend.calls[len(calls):])
    assert backend.policy_calls == policy_calls
    assert retained_bytes(store) == before


@pytest.mark.parametrize("change,status,reason", [
    ("copied", "unavailable", "native_thread_missing"),
    ("missing-comment", "unavailable", "native_comment_missing"),
    ("deleted-thread", "conflict", "native_target_deleted"),
    ("deleted-comment", "conflict", "native_target_deleted"),
    ("actor", "conflict", "native_actor_changed"),
    ("missing-actor", "unavailable", "native_actor_missing"),
    ("system", "conflict", "native_comment_not_text"),
    ("boolean-type", "conflict", "native_comment_not_text"),
    ("float-type", "conflict", "native_comment_not_text"),
    ("string-text", "correlated", "exact_prepared_content_at_retained_native_target"),
    ("parent", "conflict", "native_parent_changed"),
    ("content", "conflict", "native_content_changed"),
    ("duplicate-thread", "unavailable", "discussions_invalid"),
    ("duplicate-comment", "unavailable", "discussions_invalid"),
    ("missing-id", "unavailable", "discussions_invalid"),
    ("partial", "unavailable", "discussions_incomplete"),
])
def test_current_divergence_never_replaces_original_confirmation_or_searches_tags(
    edits: tuple, change: str, status: str, reason: str,
) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    thread, comment = backend.threads[0], backend.threads[0]["comments"][0]
    if change == "copied":
        thread["id"] = 999
    elif change == "missing-comment":
        comment["id"] = 2
    elif change.startswith("deleted"):
        (thread if change == "deleted-thread" else comment)["isDeleted"] = True
    elif change == "actor":
        comment["author"]["id"] = "different-native-principal"
    elif change == "missing-actor":
        comment.pop("author")
    elif change == "system":
        comment["commentType"] = 2
    elif change == "boolean-type":
        comment["commentType"] = True
    elif change == "float-type":
        comment["commentType"] = 1.0
    elif change == "string-text":
        comment["commentType"] = "text"
    elif change == "parent":
        comment["parentCommentId"] = 1
    elif change == "content":
        comment["content"] = "Manually edited; marker stripped."
        copied = copy.deepcopy(thread)
        copied["id"], copied["comments"][0]["content"] = 999, chosen["actions"][0]["body"]
        backend.threads.append(copied)
    elif change == "duplicate-thread":
        backend.threads.append(copy.deepcopy(thread))
    elif change == "duplicate-comment":
        thread["comments"].append(copy.deepcopy(comment))
    elif change == "missing-id":
        comment["id"] = True
    cache, generation = acquired(edits, complete=change != "partial")
    before, calls = retained_bytes(store), list(backend.calls)
    original = store.prepared_review_receipt(
        chosen["operation_id"], chosen["scope"], "finding", expires_at=expires(),
    )
    result = observe(store, cache, chosen, generation)
    assert (result["status"], result["reason"]) == (status, reason)
    assert result["action_state"] == "confirmed" and result["receipt"] == original["receipt"]
    assert retained_bytes(store) == before and backend.calls == calls


@pytest.mark.parametrize("partial", [None, {}, {"thread_id": 100}, {"thread_id": 100, "comment_id": 1}])
def test_matching_acquisition_never_confirms_unattempted_or_unknown_actions(edits: tuple, partial: dict | None) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    store.admit_review(chosen, expires_at=expires())
    if partial is not None:
        begun = store.begin_review_action(chosen["operation_id"], chosen["scope"], "finding", expires_at=expires())
        if partial:
            store.record_review_receipt(
                chosen["operation_id"], chosen["scope"], "finding", begun["action"]["attempt_id"],
                response(**partial), expires_at=expires(), confirm=False,
            )
    backend.threads = [{"id": 100, "comments": [{
        "id": 1, "parentCommentId": 0, "commentType": 1,
        "author": {"id": chosen["scope"]["principal_id"]}, "content": chosen["actions"][0]["body"],
    }]}]
    cache, generation = acquired(edits)
    before = retained_bytes(store)
    result = observe(store, cache, chosen, generation)
    assert result["reason"] == "original_action_not_confirmed" and result["status"] == "unavailable"
    assert result["action_state"] == ("unattempted" if partial is None else "unknown")
    if partial:
        assert result["receipt"]["platform_ids"] == partial
    assert retained_bytes(store) == before and backend.calls == []


def test_later_owned_edit_changes_only_current_correlation_not_prior_identity(edits: tuple) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    first = allocate(edits)
    assert submit(edits, first)["status"] == "complete"
    cache, previous = acquired(edits)
    original = observe(store, cache, first, previous)
    refresh(edits, "later-edit")
    changed = allocate(edits)
    assert submit(edits, changed)["status"] == "complete"
    cache, current = acquired(edits)
    result = observe(store, cache, first, current)
    assert result["status"] == "conflict" and result["action_state"] == "confirmed"
    assert result["identity"] == original["identity"]
    assert observe(store, cache, changed, current)["status"] == "correlated"
    # Selecting an older immutable object retains its interval, not the current pointer's.
    assert observe(store, cache, first, previous) == original


def test_actual_lost_native_response_is_not_confirmed_by_matching_acquisition(edits: tuple) -> None:
    (store, _, _, _, backend, _), _, _, _ = edits
    chosen = allocate(edits)
    backend.lose_reply = True
    submit(edits, chosen)
    assert backend.threads[0]["comments"][0]["content"] == chosen["actions"][0]["body"]
    cache, generation = acquired(edits)
    before, calls = retained_bytes(store), list(backend.calls)
    result = observe(store, cache, chosen, generation)
    assert result["status"] == "unavailable" and result["action_state"] == "unknown"
    assert result["reason"] == "original_action_not_confirmed"
    assert retained_bytes(store) == before and backend.calls == calls


@pytest.mark.parametrize("field", ["provider", "endpoint", "project_id", "repository_id", "principal_id", "number"])
def test_wrong_operation_scope_never_borrows_a_matching_comment(edits: tuple, field: str) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    cache, generation = acquired(edits)
    wrong = copy.deepcopy(chosen)
    wrong["scope"][field] = 43 if field == "number" else "https://other.example" if field == "endpoint" else "other"
    before = retained_bytes(store)
    with pytest.raises(ValueError):
        observe(store, cache, wrong, generation)
    assert retained_bytes(store) == before


def test_cache_scope_owner_and_missing_generation_are_not_caller_policy_labels(edits: tuple) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    cache, generation = acquired(edits)
    missing = {**generation, "id": "f" * 64}
    assert observe(store, cache, chosen, missing)["reason"] == "generation_missing"
    foreign = copy.deepcopy(generation)
    foreign["scope"]["number"] += 1
    foreign["id"] = _digest({key: value for key, value in foreign.items() if key != "id"})
    cache.publish(foreign, expected_current=None)
    before = retained_bytes(store)
    with pytest.raises(CacheInvalid, match="outside"):
        observe(store, cache, chosen, foreign)
    other_cache = GenerationCache(store.root, "other-namespace", max_bytes=10, max_generations=1)
    with pytest.raises(ValueError, match="share"):
        observe(store, other_cache, chosen, generation)
    assert retained_bytes(store) == before


@pytest.mark.parametrize("kind", ["legacy", "verdict"])
def test_unprepared_actions_remain_unavailable(edits: tuple, kind: str) -> None:
    (store, selected, _, _, _, _), _, _, _ = edits
    selected["operation_id"] = "unprepared-operation"
    if kind == "verdict":
        selected["version"] = 2
        selected["actions"] = [{"id": "finding", "kind": "verdict", "verdict": "APPROVED"}]
    store.admit_review(selected, expires_at=expires())
    cache, generation = acquired(edits)
    before = retained_bytes(store)
    result = observe(store, cache, selected, generation)
    assert result["status"] == "unavailable" and result["reason"] == "original_preparation_missing"
    assert retained_bytes(store) == before


@pytest.mark.parametrize("change", ["interval", "complete-errors", "malformed-actor"])
def test_hash_valid_but_unusable_cached_discussions_do_not_correlate(edits: tuple, change: str) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    cache, generation = acquired(edits)
    section = generation["sections"]["discussions"]
    if change == "interval":
        section["observed_from"] = "2026-10-30T00:02:02Z"
    elif change == "complete-errors":
        section["errors"] = ["incomplete"]
    else:
        section["data"][0]["comments"][0]["author"] = "malformed-actor"
    previous = generation["id"]
    generation["id"] = _digest({key: value for key, value in generation.items() if key != "id"})
    cache.publish(generation, expected_current=previous)
    before = retained_bytes(store)
    result = observe(store, cache, chosen, generation)
    assert result["status"] == "unavailable" and result["reason"] == "discussions_invalid"
    if change == "malformed-actor":
        assert result["observation"]["observed_from"] == section["observed_from"]
        assert result["observation"]["observed_to"] == section["observed_to"]
    else:
        assert result["observation"] is None
    assert retained_bytes(store) == before


def test_expiry_after_cached_read_returns_no_stale_success(edits: tuple) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    cache, generation = acquired(edits)
    before = retained_bytes(store)
    expired = False

    def checkpoint() -> None:
        if expired:
            raise ReplayCallerExpired("synthetic caller expired")

    def cache_checkpoint() -> None:
        nonlocal expired
        expired = True

    cache.checkpoint = cache_checkpoint
    with pytest.raises(ReplayCallerExpired, match="expired"):
        observe(store, cache, chosen, generation, checkpoint=checkpoint)
    assert retained_bytes(store) == before


@pytest.mark.parametrize("owner", ["receipt", "cache"])
def test_real_owner_lock_contention_never_falls_back_to_unlocked_reads(edits: tuple, owner: str) -> None:
    (store, _, _, _, _, _), _, _, _ = edits
    chosen = allocate(edits)
    assert submit(edits, chosen)["status"] == "complete"
    cache, generation = acquired(edits)
    entered, release = threading.Event(), threading.Event()
    before = retained_bytes(store)

    def hold() -> None:
        from single_instance_lease import OperationLock
        path = store.root / "watch-replay.lock" if owner == "receipt" else cache.lock_path
        with OperationLock(path):
            entered.set()
            assert release.wait(10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert entered.wait(10)
        with pytest.raises(OperationLockedError):
            observe(store, cache, chosen, generation)
    finally:
        release.set()
        holder.join(10)
    assert not holder.is_alive()
    assert retained_bytes(store) == before
    assert observe(store, cache, chosen, generation)["status"] == "correlated"
