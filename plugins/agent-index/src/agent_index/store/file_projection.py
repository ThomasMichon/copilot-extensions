"""Opt-in, finite raw point witnesses under the existing source-domain owner."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

from .chunk_id_index import indexed_chunk_rows
from .repo_filter import _sql_str
from .scalar_index import (
    ScalarIndexPreparationRequired,
    ScalarTable,
    inspect_index,
    require_budget,
)
from .source_layout import SourceLayoutError

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig

    from .content_store import ContentStore
    from .multi_model_store import MultiModelStore
    from .vector_table import VectorTable


class _ProjectionTable(ScalarTable, Protocol):
    @property
    def schema(self) -> Any: ...

    def search(self) -> Any: ...


@dataclass(frozen=True)
class RawModelProjection:
    """Exact registered-model binding and complete raw rows, including provenance."""

    model_id: str
    table_name: str
    dimensions: int
    profile_key: tuple[str | int, ...]
    content_types: tuple[str, ...]
    rows: bytes


@dataclass(frozen=True)
class RawFileProjection:
    """Immutable observation, not an applied-version or deletion certificate."""

    domain: str
    source: str
    file_path: str
    content_table: str
    content_rows: bytes
    vector_chunk_ids: tuple[str, ...]
    models: tuple[RawModelProjection, ...]


def _raw_rows(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(
        sorted(rows, key=lambda row: row["chunk_id"]),
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _table(owner: ContentStore | VectorTable) -> _ProjectionTable:
    return cast("Callable[[], _ProjectionTable]", owner._get_or_create_table)()


def capture_file_projection(
    config: IndexConfig, store: MultiModelStore, source: str, file_path: str, *,
    max_rows: int, retained_chunk_ids: tuple[str, ...] = (),
) -> RawFileProjection:
    """Capture a capped file candidate set and its raw registered-model points.

    The cap includes same-path candidates in other sources before filtering, and
    separately the union of current and retained IDs. Retained IDs expose known
    vector remnants after canonical deletion; they do not discover unknown
    orphans. All indices must already cover their physical tails. No preparation,
    content normalization, checkpoint publication or store write is performed.
    """
    from agent_index.indexing.embedding_coverage import embedding_profile_key

    if type(max_rows) is not int or max_rows < 1:
        raise ValueError("File projection requires a positive integer row budget")
    if any(not isinstance(value, str) or not value.strip() for value in (source, file_path)):
        raise ValueError("File projection requires an exact source and path")
    if (not isinstance(retained_chunk_ids, tuple)
            or len(retained_chunk_ids) > max_rows
            or any(not isinstance(cid, str) or not cid for cid in retained_chunk_ids)
            or len(set(retained_chunk_ids)) != len(retained_chunk_ids)):
        raise ValueError("Retained chunk IDs must be unique and fit the row budget")
    domain = store._source_domain
    content = store.content_store
    if (domain is None or Path(store._db_path).resolve() != config.lance_dir.resolve()
            or content._table_name != config.content_table
            or set(store.model_ids) != set(config.model_profiles)
            or not config.model_profiles):
        raise SourceLayoutError("File projection requires the configured participating store")
    with domain.operation(content.db):
        models = []
        tables = {config.content_table}
        for name, profile in sorted(config.model_profiles.items()):
            owner = store.get_vector_table(name)
            if (name != profile.model_id or owner.table_name != profile.table_name
                    or owner.dim != profile.dim or owner._source_domain is not domain
                    or owner.table_name in tables):
                raise SourceLayoutError("File projection model mapping is incompatible")
            schema = _table(owner).schema
            if any(field.name not in schema.names or schema.field(field.name).type != field.type
                   for field in owner._schema):
                raise SourceLayoutError("File projection native model schema is incompatible")
            tables.add(owner.table_name)
            models.append((name, profile, owner))
        # Preflight every index before any canonical/vector point query.
        for indexed_owner, column in (
            [(content, "file_path"), (content, "chunk_id")]
            + [(owner, "chunk_id") for _, _, owner in models]
        ):
            state, _ = inspect_index(_table(indexed_owner), column=column)
            if not state.prepared:
                raise ScalarIndexPreparationRequired(f"File projection needs a prepared {column} index")
            require_budget(state, 0)
        candidates = (
            _table(content).search()
            .where(f"file_path = '{_sql_str(file_path)}'")
            .limit(max_rows + 1).to_list()
        )
        if len(candidates) > max_rows:
            raise ScalarIndexPreparationRequired("File projection path candidate budget exceeded")
        selected: dict[str, dict[str, Any]] = {}
        for row in candidates:
            if row["file_path"] != file_path:
                raise SourceLayoutError("File projection returned an unexpected path")
            if row["source"] != source:
                continue
            cid = row["chunk_id"]
            if not isinstance(cid, str) or not cid or cid in selected:
                raise SourceLayoutError("File projection has duplicate or invalid canonical IDs")
            selected[cid] = row
        ids = tuple(sorted(set(retained_chunk_ids) | selected.keys()))
        if len(ids) > max_rows:
            raise ScalarIndexPreparationRequired("File projection point union budget exceeded")
        canonical = indexed_chunk_rows(content, list(ids))
        for row in canonical:
            if selected.get(row["chunk_id"]) != row:
                raise SourceLayoutError("File projection ID belongs to different canonical input")
        if len(canonical) != len(selected):
            raise SourceLayoutError("File projection canonical points are incomplete")
        projections = []
        for name, profile, owner in models:
            rows = indexed_chunk_rows(owner, list(ids))
            for row in rows:
                cid = row["chunk_id"]
                vector = row["vector"]
                if (row["source"] != source or not isinstance(row["language"], str)
                        or (cid in selected and row["language"] != selected[cid]["language"])
                        or not isinstance(vector, list) or len(vector) != owner.dim
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in vector)):
                    raise SourceLayoutError("File projection vector or provenance is incompatible")
            projections.append(RawModelProjection(
                name, owner.table_name, owner.dim, tuple(embedding_profile_key(profile)),
                tuple(sorted(profile.content_types)), _raw_rows(rows),
            ))
        identity = domain.receipt().get("domain")
        if not isinstance(identity, str) or not identity:
            raise SourceLayoutError("File projection domain identity is missing")
        return RawFileProjection(
            identity, source, file_path, content._table_name, _raw_rows(list(selected.values())),
            ids, tuple(projections),
        )
