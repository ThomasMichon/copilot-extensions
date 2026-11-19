"""Prepare acquired embeddings without holding the store's writer fence."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from copy import deepcopy
from typing import TYPE_CHECKING, Any

import numpy as np

from agent_index.indexing.component_manifest import require_unordered_application, validate_components
from agent_index.indexing.embedding_batch import routed_model_chunks
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, input_hash
from agent_index.indexing.file_batches import FileBatch, file_batches
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.work_counts import current_counts
from agent_index.store.source_layout import SourceLayoutError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from agent_index.chunking.base import Chunk
    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.store.multi_model_store import MultiModelStore


def publish_prepared_manifest(
    *, manifest: CrawlManifest, full: bool, application: Callable[..., tuple[int, int, int]],
    store: MultiModelStore, prepared: PreparedApplication,
) -> tuple[int, int, int]:
    require_unordered_application(manifest)
    from agent_index.store.ingestion_index import prepare_ingestion_indices

    domain = store._source_domain
    with domain.operation(store.content_store.db) if domain is not None else nullcontext():
        prepared.require_current_checkpoints()
        if prepared.batch_count == 0 and not manifest.deletions:
            return application(manifest=manifest, full=full, prepared=prepared)
        planned_rows = (
            sum(len(batch.chunks) for batch in prepared.batches()) if domain is not None else 0
        )
        if domain is not None:
            prepare_ingestion_indices(store, max_rows=0, reserve_segment=planned_rows > 0)
        with domain.write(store.content_store.db) if domain is not None else nullcontext():
            result = application(manifest=manifest, full=full, prepared=prepared)
            if domain is not None:
                prepare_ingestion_indices(store, max_rows=planned_rows)
            return result


class PreparedApplication:
    """Cached inputs/results, never a store checkpoint or recovery authority."""

    def __init__(
        self, manifest: CrawlManifest, *, scope: str, config: IndexConfig,
        paths: PathIndex, store: MultiModelStore, clients: dict[str, EngineClient],
        full: bool, resume_since: float | None = None,
        before_embed: Callable[[], None] | None = None,
        before_model_embed: Callable[[str], None] | None = None,
        progress_cb: ProgressCallback | None = None,
        component_selection: frozenset[tuple[str, str]] | None = None,
        point_claim: PendingPointClaim | None = None,
    ) -> None:
        if point_claim is not None:
            if (
                type(point_claim) is not PendingPointClaim
                or component_selection is None or full is not False
            ):
                raise ValueError("Point preparation requires a whole typed claim and component selection")
            manifest = deepcopy(manifest)
        if component_selection is None:
            require_unordered_application(manifest)
        else:
            validate_components(manifest)
            if (
                manifest.component_versions is None or full
                or not isinstance(component_selection, frozenset)
                or not component_selection <= {(entry.source, entry.path) for entry in manifest.upserts}
            ):
                raise ValueError("Component preparation requires selected acquired upserts")
        files = [
            entry for entry in manifest.upserts
            if component_selection is None or (entry.source, entry.path) in component_selection
        ]
        profiles = dict(config.model_profiles)
        if any(model not in profiles for model in clients):
            raise ValueError("Prepared application requires every embedding client's model profile")
        payload = PendingCrawls(paths).read_payload(manifest.source, scope)
        if payload is None:
            raise PendingCrawlConflict("Prepared application requires its pending crawl")
        self.point_scope = point_claim.hints.scope if point_claim is not None else None
        domain = store._source_domain
        if domain is not None and (
            domain.writing is True or getattr(domain._local, "operation", False) is True
            or getattr(domain._local, "reading", False) is True
        ):
            raise SourceLayoutError("Preparation requires no active native operation")
        if point_claim is None:
            self.journal = PreparedJournal(
                paths, manifest.source, scope, payload, manifest=manifest, full=full,
            )
        else:
            if domain is None:
                raise SourceLayoutError("Point preparation requires a selected point domain")
            with domain.read(store.content_store.db, point_scope=self.point_scope):
                pass
            # Retained target recomposition must not hold native readers/writers out.
            self.journal = PreparedJournal.for_point_application(
                paths, manifest.source, scope, payload, manifest=manifest,
                point_claim=point_claim,
            )
        self.profiles = profiles
        self.progress_cb = progress_cb
        self.identities = [(entry.source, entry.path) for entry in files]
        self.checkpoints = paths.get_entries(self.identities)
        self.batch_count = 0
        self.journal.reset_batches(
            bind_plan=True, guard=domain.read(
                store.content_store.db, point_scope=self.point_scope,
            ) if domain is not None else None,
        )
        coverage = EmbeddingCoverage(paths)
        for batch in file_batches(
            files, path_index=paths, model_profiles=profiles,
            stream_batch_size=config.stream_batch_size,
            force=full or component_selection is not None,
            resume_since=resume_since if component_selection is None else None,
            chunker_revision=getattr(config, "chunker_revision", "1"),
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
                        before_model_embed=before_model_embed,
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
        before_model_embed: Callable[[str], None] | None = None,
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
        with domain.read(
            store.content_store.db, point_scope=self.point_scope,
        ) if domain is not None else nullcontext():
            to_embed = missing if force else coverage.missing(
                table, missing, profile, point_scope=self.point_scope, wal_aware=True,
            )
            needed_ids = {chunk.chunk_id for chunk in to_embed}
            reusable = [chunk for chunk in missing if chunk.chunk_id not in needed_ids]
            vectors = {}
            if reusable:
                ids = [chunk.chunk_id for chunk in reusable]
                vectors = (
                    table.get_vectors(ids, strict=True) if self.point_scope is None
                    else table.get_point_vectors(ids, point_scope=self.point_scope)
                )
        if reusable:
            self.journal.put_vectors(
                self._keys(model, reusable),
                np.asarray([vectors[chunk.chunk_id] for chunk in reusable]), profile.dim,
            )
        if not to_embed:
            return
        if before_embed:
            before_embed()
        if before_model_embed:
            before_model_embed(model)
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

    def vectors(self, model: str, chunks: list[Chunk]) -> np.ndarray[Any, Any]:
        profile = self.profiles[model]
        keys = self._keys(model, chunks)
        cached = self.journal.vectors(keys, profile.dim)
        if len(cached) != len(set(keys)):
            raise ValueError("Prepared embedding is missing; publication cannot call an engine")
        return np.asarray([cached[key] for key in keys], dtype=np.float32)
