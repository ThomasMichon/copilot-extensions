"""A queryable ``(repo, pr_number) -> sessions`` catalog index.

:mod:`agent_logger.sessions`' ``review-annotations.json`` sidecar
(:func:`agent_logger.sessions.write_review_annotation` /
:func:`agent_logger.sessions.read_review_annotations`) is the durable,
first-class record of "this session worked this external work item" -- but
reading it back for a given ``(repo, pr_number)`` means sweeping every
session directory, which does not scale as a live lookup once the corpus
reaches thousands of sessions.

This module adds a small SQLite index over that same fact, analogous to
:class:`agent_logger.chronicle.source.SqliteReservationStore`: a derived,
rebuildable-from-sidecars performance cache, never a second source of truth.
Losing the db file is recoverable via :func:`rebuild_from_sidecars`; losing a
sidecar is not recoverable from the index.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_logger import sessions


@dataclass(frozen=True)
class CatalogEntry:
    """One ``(repo, pr_number, session_id, role)`` catalog row."""

    session_id: str
    repo: str
    pr_number: int
    role: str
    recorded_at: str


class ReviewCatalogIndex:
    """SQLite-backed index of review annotations, keyed on ``(repo, pr_number)``.

    Safe for concurrent writers: each call opens its own short-lived
    connection (WAL mode, a bounded busy timeout), matching the chronicle
    reservation store's concurrency posture.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_catalog (
                    repo        TEXT NOT NULL,
                    pr_number   INTEGER NOT NULL,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (repo, pr_number, session_id, role)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_catalog_repo_pr "
                "ON review_catalog(repo, pr_number)"
            )

    def record(
        self,
        *,
        session_id: str,
        repo: str,
        pr_number: int,
        role: str,
        recorded_at: str,
    ) -> None:
        """Idempotently record one catalog row.

        A duplicate ``(repo, pr_number, session_id, role)`` key is a no-op --
        safe to call repeatedly from a write path or a rebuild sweep.
        """
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO review_catalog "
                "(repo, pr_number, session_id, role, recorded_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(repo, pr_number, session_id, role) DO NOTHING",
                (repo, pr_number, session_id, role, recorded_at),
            )

    def query(
        self,
        repo: str,
        pr_number: int,
        *,
        since: str | None = None,
        until: str | None = None,
    ) -> list[CatalogEntry]:
        """Return every catalog entry for ``(repo, pr_number)``.

        ``since``/``until`` optionally window the result by ``recorded_at``
        (inclusive, ISO-8601 string comparison -- the same sortable format
        :func:`agent_logger.sessions.write_review_annotation` writes).
        Results are ordered oldest-first.
        """
        clauses = ["repo = ?", "pr_number = ?"]
        params: list[object] = [repo, pr_number]
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("recorded_at <= ?")
            params.append(until)
        query = (
            "SELECT repo, pr_number, session_id, role, recorded_at "
            "FROM review_catalog WHERE " + " AND ".join(clauses) + " "
            "ORDER BY recorded_at ASC"
        )
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            CatalogEntry(
                session_id=row["session_id"],
                repo=row["repo"],
                pr_number=row["pr_number"],
                role=row["role"],
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]


def default_index(cfg: object | None = None) -> ReviewCatalogIndex:
    """Resolve the config-configured catalog index for this host.

    The convenience entry point every production caller should use rather
    than constructing a :class:`ReviewCatalogIndex` from a hand-rolled path:
    a session-writing caller (e.g. a reviewer-session backfill tool) passes
    ``index=default_index()`` to :func:`agent_logger.sessions
    .write_review_annotation` so ordinary annotation writes populate the
    catalog as a side effect, with no separate wiring. Also used by the
    ``agent-logger catalog rebuild``/``status`` CLI (see ``__main__.py``).

    ``cfg`` is injectable for tests; production callers omit it and get
    :func:`agent_logger.config.load_config`'s resolution.
    """
    if cfg is None:
        from agent_logger.config import load_config

        cfg = load_config()
    return ReviewCatalogIndex(cfg.catalog_db_path)


def rebuild_from_sidecars(
    index: ReviewCatalogIndex, state_root: Path, *archive_stores: Path
) -> int:
    """Rebuild ``index`` from every discoverable session's own sidecar.

    Additive and idempotent (:meth:`ReviewCatalogIndex.record` no-ops on a
    duplicate key) -- safe to run against an index that already has rows,
    e.g. when the index is added to a corpus that predates it, or to repair
    after a lost/corrupted db file. A malformed annotation entry (missing or
    wrong-typed field) is skipped rather than failing the whole rebuild.

    Returns the number of sessions scanned (not the number of new rows,
    since some annotations may already be present in the index).
    """
    scanned = 0
    for ref in sessions.iter_session_refs(state_root, *archive_stores):
        for entry in sessions.read_review_annotations(ref):
            repo = entry.get("repo")
            pr_number = entry.get("pr_number")
            role = entry.get("role")
            recorded_at = entry.get("recorded_at")
            if not (
                isinstance(repo, str)
                and isinstance(pr_number, int)
                and isinstance(role, str)
                and isinstance(recorded_at, str)
            ):
                continue
            index.record(
                session_id=ref.id,
                repo=repo,
                pr_number=pr_number,
                role=role,
                recorded_at=recorded_at,
            )
        scanned += 1
    return scanned
