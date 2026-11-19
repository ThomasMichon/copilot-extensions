"""Explicit native component application; no polling or claimed-task completion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.chunking.base import Chunk
from agent_index.indexing.component_manifest import ComponentContent
from agent_index.indexing.embedding_batch import embed_and_store_batch, routed_model_chunks
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, embedding_profile_key
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.prepared_application import PreparedApplication
from agent_index.store.component_ledger import ComponentEvidence, ComponentLedger
from agent_index.store.content_mutations import content_record
from agent_index.store.file_projection import (
    RawFileProjection, capture_file_projection, require_file_projection_capacity,
)
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.scalar_index import MAX_SEGMENTS, ScalarIndexPreparationRequired, inspect_index
from agent_index.store.source_layout import SourceLayoutError

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.store.multi_model_store import MultiModelStore

Point = tuple[str, str]


def _policy(config: IndexConfig) -> str:
    value = [
        config.chunker_revision, config.content_table,
        [[name, profile.table_name, embedding_profile_key(profile), sorted(profile.content_types)]
         for name, profile in sorted(config.model_profiles.items())],
    ]
    return "component-application-v1:" + hashlib.sha256(json.dumps(value).encode()).hexdigest()


def _component_contents(
    manifest: CrawlManifest, config: IndexConfig,
) -> dict[Point, ComponentContent]:
    entries = {(entry.source, entry.path): entry for entry in manifest.upserts}
    assert manifest.component_versions is not None
    return {
        (version.source, version.path): ComponentContent(
            version,
            file_fingerprint(
                entries[(version.source, version.path)], config.model_profiles,
                chunker_revision=config.chunker_revision,
            ) if (version.source, version.path) in entries else "component-deleted-v1",
            (version.source, version.path) not in entries,
        )
        for version in manifest.component_versions
    }


def _qualified_chunks(
    projection: RawFileProjection, config: IndexConfig, store: MultiModelStore,
    coverage: EmbeddingCoverage,
) -> list[Chunk]:
    """Qualify canonical inputs and exact routed pairs, not just an old raw witness."""
    chunks = []
    for row in json.loads(projection.content_rows):
        chunk = Chunk(
            row["content"], row["file_path"], row["chunk_type"], row["language"],
            row["line_start"], row["line_end"], row["source"],
            json.loads(row["metadata"]) if row["metadata"] else {},
        )
        if {key: value for key, value in row.items() if key != "indexed_at"} != content_record(chunk):
            raise SourceLayoutError("Component canonical input is incompatible")
        chunks.append(chunk)
    for model in projection.models:
        selected = routed_model_chunks(chunks, model.model_id, config.model_profiles)
        if {row["chunk_id"] for row in json.loads(model.rows)} != {
            chunk.chunk_id for chunk in selected
        } or coverage.missing(
            store.get_vector_table(model.model_id), selected,
            config.model_profiles[model.model_id], bounded=True,
        ):
            raise SourceLayoutError("Component routed vector coverage is incomplete")
    return chunks


class PreparedComponentApplication:
    """Apply one retained manifest without clearing it or advancing source state.

    Callers explicitly select a component family and a native per-point row cap.
    The ordinary engine/recovery paths do not select this application.
    """

    def __init__(
        self, manifest: CrawlManifest, *, scope: str, family: str, max_rows: int,
        config: IndexConfig, paths: PathIndex, store: MultiModelStore,
        clients: dict[str, EngineClient], progress_cb: ProgressCallback | None = None,
        before_embed: Callable[[], None] | None = None,
        before_model_embed: Callable[[str], None] | None = None,
    ) -> None:
        if (
            Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve()
            or set(clients) != set(config.model_profiles)
            or not family or manifest.accounting_family != family
            or manifest.stored_family not in (None, family)
            or type(max_rows) is not int or max_rows < 1
        ):
            raise ValueError("Component application requires configured path/model/family owners")
        PendingCrawls._validate(manifest)
        if manifest.component_versions is None:
            raise ValueError("Component application requires ordered acquisition")
        self.config, self.paths, self.store = config, paths, store
        self.scope, self.family, self.max_rows = scope, family, max_rows
        self.policy = _policy(config)
        self.clients = dict(clients)
        self.ledger = ComponentLedger(config, store)
        self.domain = self.ledger._source_domain
        self.coverage = EmbeddingCoverage(paths)
        self.progress_cb = progress_cb
        entries = {(entry.source, entry.path): entry for entry in manifest.upserts}
        self.contents = _component_contents(manifest, config)
        self.previous: dict[Point, ComponentEvidence | None] = {}
        self.projections: dict[Point, RawFileProjection] = {}
        self.selected: set[Point] = set()
        with self.domain.operation(store.content_store.db):
            self.checkpoints = paths.get_checkpoint_rows(list(self.contents))
            for point, content in self.contents.items():
                previous = self.ledger.get(*point)
                projection = self._capture(point, previous)
                self.previous[point], self.projections[point] = previous, projection
                if previous is None:
                    if projection.content_rows != b"[]" or any(
                        model.rows != b"[]" for model in projection.models
                    ) or point in self.checkpoints:
                        raise SourceLayoutError("Component has native state without application evidence")
                else:
                    if (previous.scope, previous.policy, previous.projection) != (
                        scope, self.policy, projection,
                    ) or previous.content.component.semantics != content.component.semantics:
                        raise SourceLayoutError("Component application evidence changed or is unqualified")
                    _qualified_chunks(projection, config, store, self.coverage)
                    checkpoint = self.checkpoints.get(point)
                    if (
                        previous.content.deleted and checkpoint is not None
                        or not previous.content.deleted and (
                            checkpoint is None or checkpoint[0] != previous.content.fingerprint
                        )
                    ):
                        raise SourceLayoutError("Component application checkpoint is incomplete")
                    if content.covered_by(previous.content):
                        continue
                self.selected.add(point)
        # Parent decoding/binding remains with the existing subordinate cache owner.
        self.prepared = PreparedApplication(
            manifest, scope=scope, config=config, paths=paths, store=store,
            clients=self.clients, full=False, progress_cb=progress_cb,
            before_embed=before_embed, before_model_embed=before_model_embed,
            component_selection=frozenset(self.selected & entries.keys()),
        )
        self.chunks: dict[Point, list[Chunk]] = {point: [] for point in self.selected}
        self.checkpoint_rows: dict[Point, tuple[str, str, str, int]] = {}
        for batch in self.prepared.batches():
            for chunk in batch.chunks:
                self.chunks[(chunk.source, chunk.file_path)].append(chunk)
            for checkpoint_row in batch.checkpoints:
                self.checkpoint_rows[checkpoint_row[:2]] = checkpoint_row
        for point, chunks in self.chunks.items():
            if len(set(self.projections[point].vector_chunk_ids) | {
                chunk.chunk_id for chunk in chunks
            }) > max_rows:
                raise ScalarIndexPreparationRequired("Component prepared point union budget exceeded")

    def _capture(self, point: Point, previous: ComponentEvidence | None) -> RawFileProjection:
        return capture_file_projection(
            self.config, self.store, *point, max_rows=self.max_rows,
            retained_chunk_ids=previous.projection.vector_chunk_ids if previous else (),
        )

    def publish(self) -> tuple[int, int]:
        """Return applied/covered point counts, never successful claim acknowledgement."""
        with self.domain.operation(self.store.content_store.db):
            if _policy(self.config) != self.policy:
                raise PendingCrawlConflict("Component indexing policy changed during preparation")
            if Path(self.paths._db_path).resolve() != (
                self.config.data_dir / "path_index.db"
            ).resolve():
                raise PendingCrawlConflict("Component configured path owner changed during preparation")
            # Reconstructing validates the current configured native model/table owners.
            ComponentLedger(self.config, self.store)
            self.prepared.require_current_checkpoints()
            if self.paths.get_checkpoint_rows(list(self.contents)) != self.checkpoints:
                raise PendingCrawlConflict("Component checkpoints changed during preparation")
            for point in self.contents:
                if (
                    self.ledger.get(*point) != self.previous[point]
                    or self._capture(point, self.previous[point]) != self.projections[point]
                ):
                    raise PendingCrawlConflict("Component native point changed during preparation")
                if self.previous[point] is not None:
                    _qualified_chunks(
                        self.projections[point], self.config, self.store, self.coverage,
                    )
            if not self.selected:
                return 0, len(self.contents)
            require_file_projection_capacity(
                self.store, {point: len(chunks) for point, chunks in self.chunks.items()},
                max_rows=self.max_rows,
            )
            planned_rows = sum(len(chunks) for chunks in self.chunks.values())
            prepare_ingestion_indices(self.store, max_rows=0, reserve_segment=planned_rows > 0)
            ledger_state, _ = inspect_index(self.ledger._bound_table(), column="point_key")
            if ledger_state.segments + len(self.selected) > MAX_SEGMENTS:
                raise ScalarIndexPreparationRequired("Component ledger needs explicit index consolidation")
            with self.domain.write(self.store.content_store.db):
                for point in sorted(self.selected):
                    if self.progress_cb:
                        self.progress_cb.check_cancelled()
                    self.paths.invalidate_batch([point])
                    chunks = self.chunks[point]
                    embed_and_store_batch(
                        chunks, self.store, self.clients, self.config.model_profiles,
                        stream_batch_size=self.config.stream_batch_size, coverage=self.coverage,
                        skip_identical_content=True, prepared_vectors=self.prepared.vectors,
                    )
                    self.store.reconcile_file_chunks(*point, {chunk.chunk_id for chunk in chunks})
                    self.coverage.retain(*point, {chunk.chunk_id for chunk in chunks})
                prepare_ingestion_indices(self.store, max_rows=planned_rows)
                if self.progress_cb:
                    self.progress_cb.check_cancelled()
                targets = {}
                for point in sorted(self.selected):
                    projection = self._capture(point, self.previous[point])
                    actual = _qualified_chunks(projection, self.config, self.store, self.coverage)
                    if [content_record(chunk) for chunk in actual] != sorted(
                        [content_record(chunk) for chunk in self.chunks[point]],
                        key=lambda row: row["chunk_id"],
                    ):
                        raise SourceLayoutError("Component application did not store its prepared input")
                    targets[point] = ComponentEvidence(
                        self.contents[point], self.scope, self.policy, projection,
                    )
                from .component_publication import retain_component_publication

                publication = retain_component_publication(self, targets)
                result = publication.finish(
                    self.config, self.store, self.ledger, max_rows=self.max_rows,
                )
                if self.progress_cb:
                    self.progress_cb.check_cancelled()
            return result
