"""Selected canonical effects under the existing point-domain writer."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .chunk_id_index import indexed_chunk_rows
from .content_mutations import coalesce_chunks, content_record
from .repo_filter import _sql_str
from .scalar_index import (
    MAX_SEGMENTS, ScalarIndexPreparationRequired, ScalarIndexState, inspect_index, require_budget,
)
from .source_layout import SourceLayoutError
from .source_projection import sync_content_projection

if TYPE_CHECKING:
    from agent_index.chunking.base import Chunk

    from .content_store import ContentStore
    from .source_layout import PointScope
    from .vector_table import VectorTable


def _prepared_tables(content: ContentStore, *, point_scope: PointScope) -> tuple[ContentStore, list[VectorTable]]:
    from .component_ledger import BINDING, TABLE, qualified_component_table
    from .content_store import ContentStore, _CONTENT_SCHEMA
    from .vector_table import VectorTable

    domain = content._source_domain
    assert domain is not None
    db = content.db
    for connection in (db, getattr(domain._local, "db", None)):
        uri = getattr(connection, "uri", None)
        if not isinstance(uri, (str, Path)) or Path(uri).resolve() != domain.path.resolve():
            raise SourceLayoutError("Point content connection differs from its owning source writer")
    receipt = domain.receipt()
    binding = receipt.get("point_models")
    if binding is None or binding["content_table"] != content._table_name:
        raise SourceLayoutError("Point content differs from its retained model binding")
    names = {content._table_name, *(model["table_name"] for model in binding["models"].values())}
    actual = set(db.list_tables().tables)
    auxiliary = actual - names
    if actual != set(receipt["versions"]) or not names <= actual or auxiliary - {TABLE}:
        raise SourceLayoutError("Point content requires exactly its prepared bound tables")
    if auxiliary or BINDING in receipt:
        qualified_component_table(domain, db)
    table = db.open_table(content._table_name)
    if not table.schema.equals(_CONTENT_SCHEMA):
        raise SourceLayoutError("Point content schema is incompatible")
    canonical = ContentStore(domain.path, table_name=content._table_name, point_scope=point_scope)
    canonical._table = table
    vectors: list[VectorTable] = []
    for model_id, model in binding["models"].items():
        owner = VectorTable(
            db, table_name=model["table_name"], dim=model["dim"], source_domain=domain,
            content_table=content._table_name, model_id=model_id,
        )
        vector: Any = db.open_table(owner.table_name)
        owner._table = vector
        if not vector.schema.equals(owner._schema):
            raise SourceLayoutError("Point content model schema is incompatible")
        vectors.append(owner)
    return canonical, vectors


def upsert_point_content(
    content: ContentStore, chunks: list[Chunk], *, point_scope: PointScope | None,
) -> int:
    """Write changed raw content and maintain selected vector provenance only."""
    domain = content._source_domain
    if domain is None or point_scope is None or not domain.writing:
        raise SourceLayoutError("Point content mutation requires the owning source writer and scope")
    with domain._write(content.db, point_scope=point_scope):
        if any(chunk.source != point_scope[1] for chunk in chunks):
            raise SourceLayoutError("Content input is outside its selected family")
        chunks = coalesce_chunks(chunks)
        canonical, vectors = _prepared_tables(content, point_scope=point_scope)
        table: Any = canonical._table
        states: dict[str, list[ScalarIndexState]] = {}
        owners: list[ContentStore | VectorTable] = [canonical, *vectors]
        for owner in owners:
            states[owner._table_name] = []
            columns = ["chunk_id", "file_path", "source"] if owner is canonical else ["chunk_id"]
            for column in columns:
                native: Any = owner._table
                state, _ = inspect_index(native, column=column)
                if column == "source" and not state.prepared:
                    continue
                if not state.prepared:
                    raise ScalarIndexPreparationRequired("Point content requires prepared ingestion indices")
                require_budget(state, 0)
                states[owner._table_name].append(state)
        rows = indexed_chunk_rows(
            canonical, [chunk.chunk_id for chunk in chunks],
            columns=list(content_record(chunks[0])) if chunks else None, point_scope=point_scope,
        )
        old = {row["chunk_id"]: row for row in rows}
        if any(chunk.chunk_id in old and old[chunk.chunk_id]["source"] != chunk.source for chunk in chunks):
            raise SourceLayoutError("existing ID has conflicting canonical source")
        changed = [chunk for chunk in chunks if old.get(chunk.chunk_id) != content_record(chunk)]
        affected = {content._table_name} if changed else set()
        wanted = {chunk.chunk_id: (chunk.source, chunk.language) for chunk in chunks}
        for owner in vectors:
            vector_rows = indexed_chunk_rows(
                owner, [chunk.chunk_id for chunk in chunks],
                columns=["chunk_id", "source", "language"], point_scope=point_scope,
            )
            if any((row["source"], row["language"]) != wanted[row["chunk_id"]] for row in vector_rows):
                affected.add(owner._table_name)
        if any(state.segments >= MAX_SEGMENTS for name in affected for state in states[name]):
            raise ScalarIndexPreparationRequired("Point content needs a reserved ingestion index segment")
        if changed:
            records = [{**content_record(chunk), "indexed_at": time.time()} for chunk in changed]
            ids = ", ".join(f"'{_sql_str(chunk.chunk_id)}'" for chunk in changed)
            with domain.bucket_mutation(
                content._table_name, f"content-upsert:{content._table_name}",
                (chunk.source for chunk in changed),
            ):
                table.delete(f"chunk_id IN ({ids})")
                table.add(records)
        sync_content_projection(
            content.db, chunks, source_domain=domain, tables=[owner._table for owner in vectors],
        )
        return len(changed)
