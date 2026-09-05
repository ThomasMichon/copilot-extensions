"""Content store - chunk text, metadata, and BM25 full-text index.

Stores the canonical copy of every indexed chunk.  Vector tables
(managed by ``VectorTable``) reference rows here by ``chunk_id``.
FTS is global across all chunks regardless of which embedding model
produced vectors for them.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pyarrow as pa

if TYPE_CHECKING:
    from pathlib import Path

    from agent_index.chunking.base import Chunk

logger = logging.getLogger(__name__)

# Cap on a reconstructed full-document preview (#882) -- enough for large docs
# and issues, small enough to keep the response and browser render snappy.
_MAX_DOCUMENT_CHARS = 256_000

# FTS rebuild backoff (#1818). A rebuild that keeps losing a LanceDB CreateIndex
# commit conflict must not be retried every maintainer tick forever -- that turns
# a transient/orphaned conflict into a permanent hammer plus recurring search
# stalls. On persistent failure we arm a capped exponential backoff.
_FTS_RETRY_BASE_S = 30.0      # first backoff after a failed rebuild cycle
_FTS_RETRY_CAP_S = 1800.0     # 30-minute ceiling between retries
_FTS_RETRY_JITTER_S = 5.0     # random jitter added to each backoff
_FTS_LOCK_RECHECK_S = 15.0    # re-check soon when another process holds the lock
_FTS_FAILURE_ALERT_THRESHOLD = 3  # escalate log to ERROR after N consecutive fails

_CONTENT_SCHEMA = pa.schema([
    pa.field("chunk_id", pa.string()),
    pa.field("source", pa.string()),
    pa.field("file_path", pa.string()),
    pa.field("chunk_type", pa.string()),
    pa.field("language", pa.string()),
    pa.field("content", pa.string()),
    pa.field("content_hash", pa.string()),
    pa.field("line_start", pa.int32()),
    pa.field("line_end", pa.int32()),
    pa.field("metadata", pa.string()),
    pa.field("indexed_at", pa.float64()),
])


@dataclass(frozen=True)
class ChunkRecord:
    """A chunk's content and metadata (no vector)."""

    chunk_id: str
    source: str
    file_path: str
    chunk_type: str
    language: str
    content: str
    content_hash: str
    line_start: int
    line_end: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _decode_metadata(raw: object) -> dict:
    """Safely decode a JSON metadata string to a dict."""
    if not raw or not isinstance(raw, str):
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


class ContentStore:
    """LanceDB-backed store for chunk content and metadata.

    Owns the canonical ``chunks`` table and the BM25 full-text index.
    Vector tables are managed separately by ``VectorTable`` instances.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        table_name: str = "chunks",
    ) -> None:
        import lancedb

        self._db = lancedb.connect(str(db_path))
        self._db_path = str(db_path)
        self._table_name = table_name
        self._table = None
        self._fts_available = False
        self._fts_dirty = False
        self._fts_lock = threading.Lock()
        # Persistent-failure backoff state (#1818). Guarded by ``_fts_lock``.
        self._fts_consecutive_failures = 0
        self._fts_next_retry_at = 0.0  # time.monotonic() gate; 0 = due now

    @property
    def db(self):
        """Expose the LanceDB connection for VectorTable instances."""
        return self._db

    def _get_or_create_table(self):
        if self._table is not None:
            return self._table
        try:
            table = self._db.open_table(self._table_name)
        except Exception:
            self._table = self._db.create_table(
                self._table_name, schema=_CONTENT_SCHEMA
            )
            logger.info("Created content table: %s", self._table_name)
            return self._table
        self._migrate_table(table)
        self._table = table
        return self._table

    def _migrate_table(self, table) -> None:
        """Add columns introduced after a table was first created.

        Tables created before the ``metadata`` column existed lack it, so an
        ``upsert`` writing a ``metadata`` key would raise an "Append with
        different schema" error.  Backfill the column (empty string per row)
        so existing deployments keep working without a full rebuild.
        """
        existing = set(table.schema.names)
        if "metadata" not in existing:
            table.add_columns({"metadata": "''"})
            logger.info(
                "Migrated content table %s: added 'metadata' column",
                self._table_name,
            )

    # -- write ---------------------------------------------------------------

    def upsert(self, chunks: list[Chunk]) -> int:
        """Insert or update chunk content by chunk_id.

        Returns the number of rows written.
        """
        table = self._get_or_create_table()
        now = time.time()

        records = [
            {
                "chunk_id": c.chunk_id,
                "source": c.source,
                "file_path": c.file_path,
                "chunk_type": c.chunk_type,
                "language": c.language,
                "content": c.content,
                "content_hash": c.content_hash,
                "line_start": c.line_start,
                "line_end": c.line_end,
                "metadata": json.dumps(c.metadata, sort_keys=True) if c.metadata else "",
                "indexed_at": now,
            }
            for c in chunks
        ]

        chunk_ids = [c.chunk_id for c in chunks]
        try:
            id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
            table.delete(f"chunk_id IN ({id_list})")
        except Exception:
            pass

        table.add(records)
        logger.info("Upserted %d chunks to content store", len(records))
        return len(records)

    def delete_by_source(self, source: str) -> None:
        """Remove all chunks from a specific source (prefix-matching)."""
        table = self._get_or_create_table()
        try:
            table.delete(f"source = '{source}' OR source LIKE '{source}:%'")
            logger.info("Deleted content for source: %s", source)
        except Exception:
            logger.debug("No content to delete for source: %s", source)

    def delete_by_source_exact(self, source: str) -> None:
        """Remove chunks for an EXACT source value (no prefix matching).

        Unlike :meth:`delete_by_source`, this only deletes rows whose
        ``source`` equals *source* exactly -- so purging a stale generic
        ``forge:code`` does not also wipe live ``forge:code:owner/repo``
        chunks. Used by source garbage collection (#879).
        """
        table = self._get_or_create_table()
        try:
            table.delete(f"source = '{_sql_str(source)}'")
            logger.info("Deleted content for exact source: %s", source)
        except Exception:
            logger.debug("No content to delete for exact source: %s", source)

    def get_chunk_ids_by_source_exact(self, source: str) -> list[str]:
        """Return chunk_ids for an EXACT source value (no prefix matching)."""
        table = self._get_or_create_table()
        try:
            total = table.count_rows()
            rows = (
                table.search()
                .where(f"source = '{_sql_str(source)}'")
                .select(["chunk_id"])
                .limit(max(total, 1))
                .to_list()
            )
            return [r["chunk_id"] for r in rows]
        except Exception:
            return []

    def source_counts(self) -> dict[str, int]:
        """Return ``{source: chunk_count}`` for every distinct source.

        Projects only the ``source`` column (no read of the large
        ``content`` column), so it stays cheap as the index grows. This is
        the authoritative per-source chunk tally actually stored in the
        index -- use it for truthful status (#879) rather than IndexState.
        """
        table = self._get_or_create_table()
        counts: dict[str, int] = {}
        try:
            total = table.count_rows()
            if total == 0:
                return counts
            rows = table.search().select(["source"]).limit(total).to_list()
            for r in rows:
                src = r.get("source")
                if src:
                    counts[src] = counts.get(src, 0) + 1
        except Exception:
            logger.debug("source_counts scan failed", exc_info=True)
        return counts

    def delete_by_file(self, source: str, file_path: str) -> list[str]:
        """Remove all chunks for a specific file. Returns deleted chunk_ids."""
        table = self._get_or_create_table()
        # Query chunk_ids before deleting so callers can clean up vector tables
        chunk_ids = self.get_chunk_ids_by_file(source, file_path)
        if chunk_ids:
            try:
                table.delete(f"source = '{source}' AND file_path = '{file_path}'")
            except Exception:
                logger.warning(
                    "Failed to delete chunks for %s:%s", source, file_path,
                    exc_info=True,
                )
        return chunk_ids

    def get_chunk_ids_by_file(self, source: str, file_path: str) -> list[str]:
        """Return chunk_ids for a specific file within a source."""
        table = self._get_or_create_table()
        try:
            rows = (
                table.search()
                .where(f"source = '{source}' AND file_path = '{file_path}'")
                .select(["chunk_id"])
                .limit(10000)
                .to_list()
            )
            return [r["chunk_id"] for r in rows]
        except Exception:
            return []

    def delete_stale(self, source: str, *, before: float) -> int:
        """Remove chunks not refreshed after *before* timestamp."""
        table = self._get_or_create_table()
        try:
            initial = table.count_rows()
            table.delete(
                f"(source = '{source}' OR source LIKE '{source}:%') "
                f"AND indexed_at < {before}"
            )
            remaining = table.count_rows()
            removed = initial - remaining
            if removed > 0:
                logger.info(
                    "Deleted %d stale chunks for source %s", removed, source
                )
            return removed
        except Exception:
            return 0

    def get_stale_chunk_ids_by_file(
        self,
        source: str,
        file_path: str,
        *,
        before: float,
    ) -> list[str]:
        """Return chunk_ids for stale chunks of a specific file."""
        table = self._get_or_create_table()
        try:
            rows = (
                table.search()
                .where(
                    f"source = '{source}' AND file_path = '{file_path}' "
                    f"AND indexed_at < {before}"
                )
                .select(["chunk_id"])
                .limit(10000)
                .to_list()
            )
            return [r["chunk_id"] for r in rows]
        except Exception:
            return []

    def delete_stale_by_file(
        self,
        source: str,
        file_path: str,
        *,
        before: float,
    ) -> int:
        """Remove stale chunks for a specific file (indexed before *before*)."""
        table = self._get_or_create_table()
        try:
            initial = table.count_rows()
            table.delete(
                f"source = '{source}' AND file_path = '{file_path}' "
                f"AND indexed_at < {before}"
            )
            remaining = table.count_rows()
            removed = initial - remaining
            if removed > 0:
                logger.info(
                    "Deleted %d stale chunks for %s:%s",
                    removed, source, file_path,
                )
            return removed
        except Exception:
            return 0

    # -- maintenance ---------------------------------------------------------

    def compact(self, *, older_than_days: int = 2) -> dict[str, int]:
        """Compact fragments and clean up old versions.

        Uses ``table.optimize()`` which merges small data fragments
        into larger ones, prunes old versions, and optimizes indices.

        Returns:
            Dict with keys: fragments_before, fragments_after.
        """
        import datetime

        table = self._get_or_create_table()
        stats: dict[str, int] = {
            "fragments_before": 0,
            "fragments_after": 0,
        }

        try:
            lance_dataset = table.to_lance()
            stats["fragments_before"] = len(lance_dataset.get_fragments())
        except Exception:
            pass

        try:
            cutoff = datetime.timedelta(days=older_than_days)
            table.optimize(cleanup_older_than=cutoff)
            logger.info(
                "Optimized table %s (cleanup older than %d days)",
                self._table_name, older_than_days,
            )
        except Exception:
            logger.warning(
                "optimize failed for %s", self._table_name, exc_info=True,
            )

        try:
            lance_dataset = table.to_lance()
            stats["fragments_after"] = len(lance_dataset.get_fragments())
        except Exception:
            pass

        return stats

    # -- read ----------------------------------------------------------------

    def get_by_ids(self, chunk_ids: list[str]) -> dict[str, ChunkRecord]:
        """Fetch chunk records by ID for hydrating search results."""
        if not chunk_ids:
            return {}
        table = self._get_or_create_table()
        id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
        try:
            rows = (
                table.search()
                .where(f"chunk_id IN ({id_list})")
                .limit(len(chunk_ids))
                .to_list()
            )
        except Exception:
            return {}

        return {
            r["chunk_id"]: ChunkRecord(
                chunk_id=r["chunk_id"],
                source=r["source"],
                file_path=r["file_path"],
                chunk_type=r["chunk_type"],
                language=r["language"],
                content=r["content"],
                content_hash=r.get("content_hash", ""),
                line_start=r["line_start"],
                line_end=r["line_end"],
                metadata=_decode_metadata(r.get("metadata")),
            )
            for r in rows
        }

    def get_document(self, source: str, file_path: str) -> ChunkRecord | None:
        """Reconstruct a file's full indexed text from all its chunks.

        Returns a single ChunkRecord whose ``content`` is every chunk for
        ``(source, file_path)`` joined in line order (deduplicated), so the
        preview can show the whole artifact rather than just the matched
        chunk (#882). ``line_start``/``line_end`` span the full file. Returns
        None if the file has no chunks.
        """
        table = self._get_or_create_table()
        try:
            rows = (
                table.search()
                .where(
                    f"source = '{_sql_str(source)}' "
                    f"AND file_path = '{_sql_str(file_path)}'"
                )
                .limit(10000)
                .to_list()
            )
        except Exception:
            return None
        if not rows:
            return None

        rows.sort(key=lambda r: (r.get("line_start", 0), r.get("line_end", 0)))

        # Cap the reconstructed size so a huge file can't bloat the response
        # or stall the browser preview (#882). Chunks are appended in line
        # order until the cap is reached.
        seen: set[str] = set()
        parts: list[str] = []
        total = 0
        for r in rows:
            text = r.get("content", "")
            key = r.get("content_hash") or text
            if key in seen:
                continue
            seen.add(key)
            parts.append(text)
            total += len(text)
            if total >= _MAX_DOCUMENT_CHARS:
                break

        first = rows[0]
        return ChunkRecord(
            chunk_id=first["chunk_id"],
            source=first["source"],
            file_path=first["file_path"],
            chunk_type=first.get("chunk_type", ""),
            language=first.get("language", ""),
            content="\n\n".join(parts),
            content_hash=first.get("content_hash", ""),
            line_start=min(r.get("line_start", 0) for r in rows),
            line_end=max(r.get("line_end", 0) for r in rows),
            metadata=_decode_metadata(first.get("metadata")),
        )

    def get_chunks_for_file(
        self, source: str, file_path: str,
    ) -> list[ChunkRecord]:
        """Return all chunks for a ``(source, file_path)`` item, line-ordered.

        Unlike ``get_document`` (which joins chunk text into one preview), this
        keeps the chunks separate so callers can pool their per-chunk vectors
        and weight by line span.  Returns an empty list if the item has no
        chunks.
        """
        table = self._get_or_create_table()
        try:
            rows = (
                table.search()
                .where(
                    f"source = '{_sql_str(source)}' "
                    f"AND file_path = '{_sql_str(file_path)}'"
                )
                .limit(10000)
                .to_list()
            )
        except Exception:
            return []
        if not rows:
            return []

        rows.sort(key=lambda r: (r.get("line_start", 0), r.get("line_end", 0)))
        return [
            ChunkRecord(
                chunk_id=r["chunk_id"],
                source=r["source"],
                file_path=r["file_path"],
                chunk_type=r.get("chunk_type", ""),
                language=r.get("language", ""),
                content=r.get("content", ""),
                content_hash=r.get("content_hash", ""),
                line_start=r.get("line_start", 0),
                line_end=r.get("line_end", 0),
                metadata=_decode_metadata(r.get("metadata")),
            )
            for r in rows
        ]

    def ensure_fts_index(self, *, max_retries: int = 5) -> bool:
        """Create or rebuild the BM25 full-text index on content.

        Serialized via a threading lock to prevent concurrent
        ``CreateIndex`` commit conflicts.  On failure, the previous
        index (if any) remains usable - ``_fts_available`` is only
        ``False`` when no index has ever been built successfully.

        Retries on LanceDB retryable commit conflicts with exponential
        backoff plus random jitter.
        """
        if not self._fts_lock.acquire(timeout=120):
            logger.warning("FTS lock acquisition timed out")
            return self._fts_available

        try:
            return self._rebuild_fts_locked(max_retries=max_retries)
        finally:
            self._fts_lock.release()

    @contextlib.contextmanager
    def _fts_file_lock(self):
        """Best-effort *cross-process* exclusive lock around ``create_fts_index``.

        The in-process ``_fts_lock`` cannot coordinate with a *different* OS
        process (e.g. a standalone ``agent-index reindex`` run, or a serve process that
        died mid-build) issuing its own LanceDB ``CreateIndex``. Two concurrent
        ``CreateIndex`` transactions livelock LanceDB's optimistic-concurrency
        resolver and can leave the index stuck on a version forever (#1818).

        Yields ``True`` if the lock was acquired, ``False`` if another process
        holds it -- in which case the caller should skip this rebuild rather
        than pile on with a competing ``CreateIndex``.
        """
        try:
            import fcntl
        except ImportError:  # non-POSIX: fall back to in-process serialization
            yield True
            return

        lock_path = os.path.join(self._db_path, ".fts_index.lock")
        fd = None
        acquired = False
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
            yield acquired
        finally:
            if fd is not None:
                if acquired:
                    with contextlib.suppress(OSError):
                        fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _run_fts_build(self, *, timeout: float = 300.0) -> None:
        """Build the BM25 FTS index in a FRESH subprocess (optimize + create).

        LanceDB's sync ``create_fts_index`` bridges to a module-singleton
        background asyncio loop; inside the agent-index server's full lifespan that
        bridge DEADLOCKS (#3587) -- the worker blocks forever on the
        background-loop result while the loop sits idle, so uvicorn never binds
        and the service is DOWN. A fresh interpreter has a pristine background
        loop, so the build completes normally out of process (verified: works
        standalone in <1s where the in-process call hangs). Running it out of
        process ALSO (a) makes the build KILLABLE with a hard timeout so a stuck
        build can never wedge the server (never-strand, #1208), and (b) lets us
        ``optimize()`` (compact) the table first -- a full reindex leaves
        hundreds of tiny fragments/versions, the pathological state that made the
        in-process build crawl before it deadlocked. The server's cached table
        handle sees the freshly-built index immediately, with no reopen
        (verified), so search picks up FTS without a restart.
        """
        code = (
            "import sys, lancedb\n"
            "t = lancedb.connect(sys.argv[1]).open_table(sys.argv[2])\n"
            "try:\n"
            "    t.optimize()\n"
            "except Exception as e:\n"
            "    print(f'optimize skipped: {e}', file=sys.stderr)\n"
            "t.create_fts_index('content', replace=True)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code, self._db_path, self._table_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            # Surface the child's stderr so the caller's retry loop can still
            # distinguish a (retryable) LanceDB commit conflict from a hard error.
            raise RuntimeError(
                f"FTS build subprocess failed (rc={proc.returncode}): "
                f"{proc.stderr.strip()[-500:]}"
            )

    def _rebuild_fts_locked(self, *, max_retries: int) -> bool:
        """Inner FTS rebuild - caller must hold the in-process ``_fts_lock``.

        Runs the actual build in a subprocess (``_run_fts_build``) to dodge the
        in-process LanceDB sync->async deadlock (#3587). Additionally takes a
        cross-process file lock (#1818) so a standalone ``agent-index`` process cannot
        race/poison the shared Lance table, and arms a capped exponential backoff
        on persistent failure so the 60s maintainer stops hammering a stuck
        conflict every tick.
        """
        table = self._get_or_create_table()
        try:
            count = table.count_rows()
            if count == 0:
                logger.info("Content table empty, skipping FTS index")
                return False
        except Exception:
            logger.warning("Failed to count rows for FTS check", exc_info=True)
            return self._fts_available

        with self._fts_file_lock() as have_lock:
            if not have_lock:
                # Another process is (re)building FTS. Don't compete with a
                # concurrent CreateIndex; re-check shortly. Not a failure, so
                # the consecutive-failure backoff is left untouched.
                logger.debug(
                    "FTS rebuild skipped: another process holds the index lock"
                )
                self._fts_next_retry_at = time.monotonic() + _FTS_LOCK_RECHECK_S
                return self._fts_available

            last_err: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    self._run_fts_build()
                    # The subprocess built the index on its OWN connection; drop
                    # this store's cached table handle so the next query re-opens
                    # at the latest version and actually sees the new FTS index
                    # (a handle opened before the first-ever index does not pick
                    # it up otherwise). Atomic under the GIL; concurrent readers
                    # holding the old handle are unaffected.
                    self._table = None
                    self._fts_available = True
                    self._fts_dirty = False
                    self._fts_consecutive_failures = 0
                    self._fts_next_retry_at = 0.0
                    logger.info("FTS index created/rebuilt on %d chunks", count)
                    return True
                except Exception as e:
                    last_err = e
                    err_msg = str(e).lower()
                    retryable = "retryable" in err_msg or "commit conflict" in err_msg
                    if retryable and attempt < max_retries:
                        delay = (2.0 ** attempt) + random.uniform(0, 1)
                        logger.warning(
                            "FTS index creation hit commit conflict (attempt %d/%d), "
                            "retrying in %.1fs",
                            attempt, max_retries, delay,
                        )
                        time.sleep(delay)
                    else:
                        break

        # Failed - arm a capped exponential backoff so the maintainer stops
        # retrying a stuck conflict every tick (#1818). Keep _fts_dirty True
        # (work is still pending) and don't downgrade _fts_available -- the
        # previous index (at an earlier LanceDB version) may still be queryable.
        self._fts_consecutive_failures += 1
        backoff = min(
            _FTS_RETRY_CAP_S,
            _FTS_RETRY_BASE_S * (2.0 ** (self._fts_consecutive_failures - 1)),
        )
        backoff += random.uniform(0, _FTS_RETRY_JITTER_S)
        self._fts_next_retry_at = time.monotonic() + backoff
        self._fts_dirty = True
        log = (
            logger.error
            if self._fts_consecutive_failures >= _FTS_FAILURE_ALERT_THRESHOLD
            else logger.warning
        )
        log(
            "FTS index rebuild failed after %d attempt(s): %s "
            "(fts_available=%s, consecutive_failures=%d, next retry in %.0fs - "
            "previous index may still serve queries)",
            max_retries, last_err, self._fts_available,
            self._fts_consecutive_failures, backoff,
        )
        return self._fts_available

    def mark_fts_dirty(self) -> None:
        """Signal that content has changed and FTS should be rebuilt.

        Does not trigger an immediate rebuild - the server's FTS
        maintainer task or the post-index hook will pick it up.

        A genuine content change may also clear whatever conflict was
        blocking a rebuild, so reset the failure backoff gate (#1818) and let
        the maintainer attempt promptly rather than waiting out the cooldown.
        """
        self._fts_dirty = True
        self._fts_consecutive_failures = 0
        self._fts_next_retry_at = 0.0

    def fts_rebuild_due(self) -> bool:
        """True when a rebuild is needed *and* the backoff gate has elapsed.

        The FTS maintainer consults this instead of ``fts_dirty`` directly so a
        stuck ``CreateIndex`` conflict backs off (capped exponential) rather
        than retrying every 60s tick forever (#1818).
        """
        needs = (not self._fts_available) or self._fts_dirty
        return needs and time.monotonic() >= self._fts_next_retry_at

    @property
    def fts_consecutive_failures(self) -> int:
        """Number of consecutive failed FTS rebuild cycles (0 when healthy)."""
        return self._fts_consecutive_failures

    @property
    def fts_dirty(self) -> bool:
        return self._fts_dirty

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    def fts_search(
        self,
        query: str,
        *,
        limit: int = 10,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
    ) -> list[tuple[str, float]]:
        """BM25 full-text search. Returns (chunk_id, bm25_score) pairs."""
        if not self.fts_available:
            return []

        sanitized = sanitize_fts_query(query)
        if not sanitized:
            return []

        table = self._get_or_create_table()
        try:
            fts_query = table.search(sanitized, query_type="fts").limit(limit)

            filters: list[str] = []
            if source:
                src_eq = _sql_str(source)
                src_like = _like_pattern(source) + ":%"
                filters.append(
                    f"(source = '{src_eq}' OR source LIKE '{src_like}' ESCAPE '\\')"
                )
            if language:
                filters.append(f"language = '{_sql_str(language)}'")
            if file_path_glob:
                filters.append(f"file_path LIKE '{_sql_str(file_path_glob)}'")
            if repo:
                repo_like = "%:" + _like_pattern(repo)
                filters.append(f"source LIKE '{repo_like}' ESCAPE '\\'")
            if filters:
                fts_query = fts_query.where(" AND ".join(filters))

            results = fts_query.to_list()
        except Exception:
            logger.debug("FTS search failed", exc_info=True)
            return []

        return [
            (r["chunk_id"], r.get("_score", 0.0))
            for r in results
        ]

    # -- stats ---------------------------------------------------------------

    def distinct_sources(self) -> set[str]:
        """Return the set of distinct ``source`` values across all chunks.

        Projects only the ``source`` column (no pandas, and crucially no
        read of the large ``content`` column) so it stays cheap even as the
        index grows to cover every Forge repo.
        """
        table = self._get_or_create_table()
        try:
            total = table.count_rows()
            if total == 0:
                return set()
            rows = table.search().select(["source"]).limit(total).to_list()
            return {r["source"] for r in rows if r.get("source")}
        except Exception:
            logger.debug("distinct_sources scan failed", exc_info=True)
            return set()

    def distinct_items(self) -> list[tuple[str, str]]:
        """Return distinct ``(source, file_path)`` content items, sorted.

        Projects only ``source`` + ``file_path`` (never the large ``content``
        column) so the similarity-cluster pass can enumerate every content
        item cheaply even as the index grows.
        """
        table = self._get_or_create_table()
        try:
            total = table.count_rows()
            if total == 0:
                return []
            rows = (
                table.search()
                .select(["source", "file_path"])
                .limit(total)
                .to_list()
            )
        except Exception:
            logger.debug("distinct_items scan failed", exc_info=True)
            return []
        items = {
            (r["source"], r["file_path"])
            for r in rows
            if r.get("source") is not None and r.get("file_path") is not None
        }
        return sorted(items)

    def stats(self) -> dict[str, Any]:
        """Return content store statistics."""
        table = self._get_or_create_table()
        try:
            total = table.count_rows()
        except Exception:
            total = 0

        info: dict[str, Any] = {
            "total_chunks": total,
            "table_name": self._table_name,
        }

        if total > 0:
            try:
                df = table.to_pandas()
                info["sources"] = df["source"].value_counts().to_dict()
                info["languages"] = df["language"].value_counts().to_dict()
            except Exception:
                pass

        return info


# -- helpers -----------------------------------------------------------------


def _sql_str(value: str) -> str:
    """Escape a value for use inside a single-quoted SQL string literal."""
    return value.replace("'", "''")


def _like_pattern(value: str) -> str:
    """Escape a value for a SQL ``LIKE`` pattern (with ``ESCAPE '\\'``).

    Escapes the ``LIKE`` metacharacters ``%`` and ``_`` (and the escape
    char ``\\`` itself) so that e.g. a repo named ``owner/home_assistant``
    does not also match ``owner/homeXassistant``.  The result is still
    single-quoted by the caller, so SQL-quote escaping is applied last.
    """
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return _sql_str(escaped)


def sanitize_fts_query(query: str) -> str:
    """Strip FTS operators and special chars that confuse Tantivy."""
    cleaned = re.sub(r'[+\-!(){}[\]^"~*?:\\/<>]', " ", query)
    return re.sub(r"\s+", " ", cleaned).strip()
