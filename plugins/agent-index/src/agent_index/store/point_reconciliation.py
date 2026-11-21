"""Bounded stale-ID cleanup under the existing selected source writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .file_projection import capture_file_projection
from .multi_model_store import MultiModelStore
from .point_content import _prepared_tables
from .repo_filter import _sql_str
from .scalar_index import MAX_SEGMENTS, ScalarIndexPreparationRequired, inspect_index, require_budget
from .source_layout import SourceLayoutError

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig

    from .content_store import ContentStore
    from .source_layout import PointScope
    from .vector_table import VectorTable


def reconcile_point_file(
    config: IndexConfig, store: MultiModelStore, source: str, file_path: str,
    keep_chunk_ids: tuple[str, ...], *, max_rows: int,
    retained_chunk_ids: tuple[str, ...] = (), point_scope: PointScope | None,
) -> int:
    """Remove observed stale IDs from every bound model, then canonical content.

    Return the number of distinct IDs with removed rows, including known
    vector-only remnants. Unknown orphans are not discovered. This native
    prerequisite neither validates provider ordering nor publishes coverage,
    checkpoints, applied versions or claim completion.
    """
    if (type(max_rows) is not int or max_rows < 1
            or not isinstance(keep_chunk_ids, tuple) or len(keep_chunk_ids) > max_rows
            or any(not isinstance(cid, str) or not cid for cid in keep_chunk_ids)
            or len(set(keep_chunk_ids)) != len(keep_chunk_ids)):
        raise ValueError("Kept chunk IDs must be unique and fit a positive integer row budget")
    domain = store._source_domain
    content = store.content_store
    if (domain is None or point_scope is None or not domain.writing
            or content._source_domain is not domain
            or Path(store._db_path).resolve() != domain.path.resolve()):
        raise SourceLayoutError("Point cleanup requires the owning source writer and scope")
    with domain._write(content.db, point_scope=point_scope):
        canonical, vectors = _prepared_tables(content, point_scope=point_scope)
        selected = MultiModelStore(
            domain.path, content_table=canonical._table_name, point_scope=point_scope,
        )
        selected._content = canonical
        for vector in vectors:
            assert vector._model_id is not None
            selected._vector_tables[vector._model_id] = vector
        projection = capture_file_projection(
            config, selected, source, file_path, max_rows=max_rows,
            retained_chunk_ids=retained_chunk_ids, point_scope=point_scope,
        )
        keep = set(keep_chunk_ids)
        ids_by_table = {
            canonical._table_name: {
                row["chunk_id"] for row in json.loads(projection.content_rows)
            } - keep,
            **{
                model.table_name: {row["chunk_id"] for row in json.loads(model.rows)} - keep
                for model in projection.models
            },
        }
        owners: list[ContentStore | VectorTable] = [canonical, *vectors]
        for owner in owners:
            columns = ["chunk_id", "file_path", "source"] if owner is canonical else ["chunk_id"]
            table: Any = owner._table
            for column in columns:
                state, _ = inspect_index(table, column=column)
                if column == "source" and not state.prepared:
                    continue
                if not state.prepared:
                    raise ScalarIndexPreparationRequired("Point cleanup requires prepared ingestion indices")
                require_budget(state, 0)
                if ids_by_table[owner._table_name] and state.segments >= MAX_SEGMENTS:
                    raise ScalarIndexPreparationRequired("Point cleanup needs a reserved ingestion index segment")
        deletion_owners: list[ContentStore | VectorTable] = [*vectors, canonical]
        for owner in deletion_owners:
            ids = ids_by_table[owner._table_name]
            if not ids:
                continue
            sql = ", ".join(f"'{_sql_str(cid)}'" for cid in sorted(ids))
            kind = "canonical-delete" if owner is canonical else "vector-delete"
            table = owner._table
            with domain.bucket_mutation(owner._table_name, f"{kind}:{owner._table_name}", [source]):
                table.delete(f"chunk_id IN ({sql})")
        return len(set().union(*ids_by_table.values()))
