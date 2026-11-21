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
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from .source_layout import PointScope, SourceDomain, SourceLayoutError, source_read, source_write

if TYPE_CHECKING:
    from agent_index.chunking.base import Chunk

logger = logging.getLogger(__name__)
pa = import_module("pyarrow")


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

    def __init__(self, db: Any, *, table_name: str, dim: int = 768,
                 source_domain: SourceDomain | None = None, content_table: str = "chunks",
                 model_id: str | None = None) -> None:
        self._db = db
        self._table_name = table_name
        self._dim = dim
        self._table = None
        self._source_domain = source_domain
        self._content_table = content_table
        self._model_id = model_id
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

    def _get_or_create_table(self) -> Any:
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
        self, chunks: list[Chunk], vectors: np.ndarray[Any, Any], *,
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
        from .scalar_index import ScalarIndexPreparationRequired as ChunkIdIndexPreparationRequired
        from .content_store import ContentStore
        from .source_projection import projection_records

        domain = self._source_domain
        if domain is None:
            raise ChunkIdIndexPreparationRequired(
                "Bounded vector provenance requires a participating source store",
            )
        self._validate(chunks, vectors)
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("Bounded vector provenance requires unique chunk IDs")
        content = ContentStore(domain.path, table_name=self._content_table)
        with domain.read(self._db):
            if not chunks:
                return 0
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

    def preflight_point_upsert(
        self, chunks: list[Chunk], vectors: np.ndarray[Any, Any], *,
        point_scope: PointScope | None,
    ) -> dict[str, dict[str, Any]]:
        """Read current canonical input only; never authorize or execute a point write."""
        canonical, _ = self._point_upsert_input(chunks, vectors, point_scope=point_scope)
        return canonical

    def upsert_point(
        self, chunks: list[Chunk], vectors: np.ndarray[Any, Any], *,
        point_scope: PointScope | None,
    ) -> int:
        """Write selected vectors inside an already-owned, exact point writer.

        This native effect does not retain applied-version or model coverage,
        maintain indices, publish checkpoints, or complete acquired work.
        """
        domain = self._source_domain
        if domain is None or point_scope is None or not domain.writing:
            raise SourceLayoutError("Point vector mutation requires the owning source writer and scope")
        with domain._write(self._db, point_scope=point_scope):
            binding = domain.receipt().get("point_models")
            if (binding is None or self._model_id is None or type(self._dim) is not int
                    or binding["content_table"] != self._content_table
                    or binding["models"].get(self._model_id) != {
                        "table_name": self._table_name, "dim": self._dim,
                    }):
                raise SourceLayoutError("Point vector mutation differs from its retained model binding")
            canonical, table = self._point_upsert_input(chunks, vectors, point_scope=point_scope)
            if not chunks:
                return 0
            return self._write_vectors(chunks, vectors, canonical=canonical, table=table)

    def _point_upsert_input(
        self, chunks: list[Chunk], vectors: np.ndarray[Any, Any], *,
        point_scope: PointScope | None,
    ) -> tuple[dict[str, dict[str, Any]], Any]:
        from .content_store import ContentStore
        from .source_projection import projection_records

        domain = self._source_domain
        if point_scope is None or domain is None or not domain.writing:
            raise SourceLayoutError("Point vector preflight requires the owning source writer and scope")
        with domain.read(self._db, point_scope=point_scope):
            if any(chunk.source != point_scope[1] for chunk in chunks):
                raise SourceLayoutError("Vector input is outside its selected family")
            self._validate(chunks, vectors)
            if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
                raise ValueError("Point vector preflight requires unique chunk IDs")
            canonical_table, vector_table = self._require_point_binding(reserve_segment=bool(chunks))
            content = ContentStore(
                domain.path, table_name=self._content_table, point_scope=point_scope,
            )
            content._table = canonical_table
            canonical = projection_records(
                self._db, self._content_table, chunks, bounded_content=content,
                require_current_input=True, point_scope=point_scope,
            )
            return canonical, vector_table

    def _require_point_binding(self, *, reserve_segment: bool) -> tuple[Any, Any]:
        from .content_store import _CONTENT_SCHEMA
        from .scalar_index import MAX_SEGMENTS, ScalarIndexPreparationRequired, inspect_index, require_budget

        domain = self._source_domain
        assert domain is not None
        for db in (self._db, getattr(domain._local, "db", None)):
            uri = getattr(db, "uri", None)
            if not isinstance(uri, (str, Path)) or Path(uri).resolve() != domain.path.resolve():
                raise SourceLayoutError("Point vector connection differs from its owning source writer")
        names = {self._table_name, self._content_table}
        if (len(names) != 2 or not names <= set(self._db.list_tables().tables)
                or not names <= domain.receipt()["versions"].keys()):
            raise SourceLayoutError("Point vectors require distinct prepared content and vector tables")
        canonical = self._db.open_table(self._content_table)
        vector = self._db.open_table(self._table_name)
        if (not canonical.schema.equals(_CONTENT_SCHEMA)
                or not vector.schema.equals(self._schema)):
            raise SourceLayoutError("Point vector content or model schema is incompatible")
        state, _ = inspect_index(vector, column="chunk_id")
        if not state.prepared:
            raise ScalarIndexPreparationRequired("Point vectors require a prepared chunk-ID index")
        require_budget(state, 0)
        if reserve_segment and state.segments >= MAX_SEGMENTS:
            raise ScalarIndexPreparationRequired("Point vector preflight needs a reserved index segment")
        return canonical, vector

    @source_write
    def _upsert(self, chunks: list[Chunk], vectors: np.ndarray[Any, Any]) -> int:
        self._validate(chunks, vectors)
        return self._write_vectors(chunks, vectors)

    def _write_vectors(
        self, chunks: list[Chunk], vectors: np.ndarray[Any, Any],
        *, canonical: dict[str, dict[str, object]] | None = None, table: Any = None,
    ) -> int:
        if table is None:
            table = self._get_or_create_table()
        now = time.time()

        records: list[dict[str, Any]] = [
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
            return cast(int, removed)
        except Exception:
            if self._source_domain is not None:
                raise
            return 0

    # -- search --------------------------------------------------------------

    @source_read
    def search(
        self,
        vector: np.ndarray[Any, Any],
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
    def get_vector(self, chunk_id: str) -> np.ndarray[Any, Any] | None:
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
    ) -> dict[str, np.ndarray[Any, Any]]:
        """Batch-read stored embeddings for *chunk_ids* present in this table.

        Returns ``{chunk_id: vector}`` for the ids that exist here; ids absent
        from this table (e.g. chunks routed to the other model) are simply
        omitted, which is how item pooling stays inside one embedding space.
        """
        return self._get_vectors(chunk_ids, strict=strict, bounded=bounded)

    def get_point_vectors(
        self, chunk_ids: list[str], *, point_scope: PointScope,
    ) -> dict[str, np.ndarray[Any, Any]]:
        """Read explicitly selected component vectors through prepared point indices."""
        if self._source_domain is None or point_scope is None:
            raise SourceLayoutError("Point vectors require a selected source domain")
        with self._source_domain.read(self._db, point_scope=point_scope):
            return self._get_vectors(
                chunk_ids, strict=True, bounded=True, point_scope=point_scope,
            )

    def _get_vectors(
        self, chunk_ids: list[str], *, strict: bool, bounded: bool,
        point_scope: PointScope | None = None,
    ) -> dict[str, np.ndarray[Any, Any]]:
        if not chunk_ids:
            return {}
        strict = strict or bounded
        if bounded:
            from .chunk_id_index import indexed_chunk_rows

            rows = indexed_chunk_rows(
                self, chunk_ids, columns=["chunk_id", "vector"], point_scope=point_scope,
            )
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
        out: dict[str, np.ndarray[Any, Any]] = {}
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
            return cast(int, table.count_rows())
        except Exception:
            return 0

    # -- validation ----------------------------------------------------------

    def _validate(self, chunks: list[Chunk], vectors: np.ndarray[Any, Any]) -> None:
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
