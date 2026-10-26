"""Canonical writer projection maintenance for a selected cold-only domain."""

from __future__ import annotations

from collections import defaultdict

from .repo_filter import _sql_str
from .source_layout import SourceLayoutError

PAGE_SIZE = 512


def check_content_sources(content, chunks) -> None:
    """Validate immutable canonical identities through the selected strict lookup."""
    old = content.get_by_ids([chunk.chunk_id for chunk in chunks])
    if any(chunk.chunk_id in old and old[chunk.chunk_id].source != chunk.source for chunk in chunks):
        raise SourceLayoutError("existing ID has conflicting canonical source")


def delete_canonical(content, predicate: str, *, return_ids: bool = False):
    table = content._get_or_create_table()
    content._source_domain.effect(f"canonical-delete:{content._table_name}")
    vectors = [
        content._db.open_table(name) for name in content._db.list_tables().tables
        if "vector" in content._db.open_table(name).schema.names
    ]
    reader = (
        table.search().where(predicate, prefilter=True).select(["chunk_id"])
        .limit(None).to_batches(batch_size=PAGE_SIZE)
    )
    total, selected = 0, []
    try:
        for batch in reader:
            ids = batch.column("chunk_id").to_pylist()
            total += len(ids)
            if return_ids:
                selected.extend(ids)
            sql = ", ".join(f"'{_sql_str(cid)}'" for cid in ids)
            for vector in vectors:
                vector.delete(f"chunk_id IN ({sql})")
    finally:
        reader.close()
    table.delete(predicate)
    return selected if return_ids else total


def sync_content_projection(db, chunks) -> None:
    groups = defaultdict(list)
    for chunk in chunks:
        groups[(chunk.source, chunk.language)].append(chunk.chunk_id)
    for name in db.list_tables().tables:
        table = db.open_table(name)
        if "vector" not in table.schema.names:
            continue
        if not {"source", "language"} <= set(table.schema.names):
            raise SourceLayoutError("incomplete source projection")
        for (source, language), ids in groups.items():
            for start in range(0, len(ids), PAGE_SIZE):
                selected = ", ".join(f"'{_sql_str(cid)}'" for cid in ids[start:start + PAGE_SIZE])
                changed = (
                    f"chunk_id IN ({selected}) AND "
                    f"(source IS NULL OR source != '{_sql_str(source)}' OR "
                    f"language IS NULL OR language != '{_sql_str(language)}')"
                )
                if not table.search().where(changed).select(["chunk_id"]).limit(1).to_list():
                    continue
                table.update(
                    where=changed,
                    values={"source": source, "language": language},
                )


def projection_records(db, content_name, chunks) -> dict:
    table = db.open_table(content_name)
    result = {}
    for start in range(0, len(chunks), PAGE_SIZE):
        ids = ", ".join(
            f"'{_sql_str(chunk.chunk_id)}'" for chunk in chunks[start:start + PAGE_SIZE]
        )
        rows = (
            table.search().where(f"chunk_id IN ({ids})", prefilter=True)
            .select(["chunk_id", "source", "language"]).limit(PAGE_SIZE + 1).to_list()
        )
        for row in rows:
            if row["chunk_id"] in result and result[row["chunk_id"]] != row:
                raise SourceLayoutError("conflicting canonical provenance")
            result[row["chunk_id"]] = row
    for chunk in chunks:
        row = result.get(chunk.chunk_id)
        if row is None or row["source"] != chunk.source or row["language"] != chunk.language:
            raise SourceLayoutError("missing or mismatched canonical provenance")
    return result
