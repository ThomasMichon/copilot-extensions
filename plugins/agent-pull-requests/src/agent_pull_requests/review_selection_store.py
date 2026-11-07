"""Pre-approval data transitions under ReplayStore's existing transaction owner."""

from __future__ import annotations

import copy
from collections.abc import Callable

from . import review_intent
from .review_workspace import _digest
from .watch_replay_store import ReplayStore, ReplayStoreInvalid


class ReviewSelectionStore:
    """Retain one final selection, without allocating identities or admitting execution."""

    def __init__(self, owner: ReplayStore) -> None:
        if not isinstance(owner, ReplayStore):
            raise ValueError("existing replay owner required")
        self.owner = owner

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

    def retain(
        self, original: dict, prepared: dict, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Commit bounded owner-supplied preparation before exposing its final preview."""
        original = review_intent.intent(original)
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

        def retain(document: dict) -> dict:
            previous = self._previous(document, original)
            if previous is not None:
                return {"status": "existing", "operation": previous}
            operation = review_intent.new_operation(
                prepared, created_at=self.owner.clock(),
                receipt_bytes=document["capacities"]["receipt_bytes"],
            )
            operation["selection"] = {"input_id": _digest(original), "stage": "prepared"}
            operation["reserved_bytes"] += len(review_intent.encoded(operation["selection"])) + 32
            document["reviews"].append(operation)
            return {"status": "prepared", "operation": operation}

        return self.owner._review_transaction(retain, expires_at=expires_at, checkpoint=checkpoint)
