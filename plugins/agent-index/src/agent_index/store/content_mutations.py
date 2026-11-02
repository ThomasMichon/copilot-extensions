"""Strict canonical comparisons and identity-scoped file reconciliation."""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from .repo_filter import _sql_str

if TYPE_CHECKING:
    from agent_index.chunking.base import Chunk

    from .multi_model_store import MultiModelStore

BATCH_SIZE = 200


def content_record(chunk: Chunk) -> dict[str, Any]:
    """Canonical persisted fields, excluding the physical write timestamp."""
    return {
        "chunk_id": chunk.chunk_id,
        "source": chunk.source,
        "file_path": chunk.file_path,
        "chunk_type": chunk.chunk_type,
        "language": chunk.language,
        "content": chunk.content,
        "content_hash": chunk.content_hash,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "metadata": json.dumps(chunk.metadata, sort_keys=True) if chunk.metadata else "",
    }


def coalesce_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Coalesce identical canonical IDs, rejecting conflicting persisted fields."""
    unique: dict[str, Chunk] = {}
    for chunk in chunks:
        previous = unique.get(chunk.chunk_id)
        if previous is not None and content_record(previous) != content_record(chunk):
            raise ValueError("Conflicting canonical chunks share an ID")
        unique.setdefault(chunk.chunk_id, chunk)
    return list(unique.values())


def changed_chunks(table: Any, chunks: list[Chunk]) -> list[Chunk]:
    """Compare bounded raw rows; failed or incomplete reads never imply absence."""
    chunks = coalesce_chunks(chunks)
    changed: list[Chunk] = []
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]
        wanted = {chunk.chunk_id: content_record(chunk) for chunk in batch}
        ids = ", ".join(f"'{_sql_str(cid)}'" for cid in wanted)
        predicate = f"chunk_id IN ({ids})"
        count = table.count_rows(predicate)
        reader = (
            table.search().where(predicate).select(list(next(iter(wanted.values()))))
            .limit(max(count, 1)).to_batches(batch_size=BATCH_SIZE)
        )
        stored: dict[str, dict[str, Any]] = {}
        copies: Counter[str] = Counter()
        try:
            for rows in reader:
                for row in rows.to_pylist():
                    cid = row["chunk_id"]
                    if cid not in wanted:
                        raise RuntimeError("Canonical chunk lookup returned an unexpected ID")
                    if cid in stored and stored[cid] != row:
                        raise RuntimeError("Conflicting stored canonical chunks share an ID")
                    stored[cid] = row
                    copies[cid] += 1
        finally:
            reader.close()
        if sum(copies.values()) != count:
            raise RuntimeError("Incomplete canonical chunk lookup")
        changed.extend(
            chunk for chunk in batch
            if stored.get(chunk.chunk_id) != wanted[chunk.chunk_id] or copies[chunk.chunk_id] > 1
        )
    return changed


def reconcile_file_chunks(
    store: MultiModelStore, source: str, file_path: str, keep_ids: set[str],
) -> int:
    """Delete only obsolete file IDs, keeping canonical rows until vectors succeed."""
    content = store.content_store
    stale = sorted(set(content.get_chunk_ids_by_file(source, file_path)) - keep_ids)
    if not stale:
        return 0
    scope = f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}'"
    table = content._get_or_create_table()
    for start in range(0, len(stale), BATCH_SIZE):
        batch = stale[start:start + BATCH_SIZE]
        ids = ", ".join(f"'{_sql_str(cid)}'" for cid in batch)
        predicate = f"{scope} AND chunk_id IN ({ids})"
        if store._source_domain is not None:
            from .source_projection import delete_canonical

            delete_canonical(content, predicate)
        else:
            for model in store.model_ids:
                store.get_vector_table(model).delete_by_chunk_ids(batch)
            table.delete(predicate)
    return len(stale)
