"""SQLite path index — lightweight metadata tracking what's in LanceDB.

Maintains a ``(source, file_path)`` table that mirrors the set of files
currently indexed in LanceDB.  Used for:

- Deletion detection: ``stored_paths - current_paths = deletions``
- Stale chunk cleanup after modified-file re-indexing
- Quick path enumeration without materializing LanceDB to Pandas

Each public method opens its own WAL-mode connection, matching the
thread-safety pattern from ``task_store.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from .embedding_coverage import SCHEMA as _EMBEDDING_SCHEMA

log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS indexed_files (
    source       TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    content_hash TEXT,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    indexed_at   REAL NOT NULL,
    PRIMARY KEY (source, file_path)
);

CREATE INDEX IF NOT EXISTS idx_indexed_files_source
    ON indexed_files(source);
"""


class PathIndex:
    """SQLite-backed index of files stored in LanceDB.

    Thread-safe: each method opens a fresh connection with WAL mode.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA + _EMBEDDING_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_chunk_totals'",
            ).fetchone() is None:
                conn.execute(
                    "CREATE TABLE source_chunk_totals "
                    "(source TEXT PRIMARY KEY, chunk_count INTEGER NOT NULL)",
                )
                conn.execute(
                    "INSERT INTO source_chunk_totals SELECT source, SUM(chunk_count) "
                    "FROM indexed_files GROUP BY source",
                )
            for name, event, body in (
                ("insert", "INSERT", """
                    INSERT INTO source_chunk_totals VALUES (new.source, new.chunk_count)
                    ON CONFLICT(source) DO UPDATE SET
                        chunk_count = chunk_count + new.chunk_count;
                """),
                ("update", "UPDATE", """
                    UPDATE source_chunk_totals SET chunk_count = chunk_count - old.chunk_count
                        WHERE source = old.source;
                    INSERT INTO source_chunk_totals VALUES (new.source, new.chunk_count)
                    ON CONFLICT(source) DO UPDATE SET
                        chunk_count = chunk_count + new.chunk_count;
                """),
                ("delete", "DELETE", """
                    UPDATE source_chunk_totals SET chunk_count = chunk_count - old.chunk_count
                        WHERE source = old.source;
                """),
            ):
                conn.execute(
                    f"CREATE TRIGGER IF NOT EXISTS indexed_files_total_{name} "
                    f"AFTER {event} ON indexed_files BEGIN {body} END",
                )
            conn.commit()
        finally:
            conn.close()

    # ── Write ────────────────────────────────────────────────

    def mark_indexed(
        self,
        source: str,
        file_path: str,
        *,
        content_hash: str | None = None,
        chunk_count: int = 0,
    ) -> None:
        """Record that a file has been indexed (insert or update)."""
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO indexed_files
                   (source, file_path, content_hash, chunk_count, indexed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, file_path) DO UPDATE SET
                       content_hash = excluded.content_hash,
                       chunk_count = excluded.chunk_count,
                       indexed_at = excluded.indexed_at""",
                (source, file_path, content_hash, chunk_count, now),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_indexed_batch(
        self,
        entries: list[tuple[str, str, str | None, int]],
    ) -> None:
        """Batch upsert: list of (source, file_path, content_hash, chunk_count)."""
        if not entries:
            return
        now = time.time()
        conn = self._connect()
        try:
            conn.executemany(
                """INSERT INTO indexed_files
                   (source, file_path, content_hash, chunk_count, indexed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, file_path) DO UPDATE SET
                       content_hash = excluded.content_hash,
                       chunk_count = excluded.chunk_count,
                       indexed_at = excluded.indexed_at""",
                [(s, p, h, c, now) for s, p, h, c in entries],
            )
            conn.commit()
        finally:
            conn.close()

    def invalidate_batch(self, identities: list[tuple[str, str]]) -> None:
        """Revoke reuse before mutations that might only partially succeed."""
        if not identities:
            return
        conn = self._connect()
        try:
            conn.executemany(
                "UPDATE indexed_files SET content_hash = NULL WHERE source = ? AND file_path = ?",
                identities,
            )
            conn.commit()
        finally:
            conn.close()

    def remove(self, source: str, file_path: str) -> None:
        """Remove a single file entry."""
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM indexed_files WHERE source = ? AND file_path = ?",
                (source, file_path),
            )
            conn.commit()
        finally:
            conn.close()

    def remove_batch(self, entries: list[tuple[str, str]]) -> int:
        """Batch remove: list of (source, file_path). Returns count removed."""
        if not entries:
            return 0
        conn = self._connect()
        try:
            affected = 0
            for source, file_path in entries:
                affected += conn.execute(
                    "DELETE FROM indexed_files WHERE source = ? AND file_path = ?",
                    (source, file_path),
                ).rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def remove_by_source(self, source: str) -> int:
        """Remove all entries for a source (exact match). Returns count."""
        conn = self._connect()
        try:
            affected = conn.execute(
                "DELETE FROM indexed_files WHERE source = ?",
                (source,),
            ).rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    def remove_by_source_prefix(self, prefix: str) -> int:
        """Remove entries for a source and all sub-sources. Returns count."""
        conn = self._connect()
        try:
            affected = conn.execute(
                "DELETE FROM indexed_files WHERE source = ? OR source LIKE ?",
                (prefix, f"{prefix}:%"),
            ).rowcount
            conn.commit()
            return affected
        finally:
            conn.close()

    # ── Read ─────────────────────────────────────────────────

    def chunk_count_by_family(self, family: str) -> int:
        """Read transactionally maintained totals, never scan unchanged file rows."""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT COALESCE(SUM(chunk_count), 0) AS total
                   FROM source_chunk_totals
                   WHERE source = ? COLLATE BINARY
                      OR (source >= ? COLLATE BINARY AND source < ? COLLATE BINARY)""",
                (family, f"{family}:", f"{family};"),
            ).fetchone()
            return row["total"]
        finally:
            conn.close()

    def get_paths(self, source: str) -> set[str]:
        """Return all file_paths for an exact source match."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT file_path FROM indexed_files WHERE source = ?",
                (source,),
            ).fetchall()
            return {r["file_path"] for r in rows}
        finally:
            conn.close()

    def get_paths_by_prefix(self, prefix: str) -> dict[str, set[str]]:
        """Return {source: {file_paths}} for a source prefix.

        E.g., prefix="forge" returns paths for "forge:repo1",
        "forge:repo1:issues", etc.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT source, file_path FROM indexed_files
                   WHERE source = ? OR source LIKE ?""",
                (prefix, f"{prefix}:%"),
            ).fetchall()
            result: dict[str, set[str]] = {}
            for r in rows:
                result.setdefault(r["source"], set()).add(r["file_path"])
            return result
        finally:
            conn.close()

    def get_content_hash(self, source: str, file_path: str) -> str | None:
        """Return the stored content hash for a file, or None."""
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT content_hash FROM indexed_files
                   WHERE source = ? AND file_path = ?""",
                (source, file_path),
            ).fetchone()
            return row["content_hash"] if row else None
        finally:
            conn.close()

    def get_paths_by_family(self, family: str) -> dict[str, set[str]]:
        """Return only the exact case-sensitive family and its colon descendants."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT source, file_path FROM indexed_files
                   WHERE source = ? COLLATE BINARY OR instr(source, ?) = 1""",
                (family, f"{family}:"),
            ).fetchall()
            result: dict[str, set[str]] = {}
            for row in rows:
                result.setdefault(row["source"], set()).add(row["file_path"])
            return result
        finally:
            conn.close()

    def get_family_page(
        self, family: str, *, after: tuple[str, str] | None = None, limit: int = 256,
    ) -> list[tuple[str, str]]:
        """Keyset-read a bounded, ordered slice for explicit full reconciliation."""
        if not isinstance(family, str) or not family:
            raise ValueError("Reconciliation requires a source family")
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("Reconciliation identity page must contain 1 to 256 items")
        if after is not None and (
            not isinstance(after, tuple) or len(after) != 2
            or any(not isinstance(value, str) or not value for value in after)
            or not (after[0] == family or after[0].startswith(family + ":"))
        ):
            raise ValueError("Invalid reconciliation identity cursor")
        conn = self._connect()
        try:
            namespace = after[0] if after else family
            path_clause = "AND file_path > ?" if after else ""
            rows = conn.execute(
                "SELECT source, file_path FROM indexed_files "
                f"WHERE source = ? COLLATE BINARY {path_clause} ORDER BY file_path LIMIT ?",
                (namespace, after[1], limit) if after else (namespace, limit),
            ).fetchall()
            if len(rows) < limit:
                # Separate seeks keep SQLite from scanning the current namespace
                # to evaluate a composite cursor as a residual predicate.
                lower = family + ":" if namespace == family else namespace
                relation = ">=" if namespace == family else ">"
                rows.extend(conn.execute(
                    "SELECT source, file_path FROM indexed_files "
                    f"WHERE source {relation} ? COLLATE BINARY AND source < ? COLLATE BINARY "
                    "ORDER BY source, file_path LIMIT ?",
                    (lower, family + ";", limit - len(rows)),
                ).fetchall())
            return [(row["source"], row["file_path"]) for row in rows]
        finally:
            conn.close()

    def list_sources(self) -> list[str]:
        """Return exact stored source identities, including path-only remnants."""
        conn = self._connect()
        try:
            return [row["source"] for row in conn.execute(
                "SELECT DISTINCT source FROM indexed_files",
            )]
        finally:
            conn.close()

    def get_entry(self, source: str, file_path: str) -> tuple[str | None, float] | None:
        """Return ``(content_hash, indexed_at)`` for a file, or None if absent.

        Used for intra-source resume: a file already stored at the same content
        hash within the current task's window can be skipped on a resumed run.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT content_hash, indexed_at FROM indexed_files
                   WHERE source = ? AND file_path = ?""",
                (source, file_path),
            ).fetchone()
            if row is None:
                return None
            return (row["content_hash"], row["indexed_at"])
        finally:
            conn.close()

    def get_entries(
        self, identities: list[tuple[str, str]],
    ) -> dict[tuple[str, str], tuple[str | None, float]]:
        """Read only selected file checkpoints, in bounded SQLite batches."""
        identities = list(dict.fromkeys(identities))
        if not identities:
            return {}
        result: dict[tuple[str, str], tuple[str | None, float]] = {}
        conn = self._connect()
        try:
            for offset in range(0, len(identities), 100):
                batch = identities[offset:offset + 100]
                predicate = " OR ".join("(source = ? AND file_path = ?)" for _ in batch)
                params = [value for identity in batch for value in identity]
                rows = conn.execute(
                    "SELECT source, file_path, content_hash, indexed_at "
                    f"FROM indexed_files WHERE {predicate}",
                    params,
                )
                for row in rows:
                    result[(row["source"], row["file_path"])] = (
                        row["content_hash"], row["indexed_at"],
                    )
            return result
        finally:
            conn.close()

    def get_all_entries(self) -> dict[tuple[str, str], tuple[str | None, float]]:
        """Return ``{(source, file_path): (content_hash, indexed_at)}`` for all files.

        A single-query bulk read for resume: the embed loop looks each file up in
        this in-memory map instead of opening a SQLite connection per file.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT source, file_path, content_hash, indexed_at FROM indexed_files"
            ).fetchall()
            return {
                (r["source"], r["file_path"]): (r["content_hash"], r["indexed_at"])
                for r in rows
            }
        finally:
            conn.close()

    def get_content_hashes(self, source: str) -> dict[str, str | None]:
        """Return {file_path: content_hash} for all files in a source."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT file_path, content_hash FROM indexed_files WHERE source = ?",
                (source,),
            ).fetchall()
            return {r["file_path"]: r["content_hash"] for r in rows}
        finally:
            conn.close()

    def count(self, source: str | None = None) -> int:
        """Count indexed files, optionally filtered by source."""
        conn = self._connect()
        try:
            if source:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM indexed_files WHERE source = ?",
                    (source,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM indexed_files",
                ).fetchone()
            return row["cnt"]
        finally:
            conn.close()

    def stats(self) -> dict[str, int]:
        """Return {source: file_count} for all sources."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT source, COUNT(*) AS cnt
                   FROM indexed_files GROUP BY source
                   ORDER BY source""",
            ).fetchall()
            return {r["source"]: r["cnt"] for r in rows}
        finally:
            conn.close()

    def get_old_forge_sources(self) -> list[str]:
        """Return old-format Forge source names for migration.

        Old format: ``forge:{owner}/{repo}``, ``forge:{owner}/{repo}:issues``,
        ``forge:{owner}/{repo}:pulls``.
        New format: ``forge:code:{repo}``, ``forge:issues:{repo}``,
        ``forge:pulls:{repo}``.

        Identifies old entries by finding sources starting with ``forge:``
        where the segment after ``forge:`` is not a known sub-type keyword.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT DISTINCT source FROM indexed_files
                   WHERE source LIKE 'forge:%'
                     AND source NOT LIKE 'forge:code:%'
                     AND source NOT LIKE 'forge:issues:%'
                     AND source NOT LIKE 'forge:pulls:%'""",
            ).fetchall()
            return [r["source"] for r in rows]
        finally:
            conn.close()

    def delete_source(self, source: str) -> int:
        """Delete all entries for an exact source. Returns count removed."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM indexed_files WHERE source = ?",
                (source,),
            )
            conn.commit()
            removed = cursor.rowcount
            log.info("Deleted %d path_index entries for source %s", removed, source)
            return removed
        finally:
            conn.close()
