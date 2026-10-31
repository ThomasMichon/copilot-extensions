"""Vector table — per-model embedding vectors keyed by chunk_id.

Each embedding model gets its own ``VectorTable`` instance with a
distinct table name (e.g. ``vectors_code``, ``vectors_prose``).
Vectors are joined back to chunk content via ``chunk_id`` in the
``ContentStore``.
"""

from __future__ import annotations

import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa

from .source_layout import SourceLayoutError, source_read, source_write

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

    def __init__(self, db, *, table_name: str, dim: int = 768,
                 source_domain=None, content_table: str = "chunks") -> None:
        self._db = db
        self._table_name = table_name
        self._dim = dim
        self._table = None
        self._source_domain = source_domain
        self._content_table = content_table
        self._schema = pa.schema([
            pa.field("chunk_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("indexed_at", pa.float64()),
        ])
        if source_domain is not None:
            self._schema = self._schema.append(pa.field("source", pa.string()))
            self._schema = self._schema.append(pa.field("language", pa.string()))

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def table_name(self) -> str:
        return self._table_name

    def _get_or_create_table(self):
        if self._table is not None:
            if self._source_domain is not None:
                self._table.checkout_latest()
            return self._table
        try:
            self._table = self._db.open_table(self._table_name)
        except Exception:
            if self._source_domain is not None and not self._source_domain.writing:
                raise SourceLayoutError("source-aware vector table is not prepared") from None
            self._table = self._db.create_table(
                self._table_name, schema=self._schema
            )
            logger.info("Created vector table: %s (dim=%d)", self._table_name, self._dim)
        return self._table

    # -- write ---------------------------------------------------------------

    def upsert(
        self, chunks: list[Chunk], vectors: np.ndarray, *,
        bounded_provenance: bool = False, bounded_input: bool = False,
    ) -> int:
        """Insert or update vectors by chunk_id.

        Args:
            chunks: Chunk objects; selected bounded modes validate canonical fields.
            vectors: Embedding vectors, shape ``(n, dim)``, float32.
            bounded_input: Also verify raw canonical input under writer exclusion.

        Returns:
            Number of rows written.
        """
        if not (bounded_provenance or bounded_input):
            return self._upsert(chunks, vectors)
        from .chunk_id_index import ChunkIdIndexPreparationRequired
        from .content_store import ContentStore
        from .source_projection import projection_records

        domain = self._source_domain
        if domain is None:
            raise ChunkIdIndexPreparationRequired(
                "Bounded vector provenance requires a participating source store",
            )
        self._validate(chunks, vectors)
        if not chunks:
            return 0
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("Bounded vector provenance requires unique chunk IDs")
        content = ContentStore(domain.path, table_name=self._content_table)
        with domain.read(self._db):
            projection_records(
                self._db, self._content_table, chunks, bounded_content=content,
                require_current_input=bounded_input,
            )
        refusal: ChunkIdIndexPreparationRequired | SourceLayoutError
        with domain.write(self._db):
            try:
                canonical = projection_records(
                    self._db, self._content_table, chunks, bounded_content=content,
                    require_current_input=bounded_input,
                )
            except (ChunkIdIndexPreparationRequired, SourceLayoutError) as exc:
                refusal = exc
            else:
                return self._write_vectors(chunks, vectors, canonical=canonical)
        raise refusal

    @source_write
    def _upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> int:
        self._validate(chunks, vectors)
        return self._write_vectors(chunks, vectors)

    def _write_vectors(
        self, chunks: list[Chunk], vectors: np.ndarray,
        *, canonical: dict[str, dict[str, object]] | None = None,
    ) -> int:
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
        if self._source_domain is not None:
            from .source_projection import projection_records

            if canonical is None:
                canonical = projection_records(self._db, self._content_table, chunks)
            for record in records:
                provenance = canonical[record["chunk_id"]]
                record.update(source=provenance["source"], language=provenance["language"])

        chunk_ids = [c.chunk_id for c in chunks]
        mutation = (
            self._source_domain.bucket_mutation(
                self._table_name, f"vector-upsert:{self._table_name}",
                (record["source"] for record in records),
            ) if self._source_domain is not None else nullcontext()
        )
        with mutation:
            try:
                id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
                table.delete(f"chunk_id IN ({id_list})")
            except Exception:
                if self._source_domain is not None:
                    raise
                pass

            table.add(records)
        logger.info("Upserted %d vectors to %s", len(records), self._table_name)
        return len(records)

    @source_write
    def delete_by_chunk_ids(self, chunk_ids: list[str]) -> None:
        """Remove vectors for specific chunk IDs."""
        if not chunk_ids:
            return
        table = self._get_or_create_table()
        if self._source_domain is not None:
            self._source_domain.effect(f"vector-delete:{self._table_name}")
        id_list = ", ".join(f"'{cid}'" for cid in chunk_ids)
        table.delete(f"chunk_id IN ({id_list})")

    @source_write
    def delete_stale(self, *, before: float) -> int:
        """Remove vectors not refreshed after *before* timestamp."""
        table = self._get_or_create_table()
        if self._source_domain is not None:
            self._source_domain.effect(f"vector-stale:{self._table_name}")
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
            if self._source_domain is not None:
                raise
            return 0

    # -- search --------------------------------------------------------------

    @source_read
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

    @source_read
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

    @source_read
    def get_vectors(
        self, chunk_ids: list[str], *, strict: bool = False, bounded: bool = False,
    ) -> dict[str, np.ndarray]:
        """Batch-read stored embeddings for *chunk_ids* present in this table.

        Returns ``{chunk_id: vector}`` for the ids that exist here; ids absent
        from this table (e.g. chunks routed to the other model) are simply
        omitted, which is how item pooling stays inside one embedding space.
        """
        if not chunk_ids:
            return {}
        strict = strict or bounded
        if bounded:
            from .chunk_id_index import indexed_chunk_rows

            rows = indexed_chunk_rows(self, chunk_ids, columns=["chunk_id", "vector"])
        else:
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
                if strict:
                    raise
                logger.warning("Vector batch read failed", exc_info=True)
                return {}
        out: dict[str, np.ndarray] = {}
        for r in rows:
            vec = r.get("vector")
            if vec is None:
                if strict:
                    raise RuntimeError("Stored vector is missing")
                continue
            arr = np.asarray(vec, dtype=np.float32)
            if strict and (arr.shape != (self._dim,) or not np.isfinite(arr).all()):
                raise RuntimeError("Stored vector has invalid dimensions or values")
            if arr.shape == (self._dim,):
                out[r["chunk_id"]] = arr
        return out

    # -- maintenance ---------------------------------------------------------

    @source_write
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
            maintenance = (
                self._source_domain.physical_maintenance(
                    self._table_name, f"vector-compact:{self._table_name}",
                ) if self._source_domain is not None else nullcontext()
            )
            with maintenance:
                table.optimize(cleanup_older_than=cutoff)
            logger.info(
                "Optimized table %s (cleanup older than %d days)",
                self._table_name, older_than_days,
            )
        except Exception:
            if self._source_domain is not None:
                raise
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

    @source_read
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
