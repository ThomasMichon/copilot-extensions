"""Pre-approval data transitions under ReplayStore's existing transaction owner."""

from __future__ import annotations

import copy
import inspect
import uuid
from collections.abc import Callable

from . import review_intent
from .review_workspace import _digest
from .watch_replay_store import ReplayStore, ReplayStoreInvalid


class ReviewSelectionStore:
    """Retain one final selection without admitting execution or owning policy IO."""

    def __init__(self, owner: ReplayStore) -> None:
        if not isinstance(owner, ReplayStore):
            raise ValueError("existing replay owner required")
        self.owner = owner

    def supported(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> bool:
        """Read current explicit adoption without provisioning or changing selection state."""
        return self.owner._review_transaction(
            lambda document: document.get("review_selection_version") == 1,
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def adopt(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Explicit extension; the existing lifecycle owner excludes old strict readers."""
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document.get("review_intent_version") != 2:
                raise ReplayStoreInvalid("selection retention requires prepared review adoption")
            status = "already_adopted" if "review_selection_version" in document else "adopted"
            document["review_selection_version"] = 1
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": status, "review_selection_version": 1}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=checkpoint)

    @staticmethod
    def _previous(document: dict, original: dict) -> dict | None:
        from .terminal_retention import require_live
        require_live(
            document, original["scope"], operation_id=original["operation_id"],
            generation_id=original["generation_id"],
        )
        if document.get("review_selection_version") != 1:
            raise ReplayStoreInvalid("pre-approval selection requires explicit storage adoption")
        previous = next(
            (row for row in document["reviews"]
             if row["intent"]["operation_id"] == original["operation_id"]), None,
        )
        if previous is not None and (
            previous["intent"]["scope"] != original["scope"]
            or previous.get("selection", {}).get("input_id") != _digest(original)
        ):
            raise ValueError("review operation already has a different original selection")
        return previous

    def recover(
        self, original: dict, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict | None:
        """Read the exact original selection without composing again or changing admission."""
        original = review_intent.intent(original)
        return self.owner._review_transaction(
            lambda document: self._previous(document, original),
            expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    def inspect(
        self, operation_id: str, scope: dict, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Read retained preparation without treating it as execution-admitted."""
        from .review_workspace import _scope

        review_intent.text(operation_id, 128)
        scope = _scope(scope)

        def inspect(document: dict) -> dict:
            from .terminal_retention import require_live
            require_live(document, scope, operation_id=operation_id)
            if document.get("review_selection_version") != 1:
                raise ReplayStoreInvalid("pre-approval selection requires explicit storage adoption")
            operation = next(
                (row for row in document["reviews"]
                 if row["intent"]["operation_id"] == operation_id), None,
            )
            if operation is None or operation["intent"]["scope"] != scope:
                raise ValueError("review operation missing or outside selected scope")
            return operation

        return self.owner._review_transaction(
            inspect, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )

    @staticmethod
    def _prepared(original: dict, prepared: dict) -> dict:
        prepared = review_intent.intent(prepared, versions=(2,))
        projection = copy.deepcopy(prepared)
        projection["version"] = 1
        for action in projection["actions"]:
            action.pop("preparation", None)
            if "body" in action:
                action["body"] = next(
                    (row.get("body") for row in original["actions"] if row["id"] == action["id"]), None,
                )
        if projection != original:
            raise ValueError("preparation differs from the original action or source selection")
        return prepared

    def _retain(self, document: dict, original: dict, prepared: dict) -> dict:
        operation = review_intent.new_operation(
            prepared, created_at=self.owner.clock(),
            receipt_bytes=document["capacities"]["receipt_bytes"],
            mappings=document.get("review_comment_mapping_version") == 1,
        )
        operation["selection"] = {"input_id": _digest(original), "stage": "prepared"}
        operation["reserved_bytes"] += len(review_intent.encoded(operation["selection"])) + 32
        document["reviews"].append(operation)
        return {"status": "prepared", "operation": operation}

    def retain(
        self, original: dict, prepared: dict, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Commit bounded owner-supplied preparation before exposing its final preview."""
        original = review_intent.intent(original)
        prepared = self._prepared(original, prepared)

        def retain(document: dict) -> dict:
            previous = self._previous(document, original)
            if previous is not None:
                return {"status": "existing", "operation": previous}
            return self._retain(document, original, prepared)

        return self.owner._review_transaction(retain, expires_at=expires_at, checkpoint=checkpoint)

    def allocate(
        self, original: dict, *, compose: Callable[..., dict], expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        validate_intent: Callable[[dict], None] | None = None,
        generation: dict | None = None, source_attachment: dict | None = None,
    ) -> dict:
        """Allocate selected comments under owner custody before returning a preview.

        The trusted host must bind actual policy/profile/source before entering.
        Its synchronous compose callback is bounded pure preparation, never IO.
        Automatic edits additionally require the protected generation/source.
        """
        from .review_preparation import validate

        original = review_intent.intent(original)
        if not callable(compose) or inspect.iscoroutinefunction(compose):
            raise ValueError("bounded synchronous comment composition required")
        if validate_intent is not None and (
            not callable(validate_intent) or inspect.iscoroutinefunction(validate_intent)
        ):
            raise ValueError("bounded synchronous selection validation required")
        generation, source_attachment = copy.deepcopy(generation), copy.deepcopy(source_attachment)

        def allocate(document: dict) -> dict:
            previous = self._previous(document, original)
            if previous is not None:
                return {"status": "existing", "operation": previous}
            baseline = {}
            if any(action["kind"] == "edit_owned_comment" for action in original["actions"]):
                if generation is None or source_attachment is None:
                    raise ValueError("automatic owned edits require their original native identity binding")
                from .review_executor import _check_selected_ownership, validate_review_selection

                _, baseline = validate_review_selection(original, generation, source_attachment)
                targets = [(action["thread_id"], action["comment_id"]) for action in original["actions"]
                           if action["kind"] == "edit_owned_comment"]
                if len(set(targets)) != len(targets):
                    raise ValueError("one selected edit per native comment required")
                for action in original["actions"]:
                    _check_selected_ownership(action, baseline, original["scope"]["principal_id"])
            selected = copy.deepcopy(original)
            for action in selected["actions"]:
                if "body" not in action:
                    continue
                prior = None
                if action["kind"] == "edit_owned_comment":
                    from .review_edit_binding import previous_preparation

                    prior = previous_preparation(document, original, action, baseline)
                identity = {
                    "comment_id": prior["identity"]["comment_id"] if prior is not None else str(uuid.uuid4()),
                    "write_id": str(uuid.uuid4()),
                }
                prepared = compose(
                    action["kind"], action["body"], scope=copy.deepcopy(original["scope"]),
                    **identity, previous=prior,
                )
                if (
                    not isinstance(prepared, dict)
                    or set(prepared) != {"protocol", "identity", "body", "body_id", "effects"}
                    or prepared["protocol"] != "agent-forge-comment.preparation/v1"
                    or prepared["effects"] != "none"
                ):
                    raise ValueError("unsupported executing comment preparation")
                binding = validate(
                    {"identity": prepared["identity"], "body_id": prepared["body_id"]}, prepared["body"],
                )
                if any(binding["identity"][name] != value for name, value in identity.items()):
                    raise ValueError("preparer changed the allocated comment identities")
                action.update(body=prepared["body"], preparation=binding)
            selected["version"] = 2
            selected = self._prepared(original, selected)
            if validate_intent is not None:
                validate_intent(copy.deepcopy(selected))
            return self._retain(document, original, selected)

        return self.owner._review_transaction(allocate, expires_at=expires_at, checkpoint=checkpoint)
