"""Failure qualification follows proven mutation admission, not exception shape."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import owner_observation_contract as c

if TYPE_CHECKING:
    from .owner_observation_history import CanonicalObservation, OwnerObservationHistory


class MutationFailure(c.ObservationError):
    def __init__(self, message: str, *, invalidated: bool) -> None:
        super().__init__(message, "journal-write-failed" if invalidated else "unavailable")
        self.invalidated = invalidated
        self.gap_persisted = False
        self.admitted = False


@dataclass(frozen=True)
class PendingFailure:
    generation: str
    source: str
    reason: str
    details: str


@dataclass(frozen=True)
class Admission:
    generation: str | None
    source: str | None
    position: int | None
    after_generation: str | None = None
    after_position: int | None = None
    signature: tuple | None = None


def signature(row) -> tuple:
    return tuple(
        row[key]
        for key in (
            "facts",
            "fence",
            "head",
            "qualified",
            "availability",
            "completeness",
            "observed_at",
        )
    )


def before(row) -> Admission:
    return Admission(
        None if row is None else row["generation"],
        None if row is None else row["source"],
        None if row is None else row["position"],
        signature=None if row is None else signature(row),
    )


def after(boundary: Admission, row) -> Admission:
    return Admission(
        boundary.generation,
        boundary.source,
        boundary.position,
        row["generation"],
        row["position"],
        boundary.signature,
    )


def qualify(
    owner: OwnerObservationHistory,
    record: CanonicalObservation,
    boundary: Admission | None,
    error: Exception,
) -> MutationFailure:
    """An in-memory legacy boundary alone is not a committed admission witness."""
    return MutationFailure(
        f"owner failure has no committed admission intent: {type(error).__name__}: {error}",
        invalidated=False,
    )
