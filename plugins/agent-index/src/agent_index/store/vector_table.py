"""Vector table — per-model embedding vectors keyed by chunk_id.

Each embedding model gets its own ``VectorTable`` instance with a
distinct table name (e.g. ``vectors_code``, ``vectors_prose``).
Vectors are joined back to chunk content via ``chunk_id`` in the
``ContentStore``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa

if TYPE_CHECKING:
    from agent_index.chunking.base import Chunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorHit:
    """A vector search hit — chunk_id + score, no content."""

    chunk_id: str
    score: float


class VectorTable:
    """LanceDB table holding embedding vectors for one model.

    Args:
        db: LanceDB connection (shared with ContentStore).
        table_name: Name of the vector table (e.g. "vectors_code").
        dim: Embedding dimension for this model.
    """

    def __init__(self, db, *, table_name: str, dim: int = 768) -> None:
        self._db = db
        self._table_name = table_name
        self._dim = dim
        self._table = None
        self._schema = pa.schema([
            pa.field("chunk_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("indexed_at", pa.float64()),
        ])

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def table_name(self) -> str:
        return self._table_name

    def _get_or_create_table(self):
        if self._table is not None:
            return self._table
        try:
            self._table = self._db.open_table(self._table_name)
        except Exception:
            self._table = self._db.create_table(
                self._table_name, schema=self._schema
            )
            logger.info("Created vector table: %s (dim=%d)", self._table_name, self._dim)
        return self._table

    # -- write ---------------------------------------------------------------

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> int:
        """Insert or update vectors by chunk_id.

        Args:
            chunks: Chunk objects (only chunk_id is used).
            vectors: Embedding vectors, shape ``(n, dim)``, float32.

        Returns:
            Number of rows written.
        """
        self._validate(chunks, vectors)
        table = self._get_or_create_table()
        now = time.time()

        records = [
            {
                "chunk_id": chunk.chunk_id,
                "vector": vec.tolist(),
                "indexed_at": now,
            }
            for chunk, vec in zip(chunks, vectors, strict=True)
        ]

        chunk_ids = [c.chunk_id for c in chunks]
        try:
            id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
            table.delete(f"chunk_id IN ({id_list})")
        except Exception:
            pass

        table.add(records)
        logger.info("Upserted %d vectors to %s", len(records), self._table_name)
        return len(records)

    def delete_by_chunk_ids(self, chunk_ids: list[str]) -> None:
        """Remove vectors for specific chunk IDs."""
        if not chunk_ids:
            return
        table = self._get_or_create_table()
        try:
            id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
            table.delete(f"chunk_id IN ({id_list})")
        except Exception:
            pass

    def delete_stale(self, *, before: float) -> int:
        """Remove vectors not refreshed after *before* timestamp."""
        table = self._get_or_create_table()
        try:
            initial = table.count_rows()
            table.delete(f"indexed_at < {before}")
            remaining = table.count_rows()
            removed = initial - remaining
            if removed > 0:
                logger.info(
                    "Deleted %d stale vectors from %s",
                    removed, self._table_name,
                )
            return removed
        except Exception:
            return 0

    # -- search --------------------------------------------------------------

    def search(
        self,
        vector: np.ndarray,
        *,
        limit: int = 10,
        chunk_ids: list[str] | None = None,
    ) -> list[VectorHit]:
        """Cosine-similarity search. Returns (chunk_id, score) hits.

        Args:
            vector: Query embedding, shape ``(dim,)``, float32.
            limit: Maximum results.
            chunk_ids: Optional filter to restrict search to specific IDs.
        """
        if vector.shape != (self._dim,):
            raise ValueError(
                f"Expected shape ({self._dim},), got {vector.shape}"
            )
        if not np.isfinite(vector).all():
            raise ValueError("Query vector contains NaN or Inf values")

        table = self._get_or_create_table()
        query = table.search(vector.tolist()).metric("cosine").limit(limit)

        if chunk_ids:
            id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
            query = query.where(f"chunk_id IN ({id_list})")

        try:
            results = query.to_list()
        except Exception:
            return []

        return [
            VectorHit(
                chunk_id=r["chunk_id"],
                score=1.0 - r.get("_distance", 0.0),
            )
            for r in results
        ]

    def get_vector(self, chunk_id: str) -> np.ndarray | None:
        """Return the stored embedding for *chunk_id*, or None if absent.

        Used by similarity ("find similar") flows that compare an already
        indexed chunk against the rest of this table without re-embedding.
        """
        table = self._get_or_create_table()
        try:
            rows = (
                table.search()
                .where(f"chunk_id = '{chunk_id}'")
                .select(["chunk_id", "vector"])
                .limit(1)
                .to_list()
            )
        except Exception:
            return None
        if not rows:
            return None
        vec = rows[0].get("vector")
        if vec is None:
            return None
        arr = np.asarray(vec, dtype=np.float32)
        if arr.shape != (self._dim,):
            return None
        return arr

    def get_vectors(self, chunk_ids: list[str]) -> dict[str, np.ndarray]:
        """Batch-read stored embeddings for *chunk_ids* present in this table.

        Returns ``{chunk_id: vector}`` for the ids that exist here; ids absent
        from this table (e.g. chunks routed to the other model) are simply
        omitted, which is how item pooling stays inside one embedding space.
        """
        if not chunk_ids:
            return {}
        table = self._get_or_create_table()
        id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
        try:
            rows = (
                table.search()
                .where(f"chunk_id IN ({id_list})")
                .select(["chunk_id", "vector"])
                .limit(len(chunk_ids))
                .to_list()
            )
        except Exception:
            return {}
        out: dict[str, np.ndarray] = {}
        for r in rows:
            vec = r.get("vector")
            if vec is None:
                continue
            arr = np.asarray(vec, dtype=np.float32)
            if arr.shape == (self._dim,):
                out[r["chunk_id"]] = arr
        return out

    # -- maintenance ---------------------------------------------------------

    def compact(self, *, older_than_days: int = 2) -> dict[str, int]:
        """Compact fragments and clean up old versions.

        Uses ``table.optimize()`` which merges small data fragments
        into larger ones, prunes old versions, and optimizes indices.

        Returns:
            Dict with keys: fragments_before, fragments_after.
        """
        import datetime

        table = self._get_or_create_table()
        stats: dict[str, int] = {
            "fragments_before": 0,
            "fragments_after": 0,
        }

        try:
            lance_dataset = table.to_lance()
            stats["fragments_before"] = len(lance_dataset.get_fragments())
        except Exception:
            pass

        try:
            cutoff = datetime.timedelta(days=older_than_days)
            table.optimize(cleanup_older_than=cutoff)
            logger.info(
                "Optimized table %s (cleanup older than %d days)",
                self._table_name, older_than_days,
            )
        except Exception:
            logger.warning(
                "optimize failed for %s", self._table_name, exc_info=True,
            )

        try:
            lance_dataset = table.to_lance()
            stats["fragments_after"] = len(lance_dataset.get_fragments())
        except Exception:
            pass

        return stats

    # -- stats ---------------------------------------------------------------

    def count(self) -> int:
        """Number of vectors in this table."""
        table = self._get_or_create_table()
        try:
            return table.count_rows()
        except Exception:
            return 0

    # -- validation ----------------------------------------------------------

    def _validate(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if len(chunks) != vectors.shape[0]:
            raise ValueError(
                f"Chunk count ({len(chunks)}) != vector rows ({vectors.shape[0]})"
            )
        if vectors.ndim != 2 or vectors.shape[1] != self._dim:
            raise ValueError(
                f"Expected vectors shape (n, {self._dim}), got {vectors.shape}"
            )
        if vectors.dtype != np.float32:
            raise ValueError(f"Expected float32 vectors, got {vectors.dtype}")
        if not np.isfinite(vectors).all():
            raise ValueError("Vectors contain NaN or Inf values")
