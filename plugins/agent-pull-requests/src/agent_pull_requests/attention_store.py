"""Explicit attention layout adoption under the existing complete-envelope owner."""

from __future__ import annotations

from collections.abc import Callable

from .attention_state import AttentionState
from .watch_replay_store import ReplayStore, ReplayStoreInvalid


class AttentionStore:
    """Data expansion only; no serving, ingest or handled-write operation."""

    def __init__(self, owner: ReplayStore) -> None:
        if not isinstance(owner, ReplayStore):
            raise ValueError("existing replay owner required")
        self.owner = owner

    def adopt(
        self,
        *,
        lifecycle_checkpoint: Callable[[], None],
        expires_at: int,
        checkpoint: Callable[[], None] | None = None,
        attention_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document["version"] in (4, 5, 6):
                if document["capacities"]["attention_bytes"] != attention_bytes:
                    raise ValueError("attention capacity differs from adopted layout")
                return {"status": "already_adopted", "version": document["version"]}
            if document["version"] != 3:
                raise ReplayStoreInvalid("attention requires explicit conversation adoption first")
            document["version"] = 4
            document["attention"] = AttentionState().snapshot()
            document["capacities"]["attention_bytes"] = attention_bytes
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": "adopted", "version": 4}

        return self.owner._review_transaction(
            adopt, expires_at=expires_at, checkpoint=checkpoint,
        )

    def snapshot(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        def read(document: dict) -> dict:
            if document["version"] not in (4, 5, 6):
                raise ReplayStoreInvalid("attention requires explicit owner adoption")
            return AttentionState(document["attention"]).snapshot()

        return self.owner._review_transaction(
            read, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
