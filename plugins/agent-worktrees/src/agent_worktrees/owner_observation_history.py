"""Owner-fenced SQLite projection; never a YAML transaction or canonical ledger."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import owner_observation_contract as c
from .owner_observation_append import SCHEMA, append, delivery_failed, freeze_record, prepare


@dataclass(frozen=True)
class CanonicalObservation:
    """Explicit owner-supplied capture; no filesystem or actor discovery."""

    project: str
    worktree_id: str
    source: str
    head_session_id: str | None
    facts: dict[str, Any]
    record_fence: dict[str, Any]
    observed_at: str
    availability: str = "observed"
    completeness: str = "unknown"

    def __post_init__(self) -> None:
        c.Selection(self.project, self.worktree_id, self.head_session_id)
        c.reference(self.source, "canonical source identity")
        c.timestamp(self.observed_at)
        c.enum(self.availability, c.AVAILABILITY, "record availability")
        c.enum(self.completeness, c.COMPLETENESS, "retained coverage")
        if not isinstance(self.facts, dict) or not isinstance(self.record_fence, dict):
            raise c.ObservationError("canonical capture facts/fence must be objects")
        c.encode(asdict(self), c.BODY_BYTES - 2048)

    @property
    def key(self) -> tuple[str, str]:
        return self.project, self.worktree_id


class OwnerObservationHistory:
    """An optional projection fence under an already elected caller-owned resident."""

    def __init__(
        self,
        path: Path,
        *,
        resident_generation: str,
        writer_id: str,
        authority_check: Callable[[], bool],
        max_entries: int = 5000,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        self.path = Path(path)
        self.resident_generation = c.generation(resident_generation)
        self.writer_id = c.generation(writer_id)
        if not callable(authority_check):
            raise c.ObservationError("explicit elected-owner callback required")
        if type(max_entries) is not int or not 1 <= max_entries <= 5000:
            raise c.ObservationError("invalid history retention count")
        if type(max_bytes) is not int or not c.BODY_BYTES <= max_bytes <= 8 * 1024 * 1024:
            raise c.ObservationError("invalid history retention bytes")
        self.authority_check = authority_check
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._lock = threading.RLock()
        self._active = False
        self._retired = False
        self._failures: dict[tuple[str, str], str] = {}
        self._failure_reasons: dict[tuple[str, str], str] = {}

    def _authority(self) -> None:
        if self._retired or self.authority_check() is not True:
            raise c.ObservationError("owner observation publisher is not elected", "superseded")
        if self.path.is_symlink():
            raise c.ObservationError("linked owner observation database refused", "unavailable")

    def _connect(self, *, write: bool = False) -> sqlite3.Connection:
        self._authority()
        if not self._active:
            raise c.ObservationError("owner observation index is unready", "unready")
        uri = self.path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro")
        conn = sqlite3.connect(uri, uri=True, timeout=0.2)
        conn.row_factory = sqlite3.Row
        return conn

    def _fence(self, conn: sqlite3.Connection) -> None:
        self._authority()
        row = conn.execute(
            "SELECT generation,writer FROM owner_observation_owner WHERE singleton=1",
        ).fetchone()
        if row is None or tuple(row) != (self.resident_generation, self.writer_id):
            self._retired = True
            raise c.ObservationError("owner observation writer was replaced", "superseded")

    @contextmanager
    def _read(self):
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN")
            self._fence(conn)
            yield conn
            conn.rollback()
            with closing(self._connect()) as fresh:
                self._fence(fresh)

    def activate(self, *, expected_generation: str | None = None) -> None:
        if expected_generation is not None:
            c.generation(expected_generation)
        with self._lock:
            self._authority()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with closing(sqlite3.connect(self.path, timeout=0.2)) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    for statement in SCHEMA:
                        conn.execute(statement)
                    self._authority()
                    previous = conn.execute(
                        "SELECT generation,writer FROM owner_observation_owner WHERE singleton=1",
                    ).fetchone()
                    if previous != (self.resident_generation, self.writer_id):
                        if self._active:
                            self._retired = True
                            raise c.ObservationError(
                                "replaced writer cannot reactivate", "superseded"
                            )
                        old = None if previous is None else previous[0]
                        if old != expected_generation or old == self.resident_generation:
                            raise c.ObservationError(
                                "observation owner generation changed", "superseded"
                            )
                        conn.execute(
                            "INSERT OR REPLACE INTO owner_observation_owner VALUES (1,?,?)",
                            (self.resident_generation, self.writer_id),
                        )
                    self._authority()
                self._active = True
            except (OSError, sqlite3.Error):
                self._active = False
                raise

    def close(self) -> None:
        with self._lock:
            self._retired, self._active = True, False

    def _row(self, conn: sqlite3.Connection, selected: c.Selection, journal: str | None = None):
        if journal is None:
            found = conn.execute(
                "SELECT generation FROM owner_observation_active WHERE project=? AND worktree=?",
                (selected.project, selected.worktree_id),
            ).fetchone()
            journal = None if found is None else found[0]
        row = conn.execute(
            "SELECT * FROM owner_observation_journal "
            "WHERE generation=? AND project=? AND worktree=?",
            (journal, selected.project, selected.worktree_id),
        ).fetchone()
        if row is None:
            raise c.ObservationError("selected owner source is not materialized", "unready")
        return row

    def _source(self, row) -> c.Source:
        return c.Source(
            row["project"],
            row["worktree"],
            row["source"],
            row["generation"],
            self.resident_generation,
            row["head"],
        )

    def _body(self, row, selected: c.Selection) -> dict:
        state = row["availability"]
        if selected.expected_head_session_id is None or row["head"] is None:
            state = "unknown"
        elif selected.expected_head_session_id != row["head"]:
            state = "head-changed"
        failure = self._failures.get((row["project"], row["worktree"]))
        if failure:
            state = "unavailable"
        return {
            "availability": state,
            "completeness": "partial" if failure or row["gap"] else row["completeness"],
            "durability": "pending" if failure else "committed",
            "history_scope": "retained-range",
            "history_floor": c.position(self._source(row), row["floor"]),
            "observed_at": row["observed_at"],
        }

    def _entries(self, conn, row, after: int, before: int | None = None, limit: int = 64):
        upper = row["position"] if before is None else before - 1
        found = conn.execute(
            "SELECT payload FROM owner_observation_entry WHERE generation=? "
            "AND position>? AND position<=? ORDER BY position DESC LIMIT ?",
            (row["generation"], after, upper, limit),
        ).fetchall()
        return [c.decode(c.utf8(item[0]), c.BODY_BYTES) for item in reversed(found)]

    def _snapshot(self, conn, row, selected: c.Selection, after_cursor: str | None = None):
        if row["qualified"] != self.resident_generation:
            raise c.ObservationError(
                "current canonical record requires requalification", "unready"
            )
        source, entries, complete, base = self._source(row), [], True, after_cursor
        reason = row["gap"]
        if after_cursor is not None:
            try:
                after = c.sequence(after_cursor, source)
            except c.ObservationError as error:
                if error.code != "source-replaced":
                    raise
                after, reason, complete = row["position"], "source-replaced", False
            if after > row["position"]:
                raise c.ObservationError("owner position is ahead of the index")
            if after < row["floor"]:
                reason, complete = "retention", False
            elif row["position"] - after > c.PAGE_ENTRIES:
                reason, complete = "cursor-unavailable", False
            elif complete:
                entries = self._entries(conn, row, after)
        body = self._body(row, selected)
        facts = c.decode(c.utf8(row["facts"])) if body["availability"] == "observed" else None
        body.update(
            replay_barrier=c.position(source, row["position"]),
            base_cursor=base,
            entries=entries,
            replay_complete=complete,
            facts=facts,
        )
        try:
            snapshot = c.frame("snapshot", source, body["replay_barrier"], body)
        except c.ObservationError as error:
            if error.code != "oversized" or not entries:
                raise
            body.update(entries=[], replay_complete=False)
            snapshot = c.frame("snapshot", source, body["replay_barrier"], body)
            reason = "cursor-unavailable"
        if self._failures.get((row["project"], row["worktree"])):
            reason = self._failure_reasons.get(
                (row["project"], row["worktree"]), "journal-write-failed"
            )
        return snapshot, reason

    def snapshot(
        self, selected: c.Selection, after_cursor: str | None = None
    ) -> tuple[dict, dict | None]:
        with self._read() as conn:
            self._fence(conn)
            row = self._row(conn, selected)
            snapshot, reason = self._snapshot(conn, row, selected, after_cursor)
            gap = None if reason is None else self._gap(row, selected, reason, after_cursor)
            self._fence(conn)
            return snapshot, gap

    def _gap(self, row, selected, reason, base):
        source = self._source(row)
        body = self._body(row, selected)
        body.update(
            availability="unavailable",
            completeness="partial",
            reason=reason,
            base_cursor=base,
            details=self._failures.get((row["project"], row["worktree"]), reason),
            reconcile="snapshot-and-history",
            source_ref=None,
        )
        return c.frame("gap", source, c.position(source, row["position"]), body)

    def initialize(
        self,
        record: CanonicalObservation,
        *,
        expected_source_generation: str | None = None,
    ) -> dict:
        """Qualify explicit current record input, preserving old journal identities."""
        record = freeze_record(record)
        selected = c.Selection(record.project, record.worktree_id, record.head_session_id)
        with self._lock, closing(self._connect(write=True)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fence(conn)
            active = conn.execute(
                "SELECT generation FROM owner_observation_active WHERE project=? AND worktree=?",
                record.key,
            ).fetchone()
            old = None if active is None else self._row(conn, selected)
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
                conn.execute(
                    "UPDATE owner_observation_journal SET qualified=?,observed_at=? "
                    "WHERE generation=?",
                    (self.resident_generation, record.observed_at, old["generation"]),
                )
            else:
                if old is not None and old["generation"] != expected_source_generation:
                    raise c.ObservationError(
                        "source replacement needs the current incarnation", "source-replaced"
                    )
                journal = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO owner_observation_journal "
                    "VALUES (?,?,?,?,?,?,0,0,?,?,?,?,?,NULL)",
                    (
                        journal,
                        record.project,
                        record.worktree_id,
                        record.source,
                        record.head_session_id,
                        self.resident_generation,
                        c.encode(record.facts),
                        c.encode(record.record_fence),
                        record.observed_at,
                        record.availability,
                        record.completeness,
                    ),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO owner_observation_active VALUES (?,?,?)",
                    (*record.key, journal),
                )
            self._fence(conn)
        return self.snapshot(selected)[0]

    def publish(
        self,
        record: CanonicalObservation,
        entries: list[dict],
        *,
        batch_id: str,
        expected_cursor: str,
        post_save: bool = False,
    ) -> dict:
        c.reference(batch_id, "observation batch identity")
        if (
            type(post_save) is not bool
            or not isinstance(entries, list)
            or not 1 <= len(entries) <= 64
        ):
            raise c.ObservationError(
                "observation publication requires a bounded explicit entry batch"
            )
        record, frozen, digest = prepare(record, entries, post_save)
        selected = c.Selection(record.project, record.worktree_id, record.head_session_id)
        with self._lock:
            admitted = False
            materialized = False
            try:
                with closing(self._connect(write=True)) as conn, conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._fence(conn)
                    row = self._row(conn, selected)
                    if row["source"] != record.source:
                        raise c.ObservationError(
                            "record source incarnation changed", "source-replaced"
                        )
                    duplicate = conn.execute(
                        "SELECT digest FROM owner_observation_batch WHERE generation=? AND id=?",
                        (row["generation"], batch_id),
                    ).fetchone()
                    if duplicate is not None:
                        if duplicate[0] != digest:
                            raise c.ObservationError(
                                "batch identity conflicts with accepted content", "batch-conflict"
                            )
                    else:
                        if c.sequence(expected_cursor, self._source(row)) != row["position"]:
                            raise c.ObservationError(
                                "owner observation position changed", "stale-cursor"
                            )
                        admitted = True
                        materialized = append(self, conn, row, record, frozen, batch_id, digest)
                    self._fence(conn)
            except c.ObservationError as error:
                if admitted and error.code in {"oversized", "cursor-unavailable"}:
                    delivery_failed(self, selected, record, expected_cursor, error)
                    raise c.ObservationError(
                        "admitted owner publication requires reconciliation", "cursor-unavailable"
                    ) from error
                raise
            except (OSError, sqlite3.Error) as error:
                self.append_failed(record.key, error)
                raise c.ObservationError(
                    "ancillary owner journal append failed", "journal-write-failed"
                ) from error
            try:
                with closing(self._connect()) as conn:
                    self._fence(conn)
                if materialized:
                    self._failures.pop(record.key, None)
                    self._failure_reasons.pop(record.key, None)
                return self.snapshot(selected)[0]
            except (OSError, sqlite3.Error) as error:
                self.append_failed(record.key, error)
                raise c.ObservationError(
                    "owner index delivery requires reconciliation", "journal-write-failed"
                ) from error

    def delta(self, selected: c.Selection, after_cursor: str) -> tuple[dict, dict | None]:
        with self._read() as conn:
            self._fence(conn)
            row = self._row(conn, selected)
            snapshot, reason = self._snapshot(conn, row, selected, after_cursor)
            gap = None if reason is None else self._gap(row, selected, reason, after_cursor)
            if gap is not None or not snapshot["replay_complete"]:
                return snapshot, gap
            if after_cursor == snapshot["cursor"]:
                return snapshot, None
            body = {key: snapshot[key] for key in c.BODY_FIELDS["delta"]}
            result = c.frame("delta", self._source(row), snapshot["cursor"], body)
            self._fence(conn)
            return result, None

    def page(
        self, selected: c.Selection, journal: str, before_cursor: str | None, limit: int = 64
    ):
        c.generation(journal)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise c.ObservationError("invalid owner history page limit")
        with self._read() as conn:
            self._fence(conn)
            row = self._row(conn, selected, journal)
            source = self._source(row)
            before = None if before_cursor is None else c.sequence(before_cursor, source)
            if before is not None and before > row["position"] + 1:
                raise c.ObservationError("history before position is ahead of the source")
            if before is not None and before <= row["floor"]:
                return self._gap(row, selected, "retention", before_cursor)
            entries = self._entries(conn, row, row["floor"], before, limit)
            while True:
                oldest = None if not entries else c.sequence(entries[0]["cursor"], source)
                more = oldest is not None and oldest > row["floor"] + 1
                body = self._body(row, selected)
                body.update(
                    before_cursor=before_cursor,
                    next_before=entries[0]["cursor"] if more else None,
                    entries=entries,
                    has_more=more,
                )
                try:
                    result = c.frame(
                        "history-page", source, c.position(source, row["position"]), body
                    )
                    break
                except c.ObservationError as error:
                    if error.code != "oversized" or len(entries) <= 1:
                        raise
                    entries.pop(0)
            self._fence(conn)
            return result

    def last_record(self, selected: c.Selection) -> tuple[CanonicalObservation, str, str]:
        """Last materialization for labelled reconciliation, never active-source proof."""
        with self._read() as conn:
            self._fence(conn)
            row = self._row(conn, selected)
            record = CanonicalObservation(
                row["project"],
                row["worktree"],
                row["source"],
                row["head"],
                c.decode(c.utf8(row["facts"])),
                c.decode(c.utf8(row["fence"])),
                row["observed_at"],
                row["availability"] if row["qualified"] == self.resident_generation else "unknown",
                row["completeness"],
            )
            self._fence(conn)
            return record, c.position(self._source(row), row["position"]), row["generation"]

    def check_owner(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            self._fence(conn)

    def append_failed(
        self,
        key: tuple[str, str],
        error: Exception,
        *,
        reason: str = "journal-write-failed",
    ) -> None:
        with self._lock:
            self._failures[key] = str(error)[:1024] or type(error).__name__
            self._failure_reasons[key] = c.enum(reason, c.GAP_REASONS, "gap reason")
