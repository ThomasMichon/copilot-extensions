"""Two-commit admission in the auxiliary index, not a canonical-work transaction."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass

from . import owner_observation_contract as c
from . import owner_observation_failure as failures

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS owner_observation_intent "
    "(generation TEXT PRIMARY KEY, source TEXT, project TEXT, worktree TEXT, id TEXT, "
    "owner TEXT, writer TEXT, kind TEXT, batch TEXT, base_generation TEXT, base_position INTEGER, "
    "base_signature TEXT, capture_digest TEXT, base_cursor TEXT)",
    "CREATE TABLE IF NOT EXISTS owner_observation_gap "
    "(generation TEXT PRIMARY KEY, source TEXT, reason TEXT, details TEXT, completeness TEXT)",
    "CREATE TABLE IF NOT EXISTS owner_observation_protocol "
    "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER)",
)
MAX_INTENTS = 4096


@dataclass(frozen=True)
class Intent:
    generation: str
    source: str
    project: str
    worktree: str
    id: str
    owner: str
    writer: str
    kind: str
    batch: str | None
    base_generation: str | None
    base_position: int | None
    base_signature: str | None
    capture_digest: str
    base_cursor: str | None


def row_signature(row) -> str | None:
    if row is None:
        return None
    digest = hashlib.sha256()
    for value in (row["source"], *failures.signature(row)):
        raw = c.utf8(value) if isinstance(value, str) else c.utf8(c.encode(value))
        if len(raw) > c.BODY_BYTES:
            raise c.ObservationError("stored admission signature exceeds bound", "unavailable")
        digest.update(b"s" if isinstance(value, str) else b"j")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def gap(conn, generation, source, reason, details, completeness="partial") -> None:
    c.generation(generation)
    c.reference(source, "gap source")
    c.enum(reason, c.GAP_REASONS, "gap reason")
    c.enum(completeness, frozenset({"partial", "unknown"}), "gap coverage")
    details = str(details)[:1024] or reason
    c.encode(details, 8192)
    previous = conn.execute(
        "SELECT source FROM owner_observation_gap WHERE generation=?", (generation,)
    ).fetchone()
    if previous is not None and previous[0] != source:
        raise c.ObservationError("gap journal source changed", "source-replaced")
    conn.execute(
        "INSERT INTO owner_observation_gap VALUES (?,?,?,?,?) ON CONFLICT(generation) "
        "DO UPDATE SET reason=CASE WHEN completeness='unknown' "
        "THEN excluded.reason ELSE reason END,"
        "details=CASE WHEN completeness='unknown' THEN excluded.details ELSE details END,"
        "completeness=CASE WHEN excluded.completeness='partial' "
        "THEN 'partial' ELSE completeness END",
        (generation, source, reason, details, completeness),
    )
    if completeness == "partial":
        conn.execute(
            "UPDATE owner_observation_journal SET gap=COALESCE(gap,?) "
            "WHERE generation=? AND source=?",
            (reason, generation, source),
        )


def recover(conn, *, coverage_missing=False) -> None:
    protocol = conn.execute("SELECT version FROM owner_observation_protocol").fetchone()
    if protocol is not None and protocol[0] != 1:
        raise c.ObservationError("unsupported owner admission protocol", "unavailable")
    if protocol is None or coverage_missing:
        legacy = conn.execute(
            "SELECT generation,source,gap FROM owner_observation_journal LIMIT ?",
            (MAX_INTENTS + 1,),
        ).fetchall()
        if len(legacy) > MAX_INTENTS:
            raise c.ObservationError("legacy admission coverage exceeds bound", "unavailable")
        for row in legacy:
            gap(
                conn,
                row[0],
                row[1],
                row[2] or "cursor-unavailable",
                "Admission-intent coverage before this index protocol is unknown",
                "partial" if row[2] else "unknown",
            )
        if protocol is None:
            conn.execute("INSERT INTO owner_observation_protocol VALUES (1,1)")
    pending = conn.execute(
        "SELECT * FROM owner_observation_intent LIMIT ?", (MAX_INTENTS + 1,)
    ).fetchall()
    if len(pending) > MAX_INTENTS:
        raise c.ObservationError("owner admission intent bound exceeded", "unavailable")
    for row in pending:
        gap(
            conn,
            row["generation"],
            row["source"],
            "cursor-unavailable",
            "An admitted owner operation has no committed materialization acknowledgement",
        )
        if (
            conn.execute(
                "SELECT 1 FROM owner_observation_journal WHERE generation=?", (row["generation"],)
            ).fetchone()
            is not None
        ):
            conn.execute("DELETE FROM owner_observation_intent WHERE id=?", (row["id"],))


def overlay(owner, conn, row, *, active: bool, pending: bool):
    value = dict(row)
    for table in ("owner_observation_gap", "owner_observation_intent"):
        recorded = conn.execute(
            f"SELECT source FROM {table} WHERE generation=?", (row["generation"],)
        ).fetchone()
        if recorded is not None and recorded["source"] != row["source"]:
            raise c.ObservationError("journal admission source changed", "source-replaced")
    known = conn.execute(
        "SELECT * FROM owner_observation_gap WHERE generation=? AND source=?",
        (row["generation"], row["source"]),
    ).fetchone()
    if known is not None:
        value["gap"] = value["gap"] or known["reason"]
        value["_coverage"] = known["completeness"]
        value["_gap_details"] = known["details"]
    if pending:
        unresolved = conn.execute(
            "SELECT * FROM owner_observation_intent WHERE generation=? OR "
            "(? AND base_generation=? AND base_position=? "
            "AND kind='replacement' AND owner=?) LIMIT 1",
            (
                row["generation"],
                active,
                row["generation"],
                row["position"],
                owner.resident_generation,
            ),
        ).fetchone()
        if unresolved is not None:
            uncertain = conn.execute(
                "SELECT reason,details FROM owner_observation_gap WHERE generation=? AND source=?",
                (unresolved["generation"], unresolved["source"]),
            ).fetchone()
            value["gap"] = value["gap"] or (
                "cursor-unavailable" if uncertain is None else uncertain["reason"]
            )
            value["_coverage"] = "partial"
            value["_gap_details"] = (
                "Admitted operation awaits a qualified materialization"
                if uncertain is None
                else uncertain["details"]
            )
            value["_intent_pending"] = unresolved["owner"] == owner.resident_generation
    return value


def start(owner, conn, record, row, *, generation=None, kind, batch=None) -> Intent:
    c.enum(
        kind,
        frozenset({"publish", "overflow", "initialize", "refresh", "replacement", "failure"}),
        "admitted operation",
    )
    c.reference(batch, "observation batch identity", nullable=True)
    generation = c.generation(generation or row["generation"])
    old = conn.execute(
        "SELECT * FROM owner_observation_intent WHERE generation=?", (generation,)
    ).fetchone()
    if old is not None:
        if old["source"] != record.source:
            raise c.ObservationError("admitted journal source changed", "source-replaced")
        gap(
            conn,
            generation,
            record.source,
            "cursor-unavailable",
            "Earlier admitted materialization remains unacknowledged",
        )
    elif (
        conn.execute("SELECT count(*) FROM owner_observation_intent").fetchone()[0] >= MAX_INTENTS
    ):
        raise c.ObservationError("owner admission intent bound reached", "unavailable")
    token = Intent(
        generation,
        record.source,
        *record.key,
        uuid.uuid4().hex,
        owner.resident_generation,
        owner.writer_id,
        kind,
        batch,
        None if row is None else row["generation"],
        None if row is None else row["position"],
        row_signature(row),
        hashlib.sha256(c.utf8(c.encode(asdict(record), c.BODY_BYTES - 2048))).hexdigest(),
        None if row is None else c.position(owner._source(row), row["position"]),
    )
    c.encode(asdict(token), 8192)
    owner._fence(conn)
    conn.execute(
        "INSERT OR REPLACE INTO owner_observation_intent VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(asdict(token).values()),
    )
    return token


def verify(owner, conn, token: Intent, record=None):
    owner._fence(conn)
    saved = conn.execute(
        "SELECT * FROM owner_observation_intent WHERE generation=?", (token.generation,)
    ).fetchone()
    if saved is None or dict(saved) != asdict(token):
        raise c.ObservationError("admitted operation was replaced", "stale-cursor")
    if (token.owner, token.writer) != (owner.resident_generation, owner.writer_id):
        raise c.ObservationError("admitted owner was replaced", "superseded")
    if (
        record is not None
        and hashlib.sha256(c.utf8(c.encode(asdict(record), c.BODY_BYTES - 2048))).hexdigest()
        != token.capture_digest
    ):
        raise c.ObservationError("admitted capture changed", "stale-record")
    selected = c.Selection(token.project, token.worktree, None)
    active = conn.execute(
        "SELECT generation FROM owner_observation_active WHERE project=? AND worktree=?",
        (token.project, token.worktree),
    ).fetchone()
    row = None if active is None else owner._row(conn, selected, _pending=False)
    if (
        (None if row is None else row["generation"]) != token.base_generation
        or (None if row is None else row["position"]) != token.base_position
        or row_signature(row) != token.base_signature
    ):
        raise c.ObservationError("admitted source or position advanced", "stale-cursor")
    return row


def resolve(owner, conn, token: Intent) -> None:
    owner._fence(conn)
    if (
        conn.execute(
            "DELETE FROM owner_observation_intent "
            "WHERE generation=? AND id=? AND owner=? AND writer=?",
            (token.generation, token.id, token.owner, token.writer),
        ).rowcount
        != 1
    ):
        raise c.ObservationError("admitted operation cannot be resolved", "stale-cursor")


def checkpoint(owner, token: Intent) -> None:
    if owner._after_intent is not None:
        owner._after_intent(token)


def reply_failed(error, *, admitted=True):
    result = failures.MutationFailure(
        f"index transaction committed, but its observation reply failed: {error}",
        invalidated=False,
    )
    result.admitted = admitted
    return result


def archive_pending(owner, conn, row) -> None:
    pending = conn.execute(
        "SELECT id,source FROM owner_observation_intent WHERE generation=?", (row["generation"],)
    ).fetchone()
    if pending is not None:
        if pending["source"] != row["source"]:
            raise c.ObservationError("archived admission source changed", "source-replaced")
        gap(
            conn,
            row["generation"],
            row["source"],
            "cursor-unavailable",
            "An archived source contains an unacknowledged admitted operation",
        )
        owner._fence(conn)
        conn.execute("DELETE FROM owner_observation_intent WHERE id=?", (pending["id"],))


def fail(owner, record, token: Intent | None, error, *, reason="journal-write-failed"):
    c.enum(reason, c.GAP_REASONS, "gap reason")
    if token is None:
        return failures.MutationFailure(
            f"owner operation failed before durable admission: {type(error).__name__}: {error}",
            invalidated=False,
        )
    marked = False
    try:
        with owner._lock, closing(owner._connect(write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = verify(owner, conn, token, record)
            target = dict(row or {})
            target.update(
                generation=token.generation,
                source=token.source,
                project=token.project,
                worktree=token.worktree,
            )
            # A pending target may not exist yet; never key its failure to its predecessor.
            owner._mark_failed(target, error, reason=reason)
            marked = True
            gap(conn, token.generation, token.source, reason, error)
            if token.generation == token.base_generation:
                resolve(owner, conn, token)
            owner._fence(conn)
    except (OSError, sqlite3.Error) as persistence_error:
        result = failures.MutationFailure(
            f"admitted operation remains unresolved; gap persistence failed: {persistence_error}",
            invalidated=marked,
        )
        result.admitted = True
        return result
    result = failures.MutationFailure(
        f"admitted owner mutation requires reconciliation: {error}", invalidated=True
    )
    result.gap_persisted = True
    result.admitted = True
    return result
