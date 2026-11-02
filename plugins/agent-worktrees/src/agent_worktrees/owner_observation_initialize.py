"""Fresh explicit capture qualification after durable, source-fenced admission."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing

from . import owner_observation_contract as c
from . import owner_observation_intent as intents


def _admit(owner, conn, record, expected_source_generation):
    selected = c.Selection(*record.key, record.head_session_id)
    active = conn.execute(
        "SELECT generation FROM owner_observation_active WHERE project=? AND worktree=?",
        record.key,
    ).fetchone()
    old = None if active is None else owner._row(conn, selected, _pending=False)
    if old is not None and old["source"] == record.source:
        from .owner_observation_publication import same_capture

        if not same_capture(old, record):
            raise c.ObservationError(
                "changed record requires cursor-fenced publication", "stale-record"
            )
        if (
            conn.execute(
                "SELECT 1 FROM owner_observation_intent WHERE generation=? AND source=?",
                (old["generation"], record.source),
            ).fetchone()
            is not None
        ):
            return old, None
        return old, intents.start(owner, conn, record, old, kind="refresh")
    if old is not None and old["generation"] != expected_source_generation:
        raise c.ObservationError(
            "source replacement needs the current incarnation", "source-replaced"
        )
    pending = conn.execute(
        "SELECT generation FROM owner_observation_intent WHERE project=? AND worktree=? "
        "AND source=? AND base_generation IS ? LIMIT 2",
        (*record.key, record.source, None if old is None else old["generation"]),
    ).fetchall()
    if len(pending) > 1:
        raise c.ObservationError("ambiguous pending source qualification", "unavailable")
    generation = uuid.uuid4().hex if not pending else pending[0][0]
    return old, intents.start(
        owner,
        conn,
        record,
        old,
        generation=generation,
        kind="initialize" if old is None else "replacement",
    )


def _materialize(owner, conn, record, token):
    old = intents.verify(owner, conn, token, record)
    if token.kind == "refresh":
        conn.execute(
            "UPDATE owner_observation_journal SET qualified=?,observed_at=? WHERE generation=?",
            (owner.resident_generation, record.observed_at, token.generation),
        )
    else:
        if old is not None and (pending := owner.pending_failure(old)) is not None:
            intents.gap(conn, old["generation"], old["source"], pending.reason, pending.details)
        if old is not None:
            intents.archive_pending(owner, conn, old)
        conn.execute(
            "INSERT INTO owner_observation_journal VALUES (?,?,?,?,?,?,0,0,?,?,?,?,?,NULL)",
            (
                token.generation,
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
            (*record.key, token.generation),
        )
    intents.resolve(owner, conn, token)


def initialize(owner, record, expected_source_generation):
    if expected_source_generation is not None:
        c.generation(expected_source_generation)
    selected = c.Selection(*record.key, record.head_session_id)
    token = None
    with owner._lock:
        try:
            with closing(owner._connect(write=True)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                owner._fence(conn)
                old, staged = _admit(owner, conn, record, expected_source_generation)
            token = staged
            if token is not None:
                intents.checkpoint(owner, token)
                with closing(owner._connect(write=True)) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    _materialize(owner, conn, record, token)
            if token is not None and token.kind != "refresh":
                owner._clear_failure(
                    record.key, token.base_generation, None if old is None else old["source"]
                )
                owner._clear_failure(record.key, token.generation, record.source)
        except (OSError, sqlite3.Error) as error:
            raise intents.fail(owner, record, token, error) from error
        try:
            return owner.snapshot(selected)[0]
        except (OSError, sqlite3.Error) as error:
            raise intents.reply_failed(error) from error
