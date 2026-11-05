"""Explicit capture layout adoption under the existing complete-envelope owner."""

from __future__ import annotations

from collections.abc import Callable

from .feedback_capture import FeedbackCaptures
from .watch_replay_store import ReplayStore, ReplayStoreInvalid


class FeedbackCaptureStore:
    """Data expansion only; no capture admission, feedback sender or handled writer."""

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
        capture_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document["version"] == 5:
                if document["capacities"]["feedback_capture_bytes"] != capture_bytes:
                    raise ValueError("feedback capture capacity differs from adopted layout")
                return {"status": "already_adopted", "version": 5}
            if document["version"] != 4:
                raise ReplayStoreInvalid("feedback capture requires explicit attention adoption first")
            document["version"] = 5
            document["feedback_captures"] = FeedbackCaptures().snapshot()
            document["capacities"]["feedback_capture_bytes"] = capture_bytes
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": "adopted", "version": 5}

        return self.owner._review_transaction(
            adopt, expires_at=expires_at, checkpoint=checkpoint,
        )

    def snapshot(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        def read(document: dict) -> dict:
            if document["version"] != 5:
                raise ReplayStoreInvalid("feedback capture requires explicit owner adoption")
            return FeedbackCaptures(document["feedback_captures"]).snapshot()

        return self.owner._review_transaction(
            read, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
