"""Bounded current rows published by the resident Worktrees authority.

This storage seam does not assemble facts, load tracking records or enroll
producers. Live reads use only complete publications by this process. Durable
reads are explicitly advisory, including after restart.
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .worktrees_projection_index import (
    INDEX_SCHEMA,
    ProjectionError,
    ProjectionOwnershipChanged,
    ProjectionUnavailable,
    Selection,
    SelectionIndex,
    durable_selection,
)

SCHEMA_VERSION = 1
MAX_ROW_BYTES = 64 * 1024
MAX_TEXT_BYTES = 4096
MAX_COLLECTION_ITEMS = 128
MAX_DEPTH = 6
ROW_FIELDS = frozenset("""
id branch path repo machine platform machine_identity_version environment execution_machine
status started_at title resume_count
last_resumed_at codename creation_nonce session_count last_session_id head_yielded execution_leg
execution_leg_live completed_at kind owner interface origin checkout_managed checkout_present
picker_hidden bound_agent dispatch_attempt active_effort follow_up paused
summary status_note_at activity activity_at caller_worktree controller_revision
controllers controller_findings reciprocal_relation owner_ref pair_id pair_role
pair_kind current_profile_assignment state ahead behind dirty current_branch
branch_drift cleanup_bucket closure ff_eligible mux_session mux_clients
mux_attached turn_count session_head_mismatch session_head_mismatch_scanned_id
live_session_ids session_lock_live session_lock_stale stale_lock_pids
live_intent live_intent_at live_intent_idle live_rest live_rest_at pr pr_count
caller_state delegate_finalizable delegate_finalizable_reason
claims_summary claims_links claims_assets claims_link_count claims_links_complete claims_projection
session_bare_orphan session_bound_live
session_bridge_live fact_provenance
""".split())
_BOOL_FIELDS = frozenset("""
execution_leg_live checkout_managed checkout_present picker_hidden follow_up paused
branch_drift ff_eligible mux_session mux_attached session_head_mismatch
session_lock_live session_lock_stale live_intent_idle session_bare_orphan
session_bound_live session_bridge_live head_yielded
claims_links_complete
delegate_finalizable
""".split())
_INT_FIELDS = frozenset("""
resume_count session_count controller_revision ahead behind dirty mux_clients
turn_count pr_count claims_link_count machine_identity_version
""".split())
_OBJECT_FIELDS = frozenset("""
execution_leg dispatch_attempt active_effort reciprocal_relation
current_profile_assignment closure pr fact_provenance claims_assets claims_projection
caller_state
""".split())
_ARRAY_FIELDS = frozenset("""
controllers controller_findings live_session_ids stale_lock_pids claims_links
""".split())
_HISTORY_FIELDS = frozenset({
    "resources", "prs", "profile_assignments", "sessions", "handoffs",
    "journal", "history", "events", "ledger", "claims_graph",
})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS worktrees_projection_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
    generation TEXT NOT NULL,
    writer_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worktrees_projection_head (
    project TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    revision INTEGER NOT NULL,
    observed_at REAL NOT NULL,
    row_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worktrees_projection_row (
    project TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (project, worktree_id)
);
"""


def _identity(value: object, *, name: str) -> str:
    if (
        not isinstance(value, str) or not value or len(value.encode("utf-8")) > 256
        or value in {".", ".."} or any(part in value for part in ("/", "\\", "\x00"))
    ):
        raise ProjectionError(f"invalid {name}")
    return value


def _bounded(value: object, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ProjectionError("current row exceeds nesting bound")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ProjectionError("current row exceeds integer bound")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProjectionError("current row contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ProjectionError("current row exceeds text bound")
        return
    if isinstance(value, (list, dict)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ProjectionError("current row exceeds collection bound")
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or key in _HISTORY_FIELDS:
                    raise ProjectionError("current row contains historical or invalid fields")
                _bounded(key, depth + 1)
                _bounded(child, depth + 1)
        else:
            for child in value:
                _bounded(child, depth + 1)
        return
    raise ProjectionError("current row contains a non-JSON value")


def _encode_row(row: dict) -> tuple[str, str]:
    if not isinstance(row, dict) or set(row) - ROW_FIELDS:
        raise ProjectionError("current row contains undeclared fields")
    wid = _identity(row.get("id"), name="worktree id")
    from .worktree_machine_identity import WorktreeMachineIdentityError, parse_machine_identity

    try:
        parse_machine_identity(row)
    except WorktreeMachineIdentityError as exc:
        raise ProjectionError(str(exc)) from exc
    for field, value in row.items():
        expected = (
            bool if field in _BOOL_FIELDS else
            int if field in _INT_FIELDS else
            dict if field in _OBJECT_FIELDS else
            list if field in _ARRAY_FIELDS else str
        )
        if value is not None and type(value) is not expected:
            raise ProjectionError(f"invalid current row field type: {field}")
    _bounded(row)
    payload = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > MAX_ROW_BYTES:
        raise ProjectionError("current row exceeds byte bound")
    return wid, payload


def _decode_row(wid: str, payload: str) -> dict:
    try:
        row = json.loads(payload)
        identity, _encoded = _encode_row(row)
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProjectionError("invalid durable current row") from exc
    if identity != wid:
        raise ProjectionError("durable row identity mismatch")
    return row


@dataclass(frozen=True)
class _Publication:
    generation: str
    revision: int
    observed_at: float
    row_count: int
    rows: dict[str, str]
    index: SelectionIndex | None = None
    index_key: tuple | None = None

    def read(self, ids: Iterable[str], *, quality: str) -> dict:
        rows = [_decode_row(wid, self.rows[wid]) for wid in ids]
        return {
            "worktrees": rows,
            "projection": {
                "schema_version": SCHEMA_VERSION,
                "generation": self.generation,
                "revision": self.revision,
                "observed_at": self.observed_at,
                "roster_count": self.row_count,
                "selected_count": len(rows),
                "quality": quality,
            },
        }


class WorktreesProjection:
    """One resident publisher, atomic current roster replacement, no read compute."""

    def __init__(self, db_path: Path, *, generation: str | None = None) -> None:
        self.db_path = Path(db_path)
        self.generation = generation if generation is not None else uuid.uuid4().hex
        if (
            not isinstance(self.generation, str) or len(self.generation) != 32
            or any(c not in "0123456789abcdef" for c in self.generation)
        ):
            raise ProjectionError("invalid projection generation")
        self._publication_lock = threading.Lock()
        self._writer_lock = threading.Lock()
        self._projects: dict[str, _Publication] = {}
        self._closed = False
        self._writer_id = uuid.uuid4().hex
        self._activated = False

    def _claim(self, conn: sqlite3.Connection, expected_generation: str | None) -> None:
        owner = conn.execute(
            "SELECT generation, writer_id FROM worktrees_projection_owner WHERE singleton=1",
        ).fetchone()
        if owner == (self.generation, self._writer_id):
            return
        if self._activated:
            with self._publication_lock:
                self._closed = True
                self._projects.clear()
            raise ProjectionUnavailable("projection publisher was superseded")
        previous = None if owner is None else owner[0]
        if previous != expected_generation:
            raise ProjectionOwnershipChanged("projection owner generation changed")
        if previous == self.generation:
            raise ProjectionError("replacement publisher must use a new generation")
        conn.execute(
            "INSERT OR REPLACE INTO worktrees_projection_owner VALUES (1, ?, ?)",
            (self.generation, self._writer_id),
        )

    def activate(
        self, *, expected_generation: str | None = None,
        authority_check: Callable[[], bool] | None = None,
    ) -> None:
        """Claim after resident election/drain, comparing the previous owner.

        A newly elected monitor must explicitly name the prior generation.
        Constructing a second store or reading advisory data grants no authority.
        """
        if expected_generation is not None and (
            not isinstance(expected_generation, str) or len(expected_generation) != 32
            or any(c not in "0123456789abcdef" for c in expected_generation)
        ):
            raise ProjectionError("invalid previous projection generation")
        with self._writer_lock:
            if self._closed:
                raise ProjectionUnavailable("projection publisher is closed")
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.db_path, timeout=1.0)) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA + INDEX_SCHEMA)
                conn.execute("BEGIN IMMEDIATE")
                if authority_check is not None and not authority_check():
                    raise ProjectionUnavailable("projection publisher is no longer elected")
                self._claim(conn, expected_generation)
            self._activated = True

    def publish(self, project: str, rows: Iterable[dict], *, observed_at: float) -> None:
        project = _identity(project, name="project")
        if (
            type(observed_at) not in (int, float)
            or not 0 <= observed_at < 2**63
            or not math.isfinite(observed_at) or observed_at < 0
        ):
            raise ProjectionError("invalid projection observation timestamp")
        encoded = {}
        identities = []
        for row in rows:
            wid, payload = _encode_row(row)
            if wid in encoded:
                raise ProjectionError("duplicate current worktree identity")
            encoded[wid] = payload
            identities.append({field: row.get(field) for field in (
                "id", "status", "platform", "codename", "checkout_present",
            )})
        index_key = tuple(tuple(row.get(field) for field in (
            "id", "status", "platform", "codename", "checkout_present",
        )) for row in identities)
        with self._writer_lock:
            if self._closed:
                raise ProjectionUnavailable("projection publisher is closed")
            previous = self._projects.get(project)
            index = previous.index if (
                previous is not None and previous.index_key == index_key
            ) else None
            reuse_index = index is not None
            if index is None:
                index = SelectionIndex(identities)
            revision = previous.revision + 1 if previous is not None else 1
            publication = _Publication(
                self.generation, revision, observed_at, len(encoded), encoded, index, index_key,
            )
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.db_path, timeout=1.0)) as conn, conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA + INDEX_SCHEMA)
                conn.execute("BEGIN IMMEDIATE")
                self._claim(conn, None)
                conn.execute("DELETE FROM worktrees_projection_row WHERE project=?", (project,))
                conn.executemany(
                    "INSERT INTO worktrees_projection_row VALUES (?, ?, ?)",
                    ((project, wid, payload) for wid, payload in encoded.items()),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO worktrees_projection_head VALUES (?, ?, ?, ?, ?, ?)",
                    (project, SCHEMA_VERSION, self.generation, revision,
                     observed_at, len(encoded)),
                )
                if not reuse_index:
                    index.persist(conn, project)
            self._activated = True
            with self._publication_lock:
                self._projects[project] = publication

    def read(
        self, project: str, *, worktree_id: str | None = None, codename: str | None = None,
        status_filter: str | None = None, platform_filter: str | None = None,
        include_missing: bool = True,
    ) -> dict:
        project = _identity(project, name="project")
        selection = Selection(
            status_filter, platform_filter, include_missing, worktree_id, codename,
        )
        with self._publication_lock:
            publication = None if self._closed else self._projects.get(project)
        if publication is None or publication.index is None:
            raise ProjectionUnavailable("resident current roster is not published")
        return publication.read(publication.index.select(selection), quality="current")

    def published_projects(self) -> set[str]:
        with self._publication_lock:
            return set(self._projects)

    def close(self) -> None:
        with self._writer_lock:
            with self._publication_lock:
                self._closed = True
                self._projects.clear()


def read_advisory(
    db_path: Path, project: str, *, worktree_id: str | None = None, codename: str | None = None,
    status_filter: str | None = None, platform_filter: str | None = None,
    include_missing: bool = True,
) -> dict:
    """Read only independent current slots; never create or recover the store."""
    project = _identity(project, name="project")
    selection = Selection(status_filter, platform_filter, include_missing, worktree_id, codename)
    try:
        with closing(sqlite3.connect(
            Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1,
        )) as conn:
            conn.execute("BEGIN")
            head = conn.execute(
                "SELECT CASE WHEN typeof(schema_version)='integer' THEN schema_version END, "
                "CASE WHEN length(CAST(generation AS BLOB))=32 THEN generation END, "
                "CASE WHEN typeof(revision)='integer' THEN revision END, "
                "CASE WHEN typeof(observed_at) IN ('real', 'integer') THEN observed_at END, "
                "CASE WHEN typeof(row_count)='integer' THEN row_count END "
                "FROM worktrees_projection_head WHERE project=?", (project,),
            ).fetchone()
            if head is None:
                raise ProjectionUnavailable("no durable current roster")
            version, generation, revision, observed_at, row_count = head
            if (
                version != SCHEMA_VERSION or not isinstance(generation, str)
                or len(generation) != 32
                or any(c not in "0123456789abcdef" for c in generation)
                or type(revision) is not int or revision < 1
                or type(observed_at) not in (int, float) or not math.isfinite(observed_at)
                or observed_at < 0
                or type(row_count) is not int or row_count < 0
            ):
                raise ProjectionError("invalid durable publication metadata")
            selected_id, expected_count = durable_selection(conn, project, selection)
            parameters: tuple = (MAX_ROW_BYTES, project)
            query = (
                "SELECT CASE WHEN length(CAST(r.worktree_id AS BLOB))<=256 "
                "THEN r.worktree_id END, CASE WHEN length(CAST(r.payload AS BLOB))<=? "
                "THEN r.payload END FROM worktrees_projection_row r "
            )
            if selected_id is not None:
                query += "WHERE r.project=? AND r.worktree_id=?"
                parameters += (selected_id,)
            else:
                query += (
                    "JOIN worktrees_projection_member m ON "
                    "m.project=r.project AND m.worktree_id=r.worktree_id "
                    "WHERE m.project=? AND m.scope=? ORDER BY m.ordinal"
                )
                parameters += (selection.scope,)
                if not expected_count:
                    query += " LIMIT 0"
            rows = conn.execute(query, parameters)
            encoded = {}
            for wid, payload in rows:
                if not isinstance(payload, str):
                    raise ProjectionError("durable current row exceeds byte bound")
                encoded[_identity(wid, name="worktree id")] = payload
            if len(encoded) != expected_count or (
                selection == Selection() and expected_count != row_count
            ):
                raise ProjectionError("incomplete durable current roster")
            publication = _Publication(generation, revision, observed_at, row_count, encoded)
            return publication.read(encoded, quality="advisory")
    except (sqlite3.Error, OSError) as exc:
        raise ProjectionUnavailable(f"durable current roster unavailable: {exc}") from exc
