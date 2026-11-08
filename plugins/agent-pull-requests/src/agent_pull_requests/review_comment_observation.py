"""Body-free joins of original prepared receipts to existing cached observations."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from . import review_intent
from .generation_cache import GenerationCache
from .review_executor import _threads
from .review_workspace import _digest, _scope, _section
from .watch_replay_store import ReplayStore


def contract() -> dict:
    """Optional executing-receiver support, not storage adoption or freshness."""
    return {
        "protocol": "agent-pull-requests.comment-observation/v1",
        "result_protocol": "agent-pull-requests.prepared-comment-observation/v1",
        "generation_versions": [1], "effects": "none", "original_version_proof": False,
    }


def supports(value: object) -> bool:
    expected = contract()
    return (
        isinstance(value, dict) and set(value) == set(expected)
        and value["protocol"] == expected["protocol"]
        and value["result_protocol"] == expected["result_protocol"]
        and type(value["generation_versions"]) is list and len(value["generation_versions"]) == 1
        and type(value["generation_versions"][0]) is int and value["generation_versions"] == [1]
        and value["effects"] == "none" and value["original_version_proof"] is False
    )


def observe_prepared_comment(
    store: ReplayStore, cache: GenerationCache, operation_id: str, scope: dict,
    action_id: str, generation_id: str, *, expires_at: int,
    checkpoint: Callable[[], None] | None = None,
) -> dict:
    """Select through the existing owners; no acquisition, write or feedback progress.

    The embedding supplies its authenticated scope and existing storage owners,
    not caller-asserted execution authority. The optional workspace receiver
    owns negotiation of its separate wire operation.
    """
    scope = _scope(scope)
    review_intent.text(action_id, 128)
    review_intent.identifier(generation_id)
    if (
        cache.owner_root != store.root
        or cache.expected_descriptor["namespace"] != store.namespace
        or cache.expected_descriptor["service"] != (
            store.service_identity.document() if store.service_identity else None
        )
    ):
        raise ValueError("comment observation cache must share the existing storage owner")
    operation = store.inspect_review(
        operation_id, scope, expires_at=expires_at, checkpoint=checkpoint,
    )
    selected = next(
        (row for row in operation["intent"]["actions"] if row["id"] == action_id), None,
    )
    if selected is None:
        raise ValueError("selected review action missing")
    state = next(row for row in operation["actions"] if row["id"] == action_id)
    result = {
        "protocol": contract()["result_protocol"],
        "operation_id": operation_id, "action_id": action_id, "scope": scope,
        "intent_id": operation["intent_id"], "action_state": state["state"],
        "receipt": copy.deepcopy(state["receipt"]),
        "generation_id": generation_id, "observation": None,
        "effects": "none", "fresh_native_read": False,
        "original_version_proven": False, "feedback_progress": False,
    }
    generation = cache.lookup(scope, generation_id=generation_id)["generation"]
    store.check_current(expires_at, checkpoint)
    projected = _project(result, selected, operation["intent"]["version"], generation)
    store.check_current(expires_at, checkpoint)
    return projected


@dataclass
class _Acquired:
    observation: dict | None = None
    threads: dict = field(default_factory=dict)
    comments: dict = field(default_factory=dict)
    error: str | None = None
    object_ids: dict = field(default_factory=dict)


def _acquired(generation: dict, scope: dict) -> _Acquired:
    """Normalize once for all selected actions in one already-acquired generation."""
    section = generation["sections"]["discussions"]
    if section["data"] is None:
        return _Acquired(error="discussions_unavailable")
    acquired = _Acquired()
    try:
        for field in ("observed_from", "observed_to"):
            review_intent.text(section[field], 128)
        # Valid acquisition bounds survive unusable or duplicate native objects.
        metadata = _section("discussions", {**section, "data": []}, scope)
        acquired.observation = {
            "observed_from": metadata["observed_from"], "observed_to": metadata["observed_to"],
            "complete": metadata["complete"], "error_count": len(metadata["errors"]),
        }
        normalized = _section("discussions", section, scope)
        acquired.threads = _threads(normalized["data"])
    except (ValueError, TypeError):
        acquired.error = "discussions_invalid"
        return acquired
    acquired.comments = {
        (thread_id, comment["id"]): comment
        for thread_id, thread in acquired.threads.items() for comment in thread["comments"]
    }
    return acquired


def _project(
    result: dict, selected: dict, version: int, generation: dict | None, *, acquired: _Acquired | None = None,
) -> dict:
    def outcome(status: str, reason: str) -> dict:
        return {**result, "status": status, "reason": reason}

    if version != 2 or "preparation" not in selected:
        return outcome("unavailable", "original_preparation_missing")
    result.update(
        identity=copy.deepcopy(selected["preparation"]["identity"]),
        body_id=selected["preparation"]["body_id"],
    )
    if generation is None:
        return outcome("unavailable", "generation_missing")
    acquired = acquired if acquired is not None else _acquired(generation, result["scope"])
    if acquired.observation is not None:
        result["observation"] = {
            **acquired.observation, "revision": copy.deepcopy(generation["revision"]), "freshness": "retained",
        }
    if acquired.error is not None:
        return outcome("unavailable", acquired.error)
    if not acquired.observation["complete"]:
        return outcome("unavailable", "discussions_incomplete")
    if result["action_state"] != "confirmed":
        return outcome("unavailable", "original_action_not_confirmed")
    receipt = result["receipt"]
    if receipt is None or not review_intent.confirms(receipt, selected, result["scope"]):
        return outcome("unavailable", "original_native_target_missing")
    ids = receipt["platform_ids"]
    result["native_target"] = {name: ids[name] for name in ("thread_id", "comment_id")}
    thread = acquired.threads.get(ids["thread_id"])
    if thread is None:
        return outcome("unavailable", "native_thread_missing")
    target = (ids["thread_id"], ids["comment_id"])
    comment = acquired.comments.get(target)
    if comment is None:
        return outcome("unavailable", "native_comment_missing")
    if target not in acquired.object_ids:
        acquired.object_ids[target] = _digest({
            "thread": {key: value for key, value in thread.items() if key != "comments"},
            "comment": comment,
        })
    result["current_object_id"] = acquired.object_ids[target]
    if thread.get("isDeleted") or comment.get("isDeleted"):
        return outcome("conflict", "native_target_deleted")
    actor = (comment.get("author") or {}).get("id")
    try:
        review_intent.text(actor)
    except ValueError:
        return outcome("unavailable", "native_actor_missing")
    result["native_actor_id"] = actor
    if actor != result["scope"]["principal_id"]:
        return outcome("conflict", "native_actor_changed")
    kind = comment.get("commentType")
    if not (type(kind) is int and kind == 1 or kind == "text"):
        return outcome("conflict", "native_comment_not_text")
    if selected["kind"] in ("comment", "reply") and (
        type(comment.get("parentCommentId")) is not int or comment["parentCommentId"] != 0
    ):
        return outcome("conflict", "native_parent_changed")
    if comment.get("content") != selected["body"]:
        return outcome("conflict", "native_content_changed")
    return outcome("correlated", "exact_prepared_content_at_retained_native_target")
