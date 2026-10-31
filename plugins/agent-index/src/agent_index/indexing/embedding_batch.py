"""Content-first, routed embedding writes with pair-coverage qualification."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_index.indexing.work_counts import current_counts

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    from agent_index.chunking.base import Chunk
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import ModelProfile
    from agent_index.indexing.embedding_coverage import EmbeddingCoverage
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.vector_table import VectorTable

logger = logging.getLogger(__name__)


def routed_model_chunks(
    chunks: list[Chunk], model_id: str, profiles: dict[str, ModelProfile] | None,
) -> list[Chunk]:
    """Shared pair routing, including unclaimed content types."""
    if not profiles:
        return chunks
    profile = profiles.get(model_id)
    if profile is None or not profile.content_types:
        return chunks
    claimed = frozenset().union(
        *(p.content_types for p in profiles.values() if p.content_types),
    )
    return [c for c in chunks if c.chunk_type in profile.content_types or c.chunk_type not in claimed]


def embed_and_store_batch(
    chunks: list[Chunk],
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    model_profiles: dict[str, ModelProfile] | None = None,
    *,
    stream_batch_size: int = 500,
    coverage: EmbeddingCoverage | None = None,
    force: bool = False,
    before_embed: Callable[[], None] | None = None,
    skip_identical_content: bool = False,
    prepared_vectors: Callable[[str, list[Chunk]], np.ndarray] | None = None,
) -> int:
    from agent_index.store.content_mutations import coalesce_chunks

    if stream_batch_size < 1:
        raise ValueError("Embedding batch size must be positive")
    counts = current_counts()
    chunks = coalesce_chunks(chunks)
    total = 0
    for i in range(0, len(chunks), stream_batch_size):
        sub = chunks[i : i + stream_batch_size]
        if skip_identical_content:
            written = multi_store.upsert_content(sub, skip_identical=True)
        else:
            written = multi_store.upsert_content(sub)
        if counts is not None:
            counts.content_rows_upserted += written
        total += len(sub)
        embed_and_store_pairs(
            sub, multi_store, multi_clients, model_profiles,
            stream_batch_size=stream_batch_size, coverage=coverage,
            force=force, before_embed=before_embed,
            prepared_vectors=prepared_vectors,
        )
    return total


def embed_and_store_pairs(
    chunks: list[Chunk],
    multi_store: MultiModelStore,
    multi_clients: dict[str, EngineClient],
    model_profiles: dict[str, ModelProfile] | None = None,
    *,
    stream_batch_size: int = 500,
    coverage: EmbeddingCoverage | None = None,
    force: bool = False,
    before_embed: Callable[[], None] | None = None,
    bounded: bool = False,
    after_store: Callable[[VectorTable, int], None] | None = None,
    prepared_vectors: Callable[[str, list[Chunk]], np.ndarray] | None = None,
) -> int:
    """Write routed vector pairs without content writes; return rows upserted.

    Bounded callers supply prepared canonical/coverage reads and an after-store
    callback for explicitly budgeted vector-index preparation. This is not a
    discovery, profile-selection, source-lease or progress authority.
    """
    from agent_index.engine.client import EngineUnavailableError
    from agent_index.store.content_mutations import coalesce_chunks

    if stream_batch_size < 1:
        raise ValueError("Embedding batch size must be positive")
    if bounded and (
        coverage is None or after_store is None or model_profiles is None
        or any(model_id not in model_profiles for model_id in multi_clients)
    ):
        raise ValueError("Bounded pairs require coverage, model profiles and after-store preparation")
    counts = current_counts()
    chunks = coalesce_chunks(chunks)
    total = 0
    for i in range(0, len(chunks), stream_batch_size):
        sub = chunks[i : i + stream_batch_size]
        if bounded:
            _require_current_input(multi_store, sub)

        for model_id, client in multi_clients.items():
            profile = model_profiles.get(model_id) if model_profiles else None
            model_chunks = routed_model_chunks(sub, model_id, model_profiles)
            if not model_chunks:
                continue

            model_counts = counts.model(model_id) if counts is not None else None
            candidates = len(model_chunks)
            if model_counts is not None:
                model_counts.candidate_chunks += candidates
            table = multi_store.get_vector_table(model_id) if coverage else None
            if coverage and table is not None and profile is not None and not force:
                if bounded:
                    model_chunks = coverage.missing(table, model_chunks, profile, bounded=True)
                else:
                    model_chunks = coverage.missing(table, model_chunks, profile)
            if model_counts is not None:
                model_counts.reused_chunks += candidates - len(model_chunks)
            if not model_chunks:
                continue
            model_texts = [c.content for c in model_chunks]
            try:
                if before_embed and prepared_vectors is None:
                    before_embed()
                if coverage and table is not None and not bounded:
                    coverage.invalidate(table, model_chunks)
                if model_counts is not None and prepared_vectors is None:
                    model_counts.embed_calls += 1
                    model_counts.chunks_submitted += len(model_chunks)
                model_vectors = (
                    prepared_vectors(model_id, model_chunks) if prepared_vectors is not None
                    else client.embed_texts(model_texts)
                )
                if model_counts is not None and prepared_vectors is None:
                    model_counts.chunks_embedded += len(model_vectors)
                if bounded:
                    written = multi_store.upsert_vectors(
                        model_id, model_chunks, model_vectors, bounded_input=True,
                    )
                else:
                    written = multi_store.upsert_vectors(model_id, model_chunks, model_vectors)
                total += written
                if model_counts is not None:
                    model_counts.vector_rows_upserted += written
                if coverage and table is not None and profile is not None:
                    if bounded:
                        coverage.invalidate(table, model_chunks)
                        if after_store is not None:
                            after_store(table, len(model_chunks))
                        coverage.record(
                            table, model_chunks, profile, model_vectors, bounded=True,
                        )
                    else:
                        coverage.record(table, model_chunks, profile, model_vectors)
            except EngineUnavailableError:
                logger.error(
                    "Engine for model '%s' unavailable mid-run; failing the "
                    "source so %d chunks are retried, not silently skipped",
                    model_id, len(model_chunks),
                )
                raise
            except Exception:
                # Never acknowledge a failed or partially stored vector batch.
                logger.error(
                    "Failed to embed batch with model '%s'; failing the "
                    "source so %d chunks are retried, not silently stored "
                    "without vectors", model_id, len(model_chunks),
                    exc_info=True,
                )
                raise
    return total


def _require_current_input(store: MultiModelStore, chunks: list[Chunk]) -> None:
    from agent_index.store.chunk_id_index import ChunkIdIndexPreparationRequired
    from agent_index.store.source_projection import projection_records

    content = store.content_store
    domain = content._source_domain
    if domain is None:
        raise ChunkIdIndexPreparationRequired("Bounded pairs require a participating source store")
    with domain.read(content._db):
        projection_records(
            content._db, content._table_name, chunks, bounded_content=content,
            require_current_input=True,
        )
