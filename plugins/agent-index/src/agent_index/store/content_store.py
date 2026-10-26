"""Content store - chunk text, metadata, and BM25 full-text index.

Stores the canonical copy of every indexed chunk.  Vector tables
(managed by ``VectorTable``) reference rows here by ``chunk_id``.
FTS is global across all chunks regardless of which embedding model
produced vectors for them.
"""

from __future__ import annotations

import contextlib as contextlib
import json
import logging
import os as os
import random
import re
import subprocess as subprocess
import sys as sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

import pyarrow as pa
from agent_procutil import no_window_kwargs as no_window_kwargs

from agent_index.store.content_fts_worker import _ContentFtsWorkerMixin, fts_retryable
from agent_index.store.content_item_reads import _ContentItemReadsMixin
from agent_index.store.lance_connection import connect_content_store
from agent_index.store.repo_filter import _like_pattern, _sql_str, repo_filter_sql
from agent_index.store.source_layout import (
    SourceLayoutError,
    domain_for,
    source_fts_read,
    source_read,
    source_write,
)

if TYPE_CHECKING:
    from pathlib import Path

    from agent_index.chunking.base import Chunk

logger = logging.getLogger(__name__)


class FtsBuildOutcome(Enum):
    """Completion is independent of whether a prior index remains queryable."""

    SUCCESS = "success"
    EMPTY = "empty-noop"
    FAILURE = "failure"

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


class ContentStore(_ContentFtsWorkerMixin, _ContentItemReadsMixin):
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
        self._source_domain = domain_for(db_path)
        self._db = connect_content_store(db_path, source_aware=self._source_domain is not None)
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
            if self._source_domain is not None:
                self._table.checkout_latest()
            return self._table
        try:
            table = self._db.open_table(self._table_name)
        except Exception:
            if self._source_domain is not None and not self._source_domain.writing:
                raise SourceLayoutError("source-aware content table is not prepared") from None
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
            if self._source_domain is not None:
                raise SourceLayoutError("source-aware schema is incompatible")
            table.add_columns({"metadata": "''"})
            logger.info(
                "Migrated content table %s: added 'metadata' column",
                self._table_name,
            )

    # -- write ---------------------------------------------------------------

    @source_write
    def upsert(self, chunks: list[Chunk]) -> int:
        """Insert or update chunk content by chunk_id.

        Returns the number of rows written.
        """
        table = self._get_or_create_table()
        if self._source_domain is not None:
            from .source_projection import check_content_sources
            check_content_sources(self, chunks)
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
        if self._source_domain is not None:
            self._source_domain.effect(f"content-upsert:{self._table_name}")
        try:
            id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
            table.delete(f"chunk_id IN ({id_list})")
        except Exception:
            if self._source_domain is not None:
                raise
            pass

        table.add(records)
        if self._source_domain is not None:
            from .source_projection import sync_content_projection

            sync_content_projection(self._db, chunks)
        logger.info("Upserted %d chunks to content store", len(records))
        return len(records)

    @source_write
    def delete_by_source(self, source: str) -> None:
        """Remove all chunks from a specific source (prefix-matching)."""
        if self._source_domain is not None:
            from .source_projection import delete_canonical

            return delete_canonical(self, (
                f"(source = '{_sql_str(source)}' OR "
                f"source LIKE '{_like_pattern(source)}:%' ESCAPE '\\')"
            ))
        table = self._get_or_create_table()
        try:
            table.delete(f"source = '{source}' OR source LIKE '{source}:%'")
            logger.info("Deleted content for source: %s", source)
        except Exception:
            logger.debug("No content to delete for source: %s", source)

    @source_write
    def delete_by_source_exact(self, source: str) -> None:
        """Remove chunks for an EXACT source value (no prefix matching).

        Unlike :meth:`delete_by_source`, this only deletes rows whose
        ``source`` equals *source* exactly -- so purging a stale generic
        ``forge:code`` does not also wipe live ``forge:code:owner/repo``
        chunks. Used by source garbage collection (#879).
        """
        if self._source_domain is not None:
            from .source_projection import delete_canonical

            return delete_canonical(self, f"source = '{_sql_str(source)}'")
        table = self._get_or_create_table()
        try:
            table.delete(f"source = '{_sql_str(source)}'")
            logger.info("Deleted content for exact source: %s", source)
        except Exception:
            logger.debug("No content to delete for exact source: %s", source)

    @source_read
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

    @source_read
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
            if self._source_domain is not None:
                raise
            logger.debug("source_counts scan failed", exc_info=True)
        return counts

    @source_write
    def delete_by_file(self, source: str, file_path: str) -> list[str]:
        """Remove all chunks for a specific file. Returns deleted chunk_ids."""
        if self._source_domain is not None:
            from .source_projection import delete_canonical

            return delete_canonical(self, (
                f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}'"
            ), return_ids=True)
        table = self._get_or_create_table()
        # Query chunk_ids before deleting so callers can clean up vector tables
        chunk_ids = self.get_chunk_ids_by_file(source, file_path)
        if chunk_ids:
            table.delete(
                f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}'"
            )
        return chunk_ids

    @source_read
    def get_chunk_ids_by_file(self, source: str, file_path: str) -> list[str]:
        """Return chunk_ids for a specific file within a source."""
        table = self._get_or_create_table()
        predicate = f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}'"
        count = table.count_rows(predicate)
        if not count:
            return []
        rows = table.search().where(predicate).select(["chunk_id"]).limit(count).to_list()
        if len(rows) != count:
            raise RuntimeError("Incomplete file chunk lookup")
        return [r["chunk_id"] for r in rows]

    @source_write
    def delete_stale(self, source: str, *, before: float) -> int:
        """Remove chunks not refreshed after *before* timestamp."""
        if self._source_domain is not None:
            from .source_projection import delete_canonical

            return delete_canonical(self, (
                f"(source = '{_sql_str(source)}' OR "
                f"source LIKE '{_like_pattern(source)}:%' ESCAPE '\\') AND indexed_at < {before}"
            ))
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

    @source_read
    def get_stale_chunk_ids_by_file(
        self,
        source: str,
        file_path: str,
        *,
        before: float,
    ) -> list[str]:
        """Return chunk_ids for stale chunks of a specific file."""
        table = self._get_or_create_table()
        predicate = (
            f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}' "
            f"AND indexed_at < {before}"
        )
        count = table.count_rows(predicate)
        if not count:
            return []
        rows = table.search().where(predicate).select(["chunk_id"]).limit(count).to_list()
        if len(rows) != count:
            raise RuntimeError("Incomplete stale chunk lookup")
        return [r["chunk_id"] for r in rows]

    @source_write
    def delete_stale_by_file(
        self,
        source: str,
        file_path: str,
        *,
        before: float,
    ) -> int:
        """Remove stale chunks for a specific file (indexed before *before*)."""
        if self._source_domain is not None:
            from .source_projection import delete_canonical

            return delete_canonical(self, (
                f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}' "
                f"AND indexed_at < {before}"
            ))
        table = self._get_or_create_table()
        predicate = (
            f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}' "
            f"AND indexed_at < {before}"
        )
        removed = table.count_rows(predicate)
        if removed:
            table.delete(predicate)
        return removed

    # -- maintenance ---------------------------------------------------------

    @source_write
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
            if self._source_domain is not None:
                self._source_domain.effect(f"content-compact:{self._table_name}")
            table.optimize(cleanup_older_than=cutoff)
            logger.info(
                "Optimized table %s (cleanup older than %d days)",
                self._table_name, older_than_days,
            )
        except Exception:
            if self._source_domain is not None:
                raise
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

    @source_read
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
            if self._source_domain is not None:
                raise
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

    @source_read
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

    @source_write
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
            if self._source_domain is not None:
                raise SourceLayoutError("source-aware FTS lock acquisition failed")
            return self._fts_available

        try:
            result = self._rebuild_fts_locked(max_retries=max_retries)
            if self._source_domain is not None and result is FtsBuildOutcome.FAILURE:
                raise SourceLayoutError("source-aware FTS preparation failed")
            if self._source_domain is not None:
                self._source_domain.cover_fts(self)
            if result is FtsBuildOutcome.EMPTY:
                return False
            return self._fts_available
        finally:
            self._fts_lock.release()



    @staticmethod
    def _index_columns(index: object) -> list[str]:
        if isinstance(index, dict):
            raw = index.get("columns")
        else:
            raw = getattr(index, "columns", None)
        if isinstance(raw, str):
            return [raw]
        if isinstance(raw, (list, tuple)):
            return [str(value) for value in raw]
        return []

    @staticmethod
    def _index_type(index: object) -> str:
        if isinstance(index, dict):
            raw = index.get("index_type") or index.get("type")
        else:
            raw = getattr(index, "index_type", None) or getattr(index, "type", None)
        return str(raw or "").upper()

    def _durable_fts_index_exists(self, table) -> bool:
        """Best-effort check of LanceDB metadata for an existing content FTS index."""
        try:
            list_indices = table.list_indices
        except AttributeError:
            logger.debug("LanceDB table does not expose list_indices()")
            return False

        try:
            indices = list_indices()
        except Exception:
            logger.debug("Failed to check for a durable FTS index", exc_info=True)
            return False

        for index in indices:
            index_type = self._index_type(index)
            if index_type not in {"FTS", "INVERTED"}:
                continue
            if "content" in self._index_columns(index):
                return True
        return False

    def _rebuild_fts_locked(self, *, max_retries: int) -> FtsBuildOutcome:
        """Inner FTS rebuild - caller must hold the in-process ``_fts_lock``.

        Runs the actual build in a subprocess (``_run_fts_build``) to dodge the
        in-process LanceDB sync->async deadlock (#3587). Additionally takes a
        cross-process file lock (#1818) so a standalone ``agent-index`` process cannot
        race/poison the shared Lance table, and arms a capped exponential backoff
        on persistent failure so the 60s maintainer stops hammering a stuck
        conflict every tick.

        Does a full ``create_fts_index(replace=True)`` only when durable
        LanceDB metadata does not show an existing content FTS index. A
        "merely dirty" rebuild of an already-available index relies on
        ``optimize()``'s incremental FTS update instead (see
        ``_run_fts_build``), avoiding a full-corpus rescan for a small delta.

        If that incremental ``optimize()`` call fails non-retryably (e.g. a
        lancedb/lance-index panic against the on-disk index state, #2951),
        the incremental path can never succeed again -- this escalates once
        to a full ``create_fts_index(replace=True)`` rebuild instead.

        ``_fts_available`` is in-process state only, seeded ``False`` on every
        fresh ``ContentStore`` (e.g. after a restart) even when a prior
        process already built a valid FTS index. This checks LanceDB's own
        index metadata (``table.list_indices()``) for an existing ``content``
        index while holding the cross-process file lock -- avoiding both a
        wasted full rebuild per restart and a second one if another process
        just finished the first build while this one waited for the lock.
        """
        table = self._get_or_create_table()
        try:
            count = table.count_rows()
            if count == 0:
                logger.info("Content table empty, skipping FTS index")
                return FtsBuildOutcome.EMPTY
        except Exception:
            logger.warning("Failed to count rows for FTS check", exc_info=True)
            return FtsBuildOutcome.FAILURE

        with self._fts_file_lock() as have_lock:
            if not have_lock:
                # Another process is (re)building FTS. Don't compete with a
                # concurrent CreateIndex; re-check shortly. Not a failure, so
                # the consecutive-failure backoff is left untouched.
                logger.debug(
                    "FTS rebuild skipped: another process holds the index lock"
                )
                self._fts_next_retry_at = time.monotonic() + _FTS_LOCK_RECHECK_S
                return FtsBuildOutcome.FAILURE

            # Track *why* a full rebuild is chosen -- three triggers (never
            # built, recovered-unavailable, escalated-non-retryable-failure)
            # used to collapse into one "created/rebuilt" line (#1818 follow-up).
            full_reason = "never-built" if not self._fts_available else None

            if not self._fts_available:
                # A different process may have created the first durable index
                # while this one was waiting for the cross-process lock. Reopen
                # before probing so a table handle cached before that build does
                # not force a redundant full replace=True rebuild.
                self._table = None
                table = self._get_or_create_table()
                if self._durable_fts_index_exists(table):
                    self._fts_available = True
                    full_reason = None
                else:
                    full_reason = "recovered-unavailable"

            full = not self._fts_available

            def _mark_rebuilt(*, via_full: bool, reason: str | None) -> None:
                # The subprocess built the index on its OWN connection; drop
                # the cached table handle so the next query sees the fresh
                # index (a handle from before the first build won't).
                self._table = None
                self._fts_available = True
                self._fts_dirty = False
                self._fts_consecutive_failures = 0
                self._fts_next_retry_at = 0.0
                if via_full:
                    logger.info(
                        "FTS index created/rebuilt on %d chunks (full rebuild, reason=%s)",
                        count, reason or "unknown",
                    )
                else:
                    logger.info("FTS index incrementally updated on %d chunks", count)

            last_err: Exception | None = None
            last_retryable = False
            for attempt in range(1, max_retries + 1):
                try:
                    self._run_fts_build(full=full)
                    _mark_rebuilt(via_full=full, reason=full_reason)
                    return FtsBuildOutcome.SUCCESS
                except Exception as e:
                    if self._source_domain is not None and isinstance(e, OSError):
                        raise
                    last_err = e
                    retryable = fts_retryable(str(e))
                    last_retryable = retryable
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

            if not full and last_err is not None and not last_retryable:
                # A non-retryable incremental failure (#2951) would repeat
                # forever -- escalate once to a full rebuild. A merely
                # retries-exhausted conflict keeps the existing backoff.
                logger.warning(
                    "FTS incremental update hit a non-retryable failure, escalating to a full rebuild: %s",
                    last_err,
                )
                try:
                    self._run_fts_build(full=True)
                    reason = f"escalated-non-retryable-failure: {last_err}"
                    _mark_rebuilt(via_full=True, reason=reason)
                    return FtsBuildOutcome.SUCCESS
                except Exception as full_err:
                    if self._source_domain is not None and isinstance(full_err, OSError):
                        raise
                    last_err = full_err

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
        return FtsBuildOutcome.FAILURE

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

    @source_fts_read
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
                filters.append(f"({repo_filter_sql(repo)})")
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

    @source_read
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

    @source_read
    def stats(self) -> dict[str, Any]:
        """Count projected metadata in bounded batches, without loading content."""
        table = self._get_or_create_table()
        total = table.count_rows()
        info: dict[str, Any] = {
            "total_chunks": total,
            "table_name": self._table_name,
        }

        if total > 0:
            sources: Counter[str] = Counter()
            languages: Counter[str] = Counter()
            with table.search().select(["source", "language"]).limit(None).to_batches(
                batch_size=1024
            ) as batches:
                for batch in batches:
                    sources.update(value for value in batch.column("source").to_pylist()
                                   if value is not None)
                    languages.update(value for value in batch.column("language").to_pylist()
                                     if value is not None)
            info["sources"] = dict(sources.most_common())
            info["languages"] = dict(languages.most_common())
        return info


# -- helpers -----------------------------------------------------------------


def sanitize_fts_query(query: str) -> str:
    """Strip FTS operators and special chars that confuse Tantivy."""
    cleaned = re.sub(r'[+\-!(){}[\]^"~*?:\\/<>]', " ", query)
    return re.sub(r"\s+", " ", cleaned).strip()
