"""Explicit capture layout adoption under the existing complete-envelope owner."""

from __future__ import annotations

from collections.abc import Callable

from .feedback_capture import FeedbackCaptures, PROTOCOL, READBACK_PROTOCOL
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
            if document["version"] in (5, 6):
                if document["capacities"]["feedback_capture_bytes"] != capture_bytes:
                    raise ValueError("feedback capture capacity differs from adopted layout")
                return {"status": "already_adopted", "version": document["version"]}
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

    def adopt_readback(
        self, *, lifecycle_checkpoint: Callable[[], None], expires_at: int,
        checkpoint: Callable[[], None] | None = None, readback_bytes: int = 8 * 1024 * 1024,
    ) -> dict:
        """Explicit data-only expansion; old captures acquire no inferred proof."""
        if not callable(lifecycle_checkpoint):
            raise ValueError("actual reader lifecycle checkpoint required")

        def adopt(document: dict) -> dict:
            lifecycle_checkpoint()
            if document["version"] == 6:
                if document["capacities"]["feedback_readback_bytes"] != readback_bytes:
                    raise ValueError("feedback readback capacity differs from adopted layout")
                return {"status": "already_adopted", "version": 6}
            if document["version"] != 5:
                raise ReplayStoreInvalid("readback requires explicit feedback capture adoption first")
            document["version"] = 6
            document["feedback_captures"] = {
                "protocol": READBACK_PROTOCOL,
                "records": [
                    {**row, "readback": None} for row in document["feedback_captures"]["records"]
                ],
            }
            document["capacities"]["feedback_readback_bytes"] = readback_bytes
            self.owner._validate_owner(document)
            lifecycle_checkpoint()
            return {"status": "adopted", "version": 6}

        return self.owner._review_transaction(adopt, expires_at=expires_at, checkpoint=checkpoint)

    def snapshot(
        self, *, expires_at: int, checkpoint: Callable[[], None] | None = None,
    ) -> dict:
        def read(document: dict) -> dict:
            if document["version"] not in (5, 6):
                raise ReplayStoreInvalid("feedback capture requires explicit owner adoption")
            protocol = READBACK_PROTOCOL if document["version"] == 6 else PROTOCOL
            return FeedbackCaptures(document["feedback_captures"], protocol=protocol).snapshot()

        return self.owner._review_transaction(
            read, expires_at=expires_at, checkpoint=checkpoint, persist=False,
        )
