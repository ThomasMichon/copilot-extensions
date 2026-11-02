"""Bounded identity-fenced materialization inside the caller's SQLite transaction."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from . import owner_observation_contract as c

if TYPE_CHECKING:
    import sqlite3

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


def append(
    owner: OwnerObservationHistory,
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    record: CanonicalObservation,
    entries: list[dict],
    batch_id: str,
    digest: str,
) -> None:
    source = owner._source(row)
    start = row["position"]
    stored, identities, pending = [], [], {}
    for offset, item in enumerate(entries, 1):
        original = item["source_ref"]
        identity = original if original["record_id"] is not None else [batch_id, offset]
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
        ):
            raise c.ObservationError(
                "duplicate source records cannot replace current facts", "event-conflict"
            )
        conn.execute(
            "INSERT INTO owner_observation_batch VALUES (?,?,?,?)",
            (row["generation"], batch_id, digest, start),
        )
        return
    candidate = dict(row)
    candidate.update(
        position=start + len(stored),
        head=record.head_session_id,
        facts=c.encode(record.facts),
        availability=record.availability,
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
    gap = (
        owner._failure_reasons.get(record.key, "journal-write-failed")
        if record.key in owner._failures
        else row["gap"]
    )
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
            "partial" if gap else record.completeness,
            gap,
            row["generation"],
        ),
    )
    conn.execute(
        "INSERT INTO owner_observation_batch VALUES (?,?,?,?)",
        (row["generation"], batch_id, digest, candidate["position"]),
    )
    retain(owner, conn, row["generation"])


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
