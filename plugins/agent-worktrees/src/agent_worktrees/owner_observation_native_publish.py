"""A native-only dual-fence path; ordinary post-save validation stays strict."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import owner_observation_contract as c
from . import owner_observation_native_entries as semantic
from .owner_observation_append import delivery_failed, freeze_record
from .owner_observation_publication import publish
from .tracking_save_capture import SaveReceipt

if TYPE_CHECKING:
    from .owner_observation_history import CanonicalObservation
    from .owner_observation_runtime import OwnerObservationRuntime


@dataclass(frozen=True)
class NativeProof:
    receipt: SaveReceipt
    verb: str
    guard: Callable[[], None]
    receipt_digest: str

    def check(self) -> None:
        self.guard()
        semantic.validate(self.receipt, self.verb)
        if semantic.digest(self.receipt) != self.receipt_digest:
            raise c.ObservationError("native save evidence changed", "event-conflict")

    def captured_head(self, entry: dict) -> str | None:
        return self.receipt.after.identity.head_session_id


def validate_current(current: CanonicalObservation, receipt: SaveReceipt) -> None:
    """Compare allocation identity; intake/proof retain path, epoch and writer fences."""
    identity = receipt.after.identity
    if identity is None:
        raise c.ObservationError("native record identity is unknown", "unqualified-native")
    row = current.facts["row"]
    if (current.project, current.worktree_id, row.get("creation_nonce")) != (
        identity.project,
        identity.worktree_id,
        identity.incarnation,
    ):
        raise c.ObservationError("native record incarnation/namespace changed", "source-replaced")


def publish_native(
    runtime: OwnerObservationRuntime,
    current: CanonicalObservation,
    proof: NativeProof,
) -> dict:
    proof.check()
    current = freeze_record(current)
    validate_current(current, proof.receipt)
    values = semantic.entries(proof.receipt, proof.verb)
    if not values:
        return {"native_no_change": True}
    # Freeze and validate the event batch independently from current-view facts.
    raw = c.utf8(c.encode(values, c.BODY_BYTES))
    values = c.decode(raw, c.BODY_BYTES)
    for entry in values:
        c.entry(entry, post_save=True)
    with runtime._operations:
        runtime._admit()
        proof.check()
        selected = c.Selection(current.project, current.worktree_id, current.head_session_id)
        previous, cursor, _journal = runtime.history.last_record(selected)
        if previous.source != current.source:
            raise c.ObservationError("native journal source changed", "source-replaced")
        digest = hashlib.sha256()
        for part in (
            c.utf8(c.encode(current.source)),
            raw,
            proof.receipt_digest.encode("ascii"),
        ):
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
        result = publish(
            runtime.history,
            current,
            values,
            digest.hexdigest(),
            "native:" + proof.receipt.receipt_id,
            cursor,
            delivery_failed,
            native=proof,
        )
        runtime._changed((current.project, current.worktree_id))
        return result
