"""Explicit source qualification and fenced incarnation replacement."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from typing import TYPE_CHECKING

from . import owner_observation_contract as c
from . import owner_observation_failure as failures

if TYPE_CHECKING:
    from .owner_observation_history import CanonicalObservation, OwnerObservationHistory


def _apply(owner, record, expected_source_generation, boundaries):
    selected = c.Selection(*record.key, record.head_session_id)
    replacement = None
    with closing(owner._connect(write=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        owner._fence(conn)
        active = conn.execute(
            "SELECT generation FROM owner_observation_active WHERE project=? AND worktree=?",
            record.key,
        ).fetchone()
        old = None if active is None else owner._row(conn, selected)
        if old is not None and old["source"] == record.source:
            same = (
                old["facts"] == c.encode(record.facts)
                and old["head"] == record.head_session_id
                and old["fence"] == c.encode(record.record_fence)
                and old["availability"] == record.availability
                and old["completeness"] == record.completeness
            )
            if not same:
                raise c.ObservationError(
                    "changed record requires cursor-fenced publication", "stale-record"
                )
            boundaries.append(failures.before(old))
            conn.execute(
                "UPDATE owner_observation_journal SET qualified=?,observed_at=? "
                "WHERE generation=?",
                (owner.resident_generation, record.observed_at, old["generation"]),
            )
        else:
            if old is not None and old["generation"] != expected_source_generation:
                raise c.ObservationError(
                    "source replacement needs the current incarnation", "source-replaced"
                )
            boundaries.append(failures.before(old))
            if old is not None and record.key in owner._failures:
                conn.execute(
                    "UPDATE owner_observation_journal SET gap=? WHERE generation=?",
                    (
                        owner._failure_reasons.get(record.key, "journal-write-failed"),
                        old["generation"],
                    ),
                )
            replacement = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO owner_observation_journal VALUES (?,?,?,?,?,?,0,0,?,?,?,?,?,NULL)",
                (
                    replacement,
                    record.project,
                    record.worktree_id,
                    record.source,
                    record.head_session_id,
                    owner.resident_generation,
                    c.encode(record.facts),
                    c.encode(record.record_fence),
                    record.observed_at,
                    record.availability,
                    record.completeness,
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO owner_observation_active VALUES (?,?,?)",
                (*record.key, replacement),
            )
        boundaries[0] = failures.after(boundaries[0], owner._row(conn, selected))
        owner._fence(conn)
    return replacement


def initialize(
    owner: OwnerObservationHistory,
    record: CanonicalObservation,
    expected_source_generation: str | None,
) -> dict:
    selected = c.Selection(*record.key, record.head_session_id)
    with owner._lock:
        boundaries = []
        try:
            replacement = _apply(owner, record, expected_source_generation, boundaries)
            if replacement is not None:
                with owner._read() as conn:
                    current = owner._row(conn, selected)
                    if (
                        current["generation"] != replacement
                        or current["source"] != record.source
                        or current["qualified"] != owner.resident_generation
                    ):
                        raise c.ObservationError(
                            "replacement changed before qualification", "source-replaced"
                        )
                owner._failures.pop(record.key, None)
                owner._failure_reasons.pop(record.key, None)
            return owner.snapshot(selected)[0]
        except (OSError, sqlite3.Error) as error:
            boundary = boundaries[0] if boundaries else None
            raise failures.qualify(owner, record, boundary, error) from error
