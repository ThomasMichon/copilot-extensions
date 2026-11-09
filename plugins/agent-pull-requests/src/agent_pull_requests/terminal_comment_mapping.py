"""Lossless acquired evidence in the existing compact original-operation owner."""

from __future__ import annotations

import copy
from collections.abc import Callable

from . import review_intent
from .review_comment_mapping import FIELD
from .review_workspace import _scope
from .terminal_recovery import TerminalRecoveryStore
from .watch_replay_store import ReplayStore, ReplayStoreInvalid

EXTENSION = "terminal_comment_mapping_version"
PROTOCOL = "agent-pull-requests.terminal-comment-observation/v1"


class TerminalCommentMappingStore:
    """Internal adopted storage/read composition; no workspace serving capability."""

    def __init__(self, owner: ReplayStore) -> None:
        if not isinstance(owner, ReplayStore) or not owner.terminal_comment_mapping:
            raise ValueError("explicit terminal comment mapping reader required")
        self.owner = owner

    def adopt(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        if not callable(lifecycle_checkpoint) or checkpoint is not None and not callable(checkpoint):
            raise ValueError("actual exclusive reader lifecycle checkpoint required")

        def current() -> None:
            if checkpoint is not None:
                checkpoint()
            lifecycle_checkpoint()

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document.get("terminal_recovery_version") != 1 or document.get("review_comment_mapping_version") != 1:
                raise ReplayStoreInvalid("terminal recovery and acquired mapping adoption required")
            status = "already_adopted" if EXTENSION in document else "adopted"
            document[EXTENSION] = 1
            lifecycle_checkpoint()
            return {"status": status, EXTENSION: 1}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=current)

    def read(
        self, operation_id: str, scope: dict, action_id: str, *, expires_at: int,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        """Historical metadata only; missing pre-adoption evidence is not an empty slot."""
        review_intent.text(operation_id, 128)
        review_intent.text(action_id, 128)
        scope = _scope(scope)

        def read(document: dict) -> dict:
            if document.get(EXTENSION) != 1:
                raise ReplayStoreInvalid("terminal comment mapping requires explicit storage adoption")
            operation = next(
                (row for row in document["reviews"] if row["intent"]["operation_id"] == operation_id), None,
            )
            if operation is None:
                operation = TerminalRecoveryStore.selected(document, scope, operation_id)
                selected = operation
                state = next((row for row in operation["actions"] if row["id"] == action_id), None)
                prepared = copy.deepcopy(state.get("prepared")) if state is not None else None
                supported = state is not None and "comment_observation" in state
                mapping = state.get("comment_observation") if state is not None else None
                representation = "compact"
            else:
                selected = operation["intent"]
                if selected["scope"] != scope:
                    raise ValueError("retained mapping outside selected scope")
                action = next((row for row in selected["actions"] if row["id"] == action_id), None)
                state = next((row for row in operation["actions"] if row["id"] == action_id), None)
                preparation = action.get("preparation") if action is not None else None
                prepared = {
                    "comment_id": preparation["identity"]["comment_id"],
                    "write_id": preparation["identity"]["write_id"],
                    "body_id": preparation["body_id"],
                } if preparation is not None else None
                supported = action_id in operation.get(FIELD, {})
                mapping = operation.get(FIELD, {}).get(action_id)
                representation = "active"
            if state is None:
                raise ValueError("selected retained action missing")
            return {
                "protocol": PROTOCOL, "representation": representation, "scope": scope,
                "operation_id": operation_id, "action_id": action_id, "intent_id": operation["intent_id"],
                **{name: selected[name] for name in ("generation_id", "source_id", "revision")},
                "prepared": prepared, "action_state": state["state"],
                "receipt": copy.deepcopy(state["receipt"]), "mapping_supported": supported,
                "mapping": copy.deepcopy(mapping), "effects": "none",
                "fresh_native_read": False, "original_version_proven": False, "feedback_progress": False,
            }

        return self.owner._review_transaction(read, expires_at=expires_at, checkpoint=checkpoint, persist=False)
