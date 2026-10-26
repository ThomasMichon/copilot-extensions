"""Content-first, routed embedding writes with pair-coverage qualification."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent_index.indexing.work_counts import current_counts

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_index.chunking.base import Chunk
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import ModelProfile
    from agent_index.indexing.embedding_coverage import EmbeddingCoverage
    from agent_index.store.multi_model_store import MultiModelStore

logger = logging.getLogger(__name__)


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
) -> int:
    from agent_index.engine.client import EngineUnavailableError
    from agent_index.store.content_mutations import coalesce_chunks

    counts = current_counts()
    chunks = coalesce_chunks(chunks)
    all_claimed_types: frozenset[str] = frozenset()
    if model_profiles:
        all_claimed_types = frozenset().union(
            *(p.content_types for p in model_profiles.values() if p.content_types)
        )

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

        for model_id, client in multi_clients.items():
            profile = model_profiles.get(model_id) if model_profiles else None
            if profile and profile.content_types:
                model_chunks = [
                    c for c in sub
                    if c.chunk_type in profile.content_types
                    or c.chunk_type not in all_claimed_types
                ]
            else:
                model_chunks = sub
            if not model_chunks:
                continue

            model_counts = counts.model(model_id) if counts is not None else None
            candidates = len(model_chunks)
            if model_counts is not None:
                model_counts.candidate_chunks += candidates
            table = multi_store.get_vector_table(model_id) if coverage else None
            if coverage and table is not None and profile is not None and not force:
                model_chunks = coverage.missing(table, model_chunks, profile)
            if model_counts is not None:
                model_counts.reused_chunks += candidates - len(model_chunks)
            if not model_chunks:
                continue
            model_texts = [c.content for c in model_chunks]
            try:
                if before_embed:
                    before_embed()
                if coverage and table is not None:
                    coverage.invalidate(table, model_chunks)
                if model_counts is not None:
                    model_counts.embed_calls += 1
                    model_counts.chunks_submitted += len(model_chunks)
                model_vectors = client.embed_texts(model_texts)
                if model_counts is not None:
                    model_counts.chunks_embedded += len(model_vectors)
                written = multi_store.upsert_vectors(model_id, model_chunks, model_vectors)
                if model_counts is not None:
                    model_counts.vector_rows_upserted += written
                if coverage and table is not None and profile is not None:
                    coverage.record(table, model_chunks, profile, model_vectors)
            except EngineUnavailableError:
                logger.error(
                    "Engine for model '%s' unavailable mid-run; failing the "
                    "source so %d chunks are retried, not silently skipped",
                    model_id, len(model_chunks),
                )
                raise
            except Exception:
                # Content already committed; never acknowledge vector gaps.
                logger.error(
                    "Failed to embed batch with model '%s'; failing the "
                    "source so %d chunks are retried, not silently stored "
                    "without vectors", model_id, len(model_chunks),
                    exc_info=True,
                )
                raise
    return total
