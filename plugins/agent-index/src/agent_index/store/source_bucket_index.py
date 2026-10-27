"""Prepared canonical bucket reads, never a filtered corpus scan in disguise."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .clustering import source_bucket
from .content_snapshot import _records
from .repo_filter import _sql_str
from .scalar_index import (
    ScalarIndexPreparationRequired,
    ScalarIndexState,
    inspect_index,
    prepare_index,
    require_budget,
)

if TYPE_CHECKING:
    from .content_store import ChunkRecord, ContentStore

BATCH_SIZE = 200


def bucket_predicate(bucket: str) -> str:
    if (not isinstance(bucket, str) or not bucket or len(bucket.encode()) > 256
            or any(ord(char) < 32 for char in bucket) or source_bucket(bucket) != bucket):
        raise ValueError("Invalid clustering bucket")
    exact = f"source = '{_sql_str(bucket)}'"
    if ":" not in bucket:
        return exact
    # ':' and ';' are adjacent code points: this is a literal, indexable prefix range.
    return (
        f"({exact} OR (source >= '{_sql_str(bucket + ':')}' "
        f"AND source < '{_sql_str(bucket + ';')}'))"
    )


def prepare_source_index(owner: ContentStore, *, max_rows: int) -> ScalarIndexState:
    """Explicit initial/tail B-tree preparation; no reader prepares its own index."""
    return prepare_index(owner, column="source", max_rows=max_rows)


def bucket_records(owner: ContentStore, bucket: str, *, row_budget: int) -> list[ChunkRecord]:
    """Read one complete bucket within a canonical-row budget under existing exclusion."""
    predicate = bucket_predicate(bucket)
    if type(row_budget) is not int or row_budget < 0:
        raise ValueError("Bucket row budget must be a nonnegative integer")
    domain = owner._source_domain
    if domain is None:
        raise ScalarIndexPreparationRequired("Bucket reads require a participating source store")
    with domain.read(owner._db):
        table = owner._get_or_create_table()
        state, _ = inspect_index(table, column="source")
        if not state.prepared:
            raise ScalarIndexPreparationRequired("Source index is missing; explicit preparation required")
        require_budget(state, 0)
        count = table.count_rows(predicate)
        if count > row_budget:
            raise ScalarIndexPreparationRequired(
                f"Bucket needs {count} canonical rows; read budget is {row_budget}"
            )
        if not count:
            return []
        reader = table.search().where(predicate).limit(count + 1).to_batches(batch_size=BATCH_SIZE)
        result: list[ChunkRecord] = []
        seen: set[str] = set()
        try:
            for batch in reader:
                records = _records(batch.to_pylist())
                for record in records:
                    if source_bucket(record.source) != bucket or record.chunk_id in seen:
                        raise RuntimeError("Bucket read returned duplicate or unexpected canonical rows")
                    seen.add(record.chunk_id)
                result.extend(records)
                if len(result) > count:
                    raise RuntimeError("Bucket read exceeded the complete canonical row count")
        finally:
            reader.close()
        if len(result) != count:
            raise RuntimeError("Incomplete canonical bucket read")
        return result
