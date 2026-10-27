"""Per-content-item centroids and exact-duplicate identity for MultiModelStore."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import TYPE_CHECKING

import numpy as np

from .item_repr import ItemRepresentation, item_content_hash, pool_vectors
from .source_layout import source_read

if TYPE_CHECKING:
    from .content_store import ChunkRecord, ContentStore
    from .vector_table import VectorTable


class _ItemRepresentationMixin:
    """Build item views from canonical content and the registered model tables."""

    if TYPE_CHECKING:
        _content: ContentStore
        _vector_tables: dict[str, VectorTable]

    @source_read
    def item_representation(
        self, source: str, file_path: str, *, strict: bool = False,
    ) -> ItemRepresentation | None:
        """Build a content item's per-model centroids + exact-dup key.

        Groups the item's chunks, pools each model's chunk vectors (length-
        weighted by line span, L2-normalized) into a per-space centroid, and
        hashes the line-ordered chunk content hashes into an exact-duplicate
        key.  An item whose chunks span both the code and prose tables gets a
        centroid per space (``ItemRepresentation.is_mixed``); the spaces are
        never blended together.

        Returns None if the item has no indexed chunks.
        """
        records = self._content.get_chunks_for_file(source, file_path, strict=strict)
        if not records:
            return None
        return self._item_from_records(records, tuple(self._vector_tables), strict=strict)

    def _item_from_records(
        self, records: list[ChunkRecord], models: tuple[str, ...], *,
        strict: bool, bounded: bool = False,
    ) -> ItemRepresentation:
        source, file_path = records[0].source, records[0].file_path
        content_hash = item_content_hash([r.content_hash for r in records])

        centroids: dict[str, np.ndarray] = {}
        chunk_ids = [r.chunk_id for r in records]
        for model_id in models:
            vt = self._vector_tables[model_id]
            vec_map = (
                vt.get_vectors(chunk_ids, bounded=True) if bounded
                else vt.get_vectors(chunk_ids, strict=strict)
            )
            if not vec_map:
                continue
            # Preserve line order; weight each chunk by its line span so longer
            # chunks pull the centroid proportionally.
            vlist: list[np.ndarray] = []
            wlist: list[float] = []
            for r in records:
                v = vec_map.get(r.chunk_id)
                if v is None:
                    continue
                vlist.append(v)
                wlist.append(float(max(r.line_end - r.line_start + 1, 1)))
            if not vlist:
                continue
            centroids[model_id] = pool_vectors(
                np.vstack(vlist), np.asarray(wlist, dtype=np.float32),
            )

        return ItemRepresentation(
            source=source,
            file_path=file_path,
            centroids=centroids,
            content_hash=content_hash,
            chunk_count=len(records),
        )

    @source_read
    def iter_bucket_item_representations(
        self, bucket: str, *, model_ids: tuple[str, ...], row_budget: int,
    ) -> Iterator[ItemRepresentation]:
        """Read a prepared bucket and only selected model tables, without full scans."""
        from .source_bucket_index import bucket_records

        if (not isinstance(model_ids, tuple) or not model_ids
                or any(not isinstance(model, str) or model not in self._vector_tables
                       for model in model_ids)):
            raise ValueError("Bucket representations require registered model IDs")
        models = tuple(dict.fromkeys(model_ids))
        items: dict[tuple[str, str], list[ChunkRecord]] = defaultdict(list)
        for record in bucket_records(self._content, bucket, row_budget=row_budget):
            items[(record.source, record.file_path)].append(record)
        for key in sorted(items):
            records = sorted(items[key], key=lambda record: (record.line_start, record.line_end))
            rep = self._item_from_records(records, models, strict=True, bounded=True)
            if rep.centroids:
                yield rep

    @source_read
    def iter_item_representations(self, *, strict: bool = False) -> Iterator[ItemRepresentation]:
        """Yield an ``ItemRepresentation`` for every distinct content item.

        Drives the offline clustering pass.  Items with no pooled centroid
        (no vectors yet) are skipped.
        """
        for source, file_path in self._content.distinct_items(strict=strict):
            rep = self.item_representation(source, file_path, strict=strict)
            if rep is not None and rep.centroids:
                yield rep
