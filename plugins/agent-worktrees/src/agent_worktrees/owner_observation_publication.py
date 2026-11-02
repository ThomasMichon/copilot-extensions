"""Publication admission and materialization are separate owner-fenced commits."""

from __future__ import annotations

import sqlite3
from contextlib import closing

from . import owner_observation_contract as c
from . import owner_observation_intent as intents
from .owner_observation_append import append, reject_overflow_conflicts


def same_capture(row, record) -> bool:
    return (
        row["facts"] == c.encode(record.facts)
        and row["fence"] == c.encode(record.record_fence)
        and row["head"] == record.head_session_id
        and row["availability"] == record.availability
        and row["completeness"] == record.completeness
    )


def admit(owner, conn, record, entries, batch_id, expected_cursor, digest=None):
    row = owner._row(conn, c.Selection(*record.key, record.head_session_id), _pending=False)
    if row["source"] != record.source:
        raise c.ObservationError("record source incarnation changed", "source-replaced")
    duplicate = conn.execute(
        "SELECT digest FROM owner_observation_batch WHERE generation=? AND id=?",
        (row["generation"], batch_id),
    ).fetchone()
    if duplicate is not None:
        if digest is None or duplicate[0] != digest:
            raise c.ObservationError(
                "batch identity conflicts with accepted content", "batch-conflict"
            )
        return row, False
    if c.sequence(expected_cursor, owner._source(row)) != row["position"]:
        raise c.ObservationError("owner observation position changed", "stale-cursor")
    novel = reject_overflow_conflicts(conn, row, owner._source(row), batch_id, entries)
    if not novel and not same_capture(row, record):
        raise c.ObservationError(
            "duplicate source records cannot replace current facts", "event-conflict"
        )
    return row, novel


def publish(owner, record, entries, digest, batch_id, expected_cursor, delivery_failure):
    selected = c.Selection(*record.key, record.head_session_id)
    token = None
    staged = None
    with owner._lock:
        try:
            with closing(owner._connect(write=True)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                owner._fence(conn)
                row, novel = admit(owner, conn, record, entries, batch_id, expected_cursor, digest)
                duplicate = conn.execute(
                    "SELECT 1 FROM owner_observation_batch WHERE generation=? AND id=?",
                    (row["generation"], batch_id),
                ).fetchone()
                if duplicate is None:
                    if novel:
                        staged = intents.start(
                            owner, conn, record, row, kind="publish", batch=batch_id
                        )
                    else:
                        append(owner, conn, row, record, entries, batch_id, digest)
                owner._fence(conn)
            token = staged
            if token is not None:
                intents.checkpoint(owner, token)
                with closing(owner._connect(write=True)) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = intents.verify(owner, conn, token, record)
                    admit(owner, conn, record, entries, batch_id, expected_cursor, digest)
                    materialized = append(owner, conn, row, record, entries, batch_id, digest)
                    intents.resolve(owner, conn, token)
                if materialized:
                    owner._clear_failure(record.key, token.generation, record.source)
        except (OSError, sqlite3.Error) as error:
            raise intents.fail(owner, record, token, error) from error
        except c.ObservationError as error:
            if token is not None and error.code in {"oversized", "cursor-unavailable"}:
                delivery_failure(
                    owner,
                    selected,
                    record,
                    expected_cursor,
                    error,
                    batch_id=batch_id,
                    entries=entries,
                    admission=token,
                )
                raise c.ObservationError(
                    "admitted owner publication requires reconciliation", "cursor-unavailable"
                ) from error
            raise
        try:
            return owner.snapshot(selected)[0]
        except (OSError, sqlite3.Error) as error:
            # The update and intent resolution already committed; this is a read error.
            raise intents.reply_failed(error, admitted=token is not None) from error
