"""Per-content-item centroids and exact-duplicate identity for MultiModelStore."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np

from .item_repr import ItemRepresentation, item_content_hash, pool_vectors
from .source_layout import source_read


class _ItemRepresentationMixin:
    """Build item views from canonical content and the registered model tables."""

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

        content_hash = item_content_hash([r.content_hash for r in records])

        centroids: dict[str, np.ndarray] = {}
        chunk_ids = [r.chunk_id for r in records]
        for model_id, vt in self._vector_tables.items():
            vec_map = vt.get_vectors(chunk_ids, strict=strict)
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
    def iter_item_representations(self, *, strict: bool = False) -> Iterator[ItemRepresentation]:
        """Yield an ``ItemRepresentation`` for every distinct content item.

        Drives the offline clustering pass.  Items with no pooled centroid
        (no vectors yet) are skipped.
        """
        for source, file_path in self._content.distinct_items(strict=strict):
            rep = self.item_representation(source, file_path, strict=strict)
            if rep is not None and rep.centroids:
                yield rep
