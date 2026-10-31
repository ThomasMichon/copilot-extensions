"""Finite orchestration through the existing protected owner and ReplayStore."""

from __future__ import annotations

import copy
import math
import time
from collections import Counter

from . import review_intent
from .review_workspace import _digest, _generation, _record, _revision, _scope


class ReviewCandidateChanged(RuntimeError):
    """Current source/discussion evidence no longer matches selected intent."""


def _threads(values):
    if not isinstance(values, list):
        raise ValueError("current discussions must be a complete list")
    result = {}
    for value in values:
        item = _record("discussions", value)
        identity = item.get("id")
        if type(identity) is not int or identity <= 0 or identity in result:
            raise ValueError("current discussions have missing/duplicate native IDs")
        if "comments" not in item:
            raise ValueError("current discussion comments are incomplete")
        comment_ids = [comment.get("id") for comment in item["comments"]]
        if any(type(identity) is not int or identity <= 0 for identity in comment_ids) or len(set(comment_ids)) != len(comment_ids):
            raise ValueError("current discussion comment identities are incomplete")
        item["comments"] = sorted(item["comments"], key=lambda comment: comment.get("id", 0))
        result[identity] = item
    return result


def _expected_discussions(baseline, actual, operation, raw_discussions):
    """Accept only exactly verified prior self-effects, never arbitrary fresh state."""
    expected = copy.deepcopy(baseline)
    principal = operation["intent"]["scope"]["principal_id"]
    affected = set()
    for selected, state in zip(operation["intent"]["actions"], operation["actions"]):
        if state["state"] != "confirmed" or selected["kind"] == "verdict":
            continue
        ids = state["receipt"]["platform_ids"]
        thread_id, comment_id = ids["thread_id"], ids["comment_id"]
        current = actual.get(thread_id)
        if current is None or current.get("isDeleted"):
            raise ReviewCandidateChanged("confirmed owned thread is unavailable")
        comments = {comment.get("id"): comment for comment in current["comments"]}
        owned = comments.get(comment_id)
        if (
            owned is None or owned.get("isDeleted")
            or (owned.get("author") or {}).get("id") != principal
        ):
            raise ReviewCandidateChanged("confirmed owned comment is unavailable")
        if selected["kind"] == "comment":
            if thread_id in expected or current.get("status") not in (1, "active"):
                raise ReviewCandidateChanged("new owned thread has unrelated changes")
            expected[thread_id] = {**copy.deepcopy(current), "comments": []}
        before = expected.get(thread_id)
        if before is None:
            raise ReviewCandidateChanged("selected discussion baseline is unavailable")
        current_meta = {key: value for key, value in current.items() if key not in ("comments", "lastUpdatedDate")}
        before_meta = {key: value for key, value in before.items() if key not in ("comments", "lastUpdatedDate")}
        if current_meta != before_meta:
            raise ReviewCandidateChanged("owned thread metadata changed outside the selected effect")
        original_comments = {comment.get("id"): comment for comment in before["comments"]}
        if selected["kind"] in ("comment", "reply"):
            if comment_id in original_comments:
                raise ReviewCandidateChanged("confirmed reply identity already existed")
            projected = copy.deepcopy(owned)
            projected["content"] = selected["body"]
            original_comments[comment_id] = projected
        else:
            original = original_comments.get(comment_id)
            if original is None or (original.get("author") or {}).get("id") != principal:
                raise ReviewCandidateChanged("owned edit baseline is unavailable")
            if _digest(original.get("content")) != selected["expected_content_id"]:
                raise ReviewCandidateChanged("owned edit baseline content differs")
            projected = copy.deepcopy(original)
            projected["content"] = selected["body"]
            for name in ("lastUpdatedDate", "lastContentUpdatedDate"):
                if name in owned:
                    projected[name] = owned[name]
                else:
                    projected.pop(name, None)
            original_comments[comment_id] = projected
        before["comments"] = sorted(original_comments.values(), key=lambda comment: comment["id"])
        affected.add(thread_id)
    for thread_id in affected:
        if "lastUpdatedDate" in actual[thread_id]:
            expected[thread_id]["lastUpdatedDate"] = actual[thread_id]["lastUpdatedDate"]
        else:
            expected[thread_id].pop("lastUpdatedDate", None)
    votes = Counter(
        {"APPROVED": 10, "CHANGES_REQUESTED": -10}[action["verdict"]]
        for action, state in zip(operation["intent"]["actions"], operation["actions"])
        if action["kind"] == "verdict" and state["state"] == "confirmed"
    )
    from agent_worktrees.providers.azure_observation import identity as native_identity, property_value, thread_evidence
    raw = {thread.get("id"): thread for thread in raw_discussions}
    for identity, thread in actual.items():
        if identity in expected:
            continue
        native = raw[identity]
        properties = native.get("properties") or {}
        if property_value(properties, "CodeReviewThreadType") != "VoteUpdate":
            continue
        _, events = thread_evidence([native], principal)
        if (len(events) == 1 and events[0].actor_id == native_identity(principal)
                and votes[events[0].vote] > 0 and thread["comments"]
                and all(comment.get("commentType") in (2, "system") for comment in thread["comments"])):
            expected[identity] = copy.deepcopy(thread)
            votes[events[0].vote] -= 1
    return expected


def _check_candidate(current, operation, baseline):
    if current["revision"] != operation["intent"]["revision"]:
        raise ReviewCandidateChanged("native source range changed")
    actual = _threads(current["discussions"])
    expected = _expected_discussions(baseline, actual, operation, current["discussions"])
    if actual != expected:
        raise ReviewCandidateChanged("discussion/decision inputs changed")
    return actual


def _check_selected_ownership(action, actual, principal):
    if action["kind"] not in ("reply", "edit_owned_comment"):
        return
    thread = actual.get(action["thread_id"])
    if thread is None or thread.get("isDeleted"):
        raise ReviewCandidateChanged("selected native thread is unavailable")
    if action["kind"] == "edit_owned_comment":
        comment = next((row for row in thread["comments"] if row.get("id") == action["comment_id"]), None)
        if (
            comment is None or comment.get("isDeleted")
            or (comment.get("author") or {}).get("id") != principal
            or _digest(comment.get("content")) != action["expected_content_id"]
        ):
            raise ReviewCandidateChanged("selected owned comment differs from reviewed baseline")


def execute_review(
    store, selected, generation, source_attachment, *, context_factory, checkpoint,
    expires_at: int, budget_seconds: float, clock=time.monotonic,
):
    """Existing owner factory supplies actual pinned auth/input/transport, not caller labels."""
    from agent_worktrees.providers.azure_owned_review import (
        ReviewContextUnavailable, protected_review_context,
    )
    if not callable(context_factory) or not callable(checkpoint):
        raise ValueError("existing protected context factory/checkpoint required")
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("finite review IO budget required")
    selected = review_intent.intent(selected)
    generation = copy.deepcopy(generation)
    source_attachment = copy.deepcopy(source_attachment)
    scope = _scope(generation.get("scope"))
    _generation(generation, scope)
    if (
        selected["scope"] != scope or selected["generation_id"] != generation["id"]
        or not all(generation["sections"][name]["complete"] for name in ("pull_request", "iterations", "changes"))
        or generation["revision"] != _revision(generation["sections"])
        or generation["sections"]["changes"].get("revision") != selected["revision"]
        or any(selected["revision"][name] != generation["revision"].get(name)
               for name in ("head_sha", "common_sha", "iteration"))
        or not generation["sections"]["discussions"]["complete"]
        or not isinstance(source_attachment, dict) or source_attachment.get("state") != "ready"
        or source_attachment.get("scope") != scope or source_attachment.get("generation_id") != generation["id"]
        or not isinstance(source_attachment.get("source"), dict)
        or source_attachment["source"].get("id") != selected["source_id"]
        or source_attachment["source"].get("complete") is not True
    ):
        raise ValueError("review selection requires matching source and complete discussion evidence")
    source = source_attachment["source"]
    if (
        source.get("schema") != "agent-pull-requests.local-source"
        or type(source.get("version")) is not int or source["version"] != 1
        or source.get("scope") != scope or source.get("revision") != generation["revision"]
        or source.get("errors") != []
        or source["id"] != _digest({key: value for key, value in source.items() if key != "id"})
    ):
        raise ValueError("review source manifest differs from the exact reviewed generation")
    baseline = _threads(generation["sections"]["discussions"]["data"])
    start, deadline = clock(), clock() + budget_seconds

    def remaining():
        checkpoint()
        result = deadline - clock()
        if result <= 0:
            raise TimeoutError("review operation IO budget exhausted")
        return result

    admitted = store.admit_review(selected, expires_at=expires_at, checkpoint=checkpoint)
    operation_id = selected["operation_id"]
    result = {
        "schema": "agent-pull-requests.review-execution", "version": 1,
        "operation_id": operation_id, "scope": scope,
        "reviewed_revision": copy.deepcopy(selected["revision"]),
        "status": "retained", "stage": "admission", "atomic_head_cas": False,
        "entered_writes": 0, "errors": [],
    }
    operation = admitted["operation"]
    try:
        for index, action in enumerate(selected["actions"]):
            operation = store.inspect_review(operation_id, scope, expires_at=expires_at, checkpoint=checkpoint)
            state = operation["actions"][index]
            if state["state"] == "confirmed":
                continue
            with protected_review_context(context_factory, scope, remaining) as context:
                result["stage"] = "preflight"
                current = context.read_state()
                if state["state"] == "unknown":
                    result["stage"] = "reconciliation"
                    proof = context.reconcile(copy.deepcopy(operation), copy.deepcopy(action))
                    if proof is None:
                        result["status"] = "unknown"
                        break
                    store.record_review_receipt(
                        operation_id, scope, action["id"], state["attempt_id"], proof,
                        expires_at=expires_at, checkpoint=checkpoint,
                    )
                    operation = store.inspect_review(operation_id, scope, expires_at=expires_at, checkpoint=checkpoint)
                    if operation["actions"][index]["state"] != "confirmed":
                        result["status"] = "unknown"
                        break
                    current = context.read_state()
                    _check_candidate(current, operation, baseline)
                    continue
                actual = _check_candidate(current, operation, baseline)
                _check_selected_ownership(action, actual, scope["principal_id"])
                context.review_check(copy.deepcopy(operation), copy.deepcopy(action), copy.deepcopy(current), context=context)
                context.check()
                result["stage"] = "intent_fence"
                begun = store.begin_review_action(
                    operation_id, scope, action["id"], expires_at=expires_at, checkpoint=checkpoint,
                )
                if not begun["new_attempt"]:
                    result["status"] = "unknown"
                    break
                operation = store.inspect_review(operation_id, scope, expires_at=expires_at, checkpoint=checkpoint)
                # Persistence and credential renewal may change eligibility.
                current = context.read_state()
                actual = _check_candidate(current, operation, baseline)
                _check_selected_ownership(action, actual, scope["principal_id"])
                context.review_check(copy.deepcopy(operation), copy.deepcopy(action), copy.deepcopy(current), context=context)
                context.check()
                result["stage"] = "effect"
                result["entered_writes"] += 1
                native = context.perform(copy.deepcopy(operation), copy.deepcopy(action))
                result["stage"] = "receipt"
                recorded = store.record_review_receipt(
                    operation_id, scope, action["id"], begun["action"]["attempt_id"], native.receipt,
                    expires_at=expires_at, checkpoint=checkpoint, confirm=native.confirmed,
                )
                if recorded["state"] != "confirmed":
                    result["status"] = "unknown"
                    break
                result["stage"] = "readback"
                operation = store.inspect_review(operation_id, scope, expires_at=expires_at, checkpoint=checkpoint)
                current = context.read_state()
                _check_candidate(current, operation, baseline)
        else:
            result["status"] = "complete"
    except PermissionError:
        raise  # Exact existing owner denial; the retained attempt is not rollback.
    except ReviewCandidateChanged:
        result["status"], result["errors"] = "candidate_changed", ["candidate_changed"]
    except (ReviewContextUnavailable, TimeoutError, OSError, ValueError):
        result["status"], result["errors"] = "unknown", ["selected_owner_or_result_unavailable"]
    operation = store.inspect_review(operation_id, scope, expires_at=expires_at, checkpoint=checkpoint)
    result["actions"] = operation["actions"]
    result["elapsed_seconds"] = clock() - start
    return result
