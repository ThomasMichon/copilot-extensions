"""Bounded acquired evidence under existing review-operation reservations."""

from __future__ import annotations

import copy
from collections.abc import Callable

from . import review_intent
from .review_workspace import _instant

FIELD = "comment_observations"
EXTENSION = "review_comment_mapping_version"
MAX_SLOT_BYTES = 2048
REASONS = {
    "correlated": {"exact_prepared_content_at_retained_native_target"},
    "conflict": {
        "native_target_deleted", "native_actor_changed", "native_comment_not_text",
        "native_parent_changed", "native_content_changed",
    },
    "unavailable": {
        "discussions_unavailable", "discussions_invalid", "discussions_incomplete",
        "original_action_not_confirmed", "original_native_target_missing",
        "native_thread_missing", "native_comment_missing", "native_actor_missing",
    },
}


def empty_slots(operation: dict) -> dict:
    """Only admitted structured preparation is eligible; tags never allocate slots."""
    return {
        action["id"]: None for action in operation["intent"]["actions"]
        if operation["intent"]["version"] == 2 and "preparation" in action
    }


def reservation(operation: dict) -> int:
    """Fund all slot growth separately from the original native receipt reservation."""
    slots = empty_slots(operation)
    return len(review_intent.encoded({FIELD: slots})) + len(slots) * MAX_SLOT_BYTES + 32 if slots else 0


def reserve(operation: dict) -> None:
    """Expand one existing operation without replacing intent, receipt or selection."""
    slots = empty_slots(operation)
    if slots:
        operation[FIELD] = slots
        operation["reserved_bytes"] += reservation(operation)


def validate(operation: dict, *, enabled: bool) -> int:
    """Validate metadata and return its independently funded reservation."""
    slots = empty_slots(operation) if enabled else {}
    if not slots:
        if FIELD in operation:
            raise ValueError("unexpected acquired comment mapping")
        return 0
    retained = operation.get(FIELD)
    if not isinstance(retained, dict) or set(retained) != set(slots):
        raise ValueError("invalid acquired comment mapping actions")
    for action_id, value in retained.items():
        action = next(row for row in operation["actions"] if row["id"] == action_id)
        validate_slot(value, action_state=action["state"])
    return reservation(operation)


def validate_slot(value: object, *, action_state: str) -> None:
    """The same bounded historical evidence is valid before and after compaction."""
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != {
        "generation_id", "observation", "status", "reason", "current_object_id",
    }:
        raise ValueError("invalid acquired comment mapping fields")
    review_intent.identifier(value["generation_id"])
    if (
        not isinstance(value["status"], str) or value["status"] not in REASONS
        or not isinstance(value["reason"], str)
        or value["reason"] not in REASONS[value["status"]]
    ):
        raise ValueError("invalid acquired comment mapping outcome")
    if value["current_object_id"] is not None:
        review_intent.identifier(value["current_object_id"])
    observation = value["observation"]
    if observation is not None:
        if not isinstance(observation, dict) or set(observation) != {
            "observed_from", "observed_to", "complete", "error_count",
        }:
            raise ValueError("invalid acquired comment mapping interval")
        for name in ("observed_from", "observed_to"):
            review_intent.text(observation[name], 128)
        if (
            _instant(observation["observed_from"]) > _instant(observation["observed_to"])
            or type(observation["complete"]) is not bool
            or type(observation["error_count"]) is not int or observation["error_count"] < 0
            or observation["complete"] and observation["error_count"] != 0
        ):
            raise ValueError("invalid acquired comment mapping observation")
    if value["status"] == "correlated" and (
        action_state != "confirmed" or value["current_object_id"] is None
        or observation is None or observation["complete"] is not True
    ):
        raise ValueError("acquired correlation requires original confirmation")
    if len(review_intent.encoded(value)) > MAX_SLOT_BYTES:
        raise ValueError("acquired comment mapping exceeds its slot")


def invalidate(operation: dict, action_id: str) -> None:
    """A changed receipt/attempt cannot inherit a prior acquired correlation."""
    if action_id in operation.get(FIELD, {}):
        operation[FIELD][action_id] = None


def observe(document: dict, generation: dict) -> None:
    """Called only inside admitted acquisition's existing envelope transaction."""
    if document.get(EXTENSION) != 1:
        return
    from .review_comment_observation import _acquired, _project

    acquired = _acquired(generation, generation["scope"])
    for operation in document["reviews"]:
        if operation["intent"]["scope"] != generation["scope"]:
            continue
        for selected, state in zip(operation["intent"]["actions"], operation["actions"]):
            if selected["id"] not in operation.get(FIELD, {}):
                continue
            result = _project(
                {"scope": generation["scope"], "action_state": state["state"],
                 "receipt": state["receipt"], "observation": None},
                selected, operation["intent"]["version"], generation, acquired=acquired,
            )
            observation = result["observation"]
            value = {
                "generation_id": generation["id"],
                "observation": {
                    name: observation[name] for name in (
                        "observed_from", "observed_to", "complete", "error_count",
                    )
                } if observation is not None else None,
                "status": result["status"], "reason": result["reason"],
                "current_object_id": result.get("current_object_id"),
            }
            previous = operation[FIELD][selected["id"]]
            if previous is not None and value != previous:
                old, new = previous["observation"], value["observation"]
                if old is not None and (
                    new is None or _instant(new["observed_from"]) < _instant(old["observed_to"])
                ):
                    continue
            operation[FIELD][selected["id"]] = value


class CommentMappingStore:
    """Optional data owner; no serving contract, provider or additional state file."""

    def __init__(self, owner: object) -> None:
        from .watch_replay_store import ReplayStore

        if not isinstance(owner, ReplayStore):
            raise ValueError("existing replay owner required")
        self.owner = owner

    def supported(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> bool:
        return self.owner._review_transaction(
            lambda document: document.get(EXTENSION) == 1,
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def adopt(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Explicitly exclude old strict readers and reserve existing eligible actions."""
        from .watch_replay_store import ReplayStoreInvalid

        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document.get("review_intent_version") != 2 or document["version"] not in (3, 4, 5, 6):
                raise ReplayStoreInvalid("comment mapping requires prepared review and conversation adoption")
            status = "already_adopted" if EXTENSION in document else "adopted"
            if status == "adopted":
                document[EXTENSION] = 1
                for operation in document["reviews"]:
                    reserve(operation)
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": status, EXTENSION: 1}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=checkpoint)

    def read(
        self, operation_id: str, scope: dict, action_id: str, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Read retained original correlation without needing a cached body or new IO."""
        from .watch_replay_store import ReplayStoreInvalid

        review_intent.text(action_id, 128)

        def read(document: dict) -> dict:
            if document.get(EXTENSION) != 1:
                raise ReplayStoreInvalid("comment mapping requires explicit storage adoption")
            operation = self.owner._review_operation(document, operation_id, scope)
            selected = next((row for row in operation["intent"]["actions"] if row["id"] == action_id), None)
            if selected is None:
                raise ValueError("selected review action missing")
            state = next(row for row in operation["actions"] if row["id"] == action_id)
            return {
                "protocol": "agent-pull-requests.retained-comment-observation/v1",
                "operation_id": operation_id, "action_id": action_id,
                "scope": copy.deepcopy(operation["intent"]["scope"]),
                "intent_id": operation["intent_id"],
                "identity": copy.deepcopy(selected.get("preparation", {}).get("identity")),
                "body_id": selected.get("preparation", {}).get("body_id"),
                "action_state": state["state"], "receipt": copy.deepcopy(state["receipt"]),
                "mapping": copy.deepcopy(operation.get(FIELD, {}).get(action_id)),
                "fresh_native_read": False, "original_version_proven": False, "feedback_progress": False,
            }

        return self.owner._review_transaction(read, expires_at=expires_at, checkpoint=checkpoint, persist=False)
