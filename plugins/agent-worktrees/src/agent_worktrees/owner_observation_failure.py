"""Failure qualification follows proven mutation admission, not exception shape."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import owner_observation_contract as c

if TYPE_CHECKING:
    from .owner_observation_history import CanonicalObservation, OwnerObservationHistory


class MutationFailure(c.ObservationError):
    def __init__(self, message: str, *, invalidated: bool) -> None:
        super().__init__(message, "journal-write-failed" if invalidated else "unavailable")
        self.invalidated = invalidated


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
    if boundary is None:
        return MutationFailure(
            f"owner operation failed before admission: {type(error).__name__}: {error}",
            invalidated=False,
        )
    selected = c.Selection(*record.key, record.head_session_id)
    try:
        with owner._lock, closing(owner._connect(write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            owner._fence(conn)
            active = conn.execute(
                "SELECT generation FROM owner_observation_active WHERE project=? AND worktree=?",
                record.key,
            ).fetchone()
            current = None if active is None else owner._row(conn, selected)
            matches_before = (current is None and boundary.generation is None) or (
                current is not None
                and (current["generation"], current["source"], current["position"])
                == (boundary.generation, boundary.source, boundary.position)
                and signature(current) == boundary.signature
            )
            matches_after = (
                current is not None
                and boundary.after_generation is not None
                and (current["generation"], current["position"])
                == (boundary.after_generation, boundary.after_position)
                and current["source"] == record.source
                and current["head"] == record.head_session_id
                and current["facts"] == c.encode(record.facts)
                and current["fence"] == c.encode(record.record_fence)
                and current["availability"] == record.availability
                and current["completeness"] == record.completeness
                and current["observed_at"] == record.observed_at
            )
            if not matches_before and not matches_after:
                return MutationFailure(
                    "admitted owner source advanced before failure reconciliation",
                    invalidated=False,
                )
            owner._fence(conn)
            owner.append_failed(record.key, error)
    except (OSError, sqlite3.Error):
        return MutationFailure(
            "owner failure could not reestablish its admitted source fence", invalidated=False
        )
    return MutationFailure("admitted owner mutation requires reconciliation", invalidated=True)
