"""Prepare acquired embeddings without holding the store's writer fence."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from typing import TYPE_CHECKING

import numpy as np

from agent_index.indexing.embedding_batch import routed_model_chunks
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, input_hash
from agent_index.indexing.file_batches import FileBatch, file_batches
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.work_counts import current_counts

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from agent_index.chunking.base import Chunk
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.store.multi_model_store import MultiModelStore


class PreparedApplication:
    """Cached inputs/results, never a store checkpoint or recovery authority."""

    def __init__(
        self, manifest: CrawlManifest, *, scope: str, config: IndexConfig,
        paths: PathIndex, store: MultiModelStore, clients: dict[str, EngineClient],
        full: bool, resume_since: float | None = None,
        before_embed: Callable[[], None] | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> None:
        profiles = dict(config.model_profiles)
        if any(model not in profiles for model in clients):
            raise ValueError("Prepared application requires every embedding client's model profile")
        payload = PendingCrawls(paths).read_payload(manifest.source, scope)
        if payload is None:
            raise PendingCrawlConflict("Prepared application requires its pending crawl")
        self.journal = PreparedJournal(paths, manifest.source, scope, payload)
        self.profiles = profiles
        self.progress_cb = progress_cb
        self.identities = [(entry.source, entry.path) for entry in manifest.upserts]
        self.checkpoints = paths.get_entries(self.identities)
        self.batch_count = 0
        self.journal.reset_batches()
        coverage = EmbeddingCoverage(paths)
        for batch in file_batches(
            manifest.upserts, path_index=paths, model_profiles=profiles,
            stream_batch_size=config.stream_batch_size, force=full,
            resume_since=resume_since, chunker_revision=getattr(config, "chunker_revision", "1"),
            progress_cb=progress_cb, resume_index=self.checkpoints,
        ):
            for offset in range(0, len(batch.chunks), config.stream_batch_size):
                sub = batch.chunks[offset:offset + config.stream_batch_size]
                for model, client in clients.items():
                    if progress_cb:
                        progress_cb.check_cancelled()
                    selected = routed_model_chunks(sub, model, profiles)
                    self._prepare_pairs(
                        model, selected, store, client, coverage, force=full,
                        before_embed=before_embed,
                    )
            self.journal.put_batch(self.batch_count, batch)
            self.batch_count += 1
        if progress_cb:
            progress_cb.check_cancelled()

    def _keys(self, model: str, chunks: list[Chunk]) -> list[str]:
        profile = self.profiles[model]
        return [
            hashlib.sha256(json.dumps([
                model, profile.table_name, chunk.chunk_id, input_hash(chunk, profile),
            ]).encode()).hexdigest()
            for chunk in chunks
        ]

    def _prepare_pairs(
        self, model: str, chunks: list[Chunk], store: MultiModelStore,
        client: EngineClient, coverage: EmbeddingCoverage, *, force: bool,
        before_embed: Callable[[], None] | None,
    ) -> None:
        if not chunks:
            return
        profile = self.profiles[model]
        keys = self._keys(model, chunks)
        cached = self.journal.vectors(keys, profile.dim)
        missing = [chunk for chunk, key in zip(chunks, keys, strict=True) if key not in cached]
        if not missing:
            return
        table = store.get_vector_table(model)
        domain = store._source_domain
        with domain.read(store.content_store.db) if domain is not None else nullcontext():
            to_embed = missing if force else coverage.missing(table, missing, profile)
            needed_ids = {chunk.chunk_id for chunk in to_embed}
            reusable = [chunk for chunk in missing if chunk.chunk_id not in needed_ids]
            vectors = table.get_vectors([chunk.chunk_id for chunk in reusable], strict=True) if reusable else {}
        if reusable:
            self.journal.put_vectors(
                self._keys(model, reusable),
                np.asarray([vectors[chunk.chunk_id] for chunk in reusable]), profile.dim,
            )
        if not to_embed:
            return
        if before_embed:
            before_embed()
        counts = current_counts()
        model_counts = counts.model(model) if counts is not None else None
        if model_counts is not None:
            model_counts.embed_calls += 1
            model_counts.chunks_submitted += len(to_embed)
        vectors_array = client.embed_texts([chunk.content for chunk in to_embed])
        if model_counts is not None:
            model_counts.chunks_embedded += len(vectors_array)
        self.journal.put_vectors(self._keys(model, to_embed), vectors_array, profile.dim)

    def require_current_checkpoints(self) -> None:
        """Check before writer entry; never invalidate a file during preparation."""
        if self.progress_cb:
            self.progress_cb.check_cancelled()
        current = self.journal.paths.get_entries(self.identities)
        counts = current_counts()
        if counts is not None:
            counts.checkpoint_lookup_keys += len(self.identities)
            counts.checkpoint_rows_found += len(current)
        if current != self.checkpoints:
            raise PendingCrawlConflict("File checkpoints changed during application preparation")
        self.journal.get("batch", [])
        if self.progress_cb:
            self.progress_cb.check_cancelled()

    def batches(self) -> Iterator[FileBatch]:
        return self.journal.batches(self.batch_count)

    def vectors(self, model: str, chunks: list[Chunk]) -> np.ndarray:
        profile = self.profiles[model]
        keys = self._keys(model, chunks)
        cached = self.journal.vectors(keys, profile.dim)
        if len(cached) != len(set(keys)):
            raise ValueError("Prepared embedding is missing; publication cannot call an engine")
        return np.asarray([cached[key] for key in keys], dtype=np.float32)
