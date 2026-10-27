"""Explicit, budgeted scalar-index preparation for participating source stores."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .repo_filter import _sql_str

if TYPE_CHECKING:
    from lance import LanceDataset
    from lancedb.table import LanceTable

    from .content_store import ContentStore
    from .vector_table import VectorTable

log = logging.getLogger(__name__)
INDEX_NAME = "chunk_id_idx"
MAX_SEGMENTS = 32
BATCH_SIZE = 200


class ChunkIdIndexPreparationRequired(RuntimeError):
    """A bounded operation needs explicit preparation or owner-coordinated repair."""


@dataclass(frozen=True)
class ChunkIdIndexState:
    version: int
    prepared: bool
    physical_unindexed_rows: int
    segments: int


def _inspect(table: LanceTable) -> tuple[ChunkIdIndexState, LanceDataset]:
    dataset = table.to_lance()
    matches = [index for index in dataset.describe_indices() if index.name == INDEX_NAME]
    if len(matches) > 1:
        raise ChunkIdIndexPreparationRequired("Ambiguous chunk-ID index metadata")
    index = matches[0] if matches else None
    if index is not None and (
        index.index_type != "BTree" or index.field_names != ["chunk_id"]
    ):
        raise ChunkIdIndexPreparationRequired("Incompatible chunk-ID index; explicit repair required")
    indexed_fragments = {
        fragment for segment in index.segments for fragment in segment.fragment_ids
    } if index is not None else set()
    # Physical rows include deleted rows: a tiny live tail can still be expensive.
    unindexed = sum(
        fragment.metadata.physical_rows for fragment in dataset.get_fragments()
        if fragment.fragment_id not in indexed_fragments
    )
    if index is not None:
        uncovered = dataset.stats.index_stats(INDEX_NAME).get("num_unindexed_rows")
        if type(uncovered) is not int or not 0 <= uncovered <= unindexed:
            raise ChunkIdIndexPreparationRequired(
                "Chunk-ID row/fragment coverage disagrees; explicit repair required"
            )
    return ChunkIdIndexState(
        table.version, index is not None, unindexed,
        len(index.segments) if index is not None else 0,
    ), dataset


def _budget(state: ChunkIdIndexState, max_rows: int) -> None:
    if state.physical_unindexed_rows > max_rows:
        raise ChunkIdIndexPreparationRequired(
            f"Chunk-ID index needs {state.physical_unindexed_rows} physical rows; "
            f"preparation budget is {max_rows}"
        )
    extra_segment = int(state.prepared and state.physical_unindexed_rows > 0)
    if state.segments + extra_segment > MAX_SEGMENTS:
        raise ChunkIdIndexPreparationRequired(
            "Chunk-ID index segment budget exceeded; explicit consolidation required"
        )


def prepare_chunk_id_index(
    owner: ContentStore | VectorTable, *, max_rows: int,
) -> ChunkIdIndexState:
    """Build within an explicit budget, or append only a bounded new index segment.

    Uses the existing source-domain exclusion/receipt, not a new lock authority.
    Legacy stores need their existing writer-adoption path before using this API.
    No automatic consolidation, compaction, pruning or FTS rebuild is performed.
    """
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("Chunk-ID preparation budget must be a nonnegative integer")
    domain = owner._source_domain
    if domain is None:
        raise ChunkIdIndexPreparationRequired("Bounded preparation requires a participating source store")
    # Refuse expected budget failures before opening a dirty write transaction.
    with domain.read(owner._db):
        table = owner._get_or_create_table()
        state, _ = _inspect(table)
        _budget(state, max_rows)
        if state.prepared and state.physical_unindexed_rows == 0:
            return state
    refusal: ChunkIdIndexPreparationRequired
    with domain.write(owner._db):
        try:
            table = owner._get_or_create_table()
            state, dataset = _inspect(table)
            _budget(state, max_rows)
        except ChunkIdIndexPreparationRequired as exc:
            # A concurrent commit can change the preflight; no index effect began.
            refusal = exc
        else:
            if state.prepared and state.physical_unindexed_rows == 0:
                return state
            covered_fts = domain.receipt().get("fts_coverage", {}).get(owner._table_name) == state.version
            with domain.physical_maintenance(owner._table_name, "scalar-index:" + owner._table_name):
                if state.prepared:
                    dataset.optimize.optimize_indices(
                        index_names=[INDEX_NAME], num_indices_to_merge=0, retrain=False,
                    )
                else:
                    from lancedb.index import BTree

                    table.create_index("chunk_id", config=BTree(), name=INDEX_NAME, replace=False)
            table.checkout_latest()
            after, _ = _inspect(table)
            if not after.prepared or after.physical_unindexed_rows:
                raise RuntimeError("Chunk-ID index preparation did not cover the current table")
            _budget(after, 0)
            if covered_fts:
                domain.cover_fts(owner)
            log.info(
                "Prepared chunk-ID index for %s: %d physical rows, %d segments",
                owner._table_name, state.physical_unindexed_rows, after.segments,
            )
            return after
    raise refusal


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
        state, _ = _inspect(table)
        if not state.prepared:
            raise ChunkIdIndexPreparationRequired("Chunk-ID index is missing; explicit preparation required")
        _budget(state, 0)
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
