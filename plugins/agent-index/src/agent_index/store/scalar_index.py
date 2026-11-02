"""Shared, explicitly budgeted B-tree preparation under the source-domain owner."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lance import LanceDataset
    from lancedb.table import LanceTable

    from .content_store import ContentStore
    from .vector_table import VectorTable

log = logging.getLogger(__name__)
MAX_SEGMENTS = 32


class ScalarIndexPreparationRequired(RuntimeError):
    """A bounded read needs explicit preparation or owner-coordinated repair."""


@dataclass(frozen=True)
class ScalarIndexState:
    version: int
    prepared: bool
    physical_unindexed_rows: int
    segments: int


def inspect_index(table: LanceTable, *, column: str) -> tuple[ScalarIndexState, LanceDataset]:
    name = f"{column}_idx"
    dataset = table.to_lance()
    matches = [index for index in dataset.describe_indices() if index.name == name]
    if len(matches) > 1:
        raise ScalarIndexPreparationRequired(f"Ambiguous {column} index metadata")
    index = matches[0] if matches else None
    if index is not None and (
        index.index_type != "BTree" or index.field_names != [column]
    ):
        raise ScalarIndexPreparationRequired(f"Incompatible {column} index; explicit repair required")
    indexed_fragments = {
        fragment for segment in index.segments for fragment in segment.fragment_ids
    } if index is not None else set()
    unindexed = sum(
        fragment.metadata.physical_rows for fragment in dataset.get_fragments()
        if fragment.fragment_id not in indexed_fragments
    )
    if index is not None:
        uncovered = dataset.stats.index_stats(name).get("num_unindexed_rows")
        if type(uncovered) is not int or not 0 <= uncovered <= unindexed:
            raise ScalarIndexPreparationRequired(
                f"{column} row/fragment coverage disagrees; explicit repair required"
            )
    return ScalarIndexState(
        table.version, index is not None, unindexed,
        len(index.segments) if index is not None else 0,
    ), dataset


def require_budget(
    state: ScalarIndexState, max_rows: int, *, max_segments: int = MAX_SEGMENTS,
) -> None:
    if state.physical_unindexed_rows > max_rows:
        raise ScalarIndexPreparationRequired(
            f"Scalar index needs {state.physical_unindexed_rows} physical rows; "
            f"preparation budget is {max_rows}"
        )
    extra_segment = int(state.prepared and state.physical_unindexed_rows > 0)
    if state.segments + extra_segment > max_segments:
        raise ScalarIndexPreparationRequired(
            "Scalar index segment budget exceeded; explicit consolidation required"
        )


def prepare_index(
    owner: ContentStore | VectorTable, *, column: str, max_rows: int,
    max_segments: int = MAX_SEGMENTS,
) -> ScalarIndexState:
    """Prepare a column B-tree without implicit consolidation or data compaction."""
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("Scalar preparation budget must be a nonnegative integer")
    domain = owner._source_domain
    if domain is None:
        raise ScalarIndexPreparationRequired("Bounded preparation requires a participating source store")
    with domain.read(owner._db):
        table = owner._get_or_create_table()
        state, _ = inspect_index(table, column=column)
        require_budget(state, max_rows, max_segments=max_segments)
        if state.prepared and state.physical_unindexed_rows == 0:
            return state
    refusal: ScalarIndexPreparationRequired
    with domain.write(owner._db):
        try:
            table = owner._get_or_create_table()
            state, dataset = inspect_index(table, column=column)
            require_budget(state, max_rows, max_segments=max_segments)
        except ScalarIndexPreparationRequired as exc:
            refusal = exc
        else:
            if state.prepared and state.physical_unindexed_rows == 0:
                return state
            covered_fts = domain.receipt().get("fts_coverage", {}).get(owner._table_name) == state.version
            with domain.physical_maintenance(owner._table_name, "scalar-index:" + owner._table_name):
                name = f"{column}_idx"
                if state.prepared:
                    dataset.optimize.optimize_indices(
                        index_names=[name], num_indices_to_merge=0, retrain=False,
                    )
                else:
                    from lancedb.index import BTree

                    table.create_index(column, config=BTree(), name=name, replace=False)
            table.checkout_latest()
            after, _ = inspect_index(table, column=column)
            if not after.prepared or after.physical_unindexed_rows:
                raise RuntimeError("Scalar index preparation did not cover the current table")
            require_budget(after, 0, max_segments=max_segments)
            if covered_fts:
                domain.cover_fts(owner)
            log.info(
                "Prepared %s index for %s: %d physical rows, %d segments",
                column, owner._table_name, state.physical_unindexed_rows, after.segments,
            )
            return after
    raise refusal


def consolidation_rows(state: ScalarIndexState, dataset: LanceDataset, *, max_rows: int) -> int:
    """Preflight the whole physical table, not just live rows or an unindexed tail."""
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("Scalar consolidation budget must be a nonnegative integer")
    if not state.prepared:
        raise ScalarIndexPreparationRequired("Scalar consolidation requires an existing prepared index")
    if state.segments <= 1 and state.physical_unindexed_rows == 0:
        return 0
    rows = sum(fragment.metadata.physical_rows for fragment in dataset.get_fragments())
    if rows > max_rows:
        raise ScalarIndexPreparationRequired(
            f"Scalar consolidation needs {rows} physical rows; consolidation budget is {max_rows}"
        )
    return rows


def consolidate_index(
    owner: ContentStore | VectorTable, *, column: str, max_rows: int,
) -> ScalarIndexState:
    """Explicitly merge one named B-tree under its existing source-domain owner."""
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("Scalar consolidation budget must be a nonnegative integer")
    domain = owner._source_domain
    if domain is None:
        raise ScalarIndexPreparationRequired("Consolidation requires a participating source store")
    with domain.read(owner._db):
        state, dataset = inspect_index(owner._get_or_create_table(), column=column)
        consolidation_rows(state, dataset, max_rows=max_rows)
        if state.segments <= 1 and state.physical_unindexed_rows == 0:
            return state
    refusal: ScalarIndexPreparationRequired
    with domain.write(owner._db):
        try:
            table = owner._get_or_create_table()
            state, dataset = inspect_index(table, column=column)
            rows = consolidation_rows(state, dataset, max_rows=max_rows)
        except ScalarIndexPreparationRequired as exc:
            refusal = exc
        else:
            if state.segments <= 1 and state.physical_unindexed_rows == 0:
                return state
            covered_fts = domain.receipt().get("fts_coverage", {}).get(owner._table_name) == state.version
            with domain.physical_maintenance(
                owner._table_name, "scalar-consolidate:" + owner._table_name,
            ):
                dataset.optimize.optimize_indices(
                    index_names=[f"{column}_idx"],
                    num_indices_to_merge=state.segments, retrain=False,
                )
            table.checkout_latest()
            after, _ = inspect_index(table, column=column)
            if not after.prepared or after.physical_unindexed_rows or after.segments != 1:
                raise RuntimeError("Scalar consolidation did not produce one fully covered segment")
            if covered_fts:
                domain.cover_fts(owner)
            log.info(
                "Consolidated %s index for %s: %d physical rows, %d segments into %d",
                column, owner._table_name, rows, state.segments, after.segments,
            )
            return after
    raise refusal
