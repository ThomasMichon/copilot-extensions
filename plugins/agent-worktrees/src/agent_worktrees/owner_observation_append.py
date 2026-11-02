"""Bounded identity-fenced materialization inside the caller's SQLite transaction."""

from __future__ import annotations

import copy
import hashlib
import sqlite3
from contextlib import closing
from dataclasses import asdict
from typing import TYPE_CHECKING

from . import owner_observation_contract as c
from .owner_observation_failure import MutationFailure

if TYPE_CHECKING:
    from .owner_observation_history import CanonicalObservation, OwnerObservationHistory

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS owner_observation_owner "
    "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT, writer TEXT)",
    "CREATE TABLE IF NOT EXISTS owner_observation_active "
    "(project TEXT, worktree TEXT, generation TEXT, PRIMARY KEY(project,worktree))",
    "CREATE TABLE IF NOT EXISTS owner_observation_journal "
    "(generation TEXT PRIMARY KEY, project TEXT, worktree TEXT, source TEXT, head TEXT, "
    "qualified TEXT, position INTEGER, floor INTEGER, facts TEXT, fence TEXT, observed_at TEXT, "
    "availability TEXT, completeness TEXT, gap TEXT)",
    "CREATE TABLE IF NOT EXISTS owner_observation_entry "
    "(generation TEXT, position INTEGER, id TEXT, payload TEXT, bytes INTEGER, "
    "PRIMARY KEY(generation,position), UNIQUE(generation,id))",
    "CREATE TABLE IF NOT EXISTS owner_observation_batch "
    "(generation TEXT, id TEXT, digest TEXT, position INTEGER, PRIMARY KEY(generation,id))",
    "CREATE TABLE IF NOT EXISTS owner_observation_identity "
    "(generation TEXT, id TEXT, digest TEXT, position INTEGER, PRIMARY KEY(generation,id))",
)
MAX_GUARDS = 50000


def freeze_record(record: CanonicalObservation) -> CanonicalObservation:
    from .owner_observation_history import CanonicalObservation

    if not isinstance(record, CanonicalObservation):
        raise c.ObservationError("owner capture must be a canonical observation")
    capture = c.utf8(c.encode(asdict(record), c.BODY_BYTES - 2048))
    return CanonicalObservation(**c.decode(capture, c.BODY_BYTES - 2048))


def prepare(
    record: CanonicalObservation, entries: list[dict], post_save: bool
) -> tuple[CanonicalObservation, list, str]:
    if type(post_save) is not bool or not isinstance(entries, list) or not 1 <= len(entries) <= 64:
        raise c.ObservationError("observation publication requires a bounded explicit entry batch")
    record = freeze_record(record)
    capture = c.utf8(c.encode(asdict(record), c.BODY_BYTES - 2048))
    c.bounded(entries, 3)
    frozen = copy.deepcopy(entries)
    for item in frozen:
        c.entry(item, post_save=post_save, _check_size=False)
        if item["evidence_kind"] == "persisted-mutation" and c.encode(
            item["record_fence"]
        ) != c.encode(record.record_fence):
            raise c.ObservationError("post-save evidence does not match the selected record fence")
    for item in frozen:
        c.entry(item, post_save=post_save)
    c.encode(frozen, c.BODY_BYTES)
    digest = hashlib.sha256()
    for part in (capture, c.utf8(c.encode(frozen, c.BODY_BYTES)), c.utf8(c.encode(post_save))):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return record, frozen, digest.hexdigest()


def delivery_failed(
    owner: OwnerObservationHistory,
    selected: c.Selection,
    record: CanonicalObservation,
    expected_cursor: str,
    error: c.ObservationError,
) -> None:
    try:
        with owner._lock, closing(owner._connect(write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            owner._fence(conn)
            row = owner._row(conn, selected)
            if row["source"] != record.source:
                raise c.ObservationError("record source incarnation changed", "source-replaced")
            if c.sequence(expected_cursor, owner._source(row)) != row["position"]:
                raise c.ObservationError("owner observation position changed", "stale-cursor")
            owner._fence(conn)
            owner._mark_failed(row, error, reason="cursor-unavailable")
    except (OSError, sqlite3.Error) as failure:
        raise MutationFailure(
            f"delivery failure could not establish admission: {type(failure).__name__}: {failure}",
            invalidated=False,
        ) from failure


def append(
    owner: OwnerObservationHistory,
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    record: CanonicalObservation,
    entries: list[dict],
    batch_id: str,
    digest: str,
) -> bool:
    source = owner._source(row)
    start = row["position"]
    stored, identities, pending = [], [], {}
    for offset, item in enumerate(entries, 1):
        original = item["source_ref"]
        identity = (
            {key: original[key] for key in ("source", "generation", "record_id")}
            if original["record_id"] is not None
            else [batch_id, offset]
        )
        event_id = (
            "ow1:"
            + hashlib.sha256(
                c.utf8(c.encode([source.namespace(), source.source_generation, identity])),
            ).hexdigest()
        )
        entry_digest = hashlib.sha256(c.utf8(c.encode(item))).hexdigest()
        prior = conn.execute(
            "SELECT digest FROM owner_observation_identity WHERE generation=? AND id=?",
            (row["generation"], event_id),
        ).fetchone()
        known = pending.get(event_id) if prior is None else prior[0]
        if known is not None:
            if known != entry_digest:
                raise c.ObservationError(
                    "source record identity changed content", "event-conflict"
                )
            continue
        seq = start + len(stored) + 1
        if seq > c.MAX_POSITION:
            raise c.ObservationError("owner position exhausted", "unavailable")
        value = {
            **item,
            "event_id": event_id,
            "cursor": c.position(source, seq),
            "head_session_id": record.head_session_id,
        }
        c.entry(value, stored=True, post_save=True)
        stored.append(value)
        pending[event_id] = entry_digest
        identities.append((row["generation"], event_id, entry_digest, seq))
    for table, added in (("identity", len(identities)), ("batch", 1)):
        count = conn.execute(
            f"SELECT count(*) FROM owner_observation_{table} WHERE generation=?",
            (row["generation"],),
        ).fetchone()[0]
        if count + added > MAX_GUARDS:
            raise c.ObservationError("owner identity guard bound reached", "cursor-unavailable")
    if not stored:
        if (
            row["facts"] != c.encode(record.facts)
            or row["head"] != record.head_session_id
            or row["fence"] != c.encode(record.record_fence)
            or row["availability"] != record.availability
            or row["completeness"] != record.completeness
        ):
            raise c.ObservationError(
                "duplicate source records cannot replace current facts", "event-conflict"
            )
        conn.execute(
            "INSERT INTO owner_observation_batch VALUES (?,?,?,?)",
            (row["generation"], batch_id, digest, start),
        )
        return False
    candidate = dict(row)
    candidate.update(
        position=start + len(stored),
        head=record.head_session_id,
        facts=c.encode(record.facts),
        availability=record.availability,
        completeness=record.completeness,
    )
    source = owner._source(candidate)
    body = owner._body(candidate, c.Selection(*record.key, record.head_session_id))
    body.update(
        base_cursor=c.position(source, start),
        entries=stored,
        facts=record.facts if body["availability"] == "observed" else None,
    )
    c.frame("delta", source, c.position(source, candidate["position"]), body)
    for value in stored:
        text = c.encode(value, c.BODY_BYTES - 1024)
        conn.execute(
            "INSERT INTO owner_observation_entry VALUES (?,?,?,?,?)",
            (
                row["generation"],
                c.sequence(value["cursor"], source),
                value["event_id"],
                text,
                len(c.utf8(text)),
            ),
        )
    conn.executemany("INSERT INTO owner_observation_identity VALUES (?,?,?,?)", identities)
    pending_failure = owner.pending_failure(row)
    gap = pending_failure.reason if pending_failure is not None else row["gap"]
    conn.execute(
        "UPDATE owner_observation_journal SET head=?,qualified=?,position=?,facts=?,fence=?,"
        "observed_at=?,availability=?,completeness=?,gap=? WHERE generation=?",
        (
            record.head_session_id,
            owner.resident_generation,
            candidate["position"],
            c.encode(record.facts),
            c.encode(record.record_fence),
            record.observed_at,
            record.availability,
            record.completeness,
            gap,
            row["generation"],
        ),
    )
    conn.execute(
        "INSERT INTO owner_observation_batch VALUES (?,?,?,?)",
        (row["generation"], batch_id, digest, candidate["position"]),
    )
    retain(owner, conn, row["generation"])
    return True


def retain(owner: OwnerObservationHistory, conn: sqlite3.Connection, journal: str) -> None:
    rows = conn.execute(
        "SELECT position,bytes FROM owner_observation_entry "
        "WHERE generation=? ORDER BY position DESC",
        (journal,),
    ).fetchall()
    used, keep = 0, 0
    for item in rows:
        if keep >= owner.max_entries or used + item["bytes"] > owner.max_bytes:
            break
        used, keep = used + item["bytes"], keep + 1
    removed = rows[keep:]
    if removed:
        floor = max(item["position"] for item in removed)
        conn.execute(
            "DELETE FROM owner_observation_entry WHERE generation=? AND position<=?",
            (journal, floor),
        )
        conn.execute(
            "UPDATE owner_observation_journal SET floor=? WHERE generation=?", (floor, journal)
        )
