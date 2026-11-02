"""Explicit, budgeted scalar-index preparation for participating source stores."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .repo_filter import _sql_str
from .scalar_index import (
    ScalarIndexPreparationRequired as ChunkIdIndexPreparationRequired,
    ScalarIndexState as ChunkIdIndexState,
    inspect_index,
    prepare_index,
    require_budget,
)

if TYPE_CHECKING:
    from .content_store import ContentStore
    from .vector_table import VectorTable

INDEX_NAME = "chunk_id_idx"
MAX_SEGMENTS = 32
BATCH_SIZE = 200


def prepare_chunk_id_index(
    owner: ContentStore | VectorTable, *, max_rows: int,
) -> ChunkIdIndexState:
    """Build within an explicit budget, or append only a bounded new index segment.

    Uses the existing source-domain exclusion/receipt, not a new lock authority.
    Legacy stores need their existing writer-adoption path before using this API.
    No automatic consolidation, compaction, pruning or FTS rebuild is performed.
    """
    return prepare_index(owner, column="chunk_id", max_rows=max_rows, max_segments=MAX_SEGMENTS)


def indexed_chunk_rows(
    owner: ContentStore | VectorTable, chunk_ids: list[str], *, columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Strict point reads; never build an index or scan an unprepared tail.

    Native manifest/index metadata work is not zero or independent of fragment
    count. The bound covers canonical data rows, input batches and index segments.
    """
    if not chunk_ids:
        return []
    if any(not isinstance(cid, str) or not cid for cid in chunk_ids):
        raise ValueError("Chunk IDs must be nonempty strings")
    domain = owner._source_domain
    if domain is None:
        raise ChunkIdIndexPreparationRequired("Bounded reads require a participating source store")
    if columns is not None and "chunk_id" not in columns:
        raise ValueError("Chunk-ID reads must select chunk_id")
    with domain.read(owner._db):
        table = owner._get_or_create_table()
        state, _ = inspect_index(table, column="chunk_id")
        if not state.prepared:
            raise ChunkIdIndexPreparationRequired("Chunk-ID index is missing; explicit preparation required")
        require_budget(state, 0, max_segments=MAX_SEGMENTS)
        unique = list(dict.fromkeys(chunk_ids))
        result: list[dict[str, Any]] = []
        for start in range(0, len(unique), BATCH_SIZE):
            batch = unique[start:start + BATCH_SIZE]
            predicate = ", ".join(f"'{_sql_str(cid)}'" for cid in batch)
            query = table.search().where(f"chunk_id IN ({predicate})").limit(len(batch) + 1)
            if columns is not None:
                query = query.select(columns)
            rows = query.to_list()
            seen: set[str] = set()
            for row in rows:
                cid = row["chunk_id"]
                if cid not in batch or cid in seen:
                    raise RuntimeError("Chunk-ID read returned duplicate or unexpected canonical IDs")
                seen.add(cid)
            result.extend(rows)
        return result
