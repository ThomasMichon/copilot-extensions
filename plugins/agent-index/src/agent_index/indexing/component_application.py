"""Explicit native component application; no polling or claimed-task completion."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from agent_index.chunking.base import Chunk
from agent_index.indexing.component_manifest import ComponentContent
from agent_index.indexing.embedding_batch import (
    _point_pair_store, embed_and_store_batch, embed_and_store_pairs, routed_model_chunks,
)
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, embedding_profile_key
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.prepared_application import PreparedApplication
from agent_index.indexing.prepared_points import PreparedPoints
from agent_index.store.component_ledger import ComponentEvidence, ComponentLedger
from agent_index.store.content_mutations import coalesce_chunks, content_record
from agent_index.store.file_projection import (
    RawFileProjection, capture_file_projection, require_file_projection_capacity,
)
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.point_reconciliation import reconcile_point_file
from agent_index.store.scalar_index import (
    MAX_SEGMENTS, ScalarIndexPreparationRequired, inspect_index, prepare_index,
)
from agent_index.store.source_layout import PointScope, SourceLayoutError

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_index.engine.client import EngineClient
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.manifest import CrawlManifest
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.runner import ProgressCallback
    from agent_index.store.content_store import ContentStore
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.vector_table import VectorTable

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
    *, point_scope: PointScope | None = None, read_only: bool = False,
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
            point_scope=point_scope,
            read_only=read_only,
            wal_aware=point_scope is not None and not read_only,
        ):
            raise SourceLayoutError("Component routed vector coverage is incomplete")
    return chunks


@dataclass(frozen=True)
class AppliedComponentSnapshot:
    """Qualified native point at one read boundary, not a durable completion token."""

    evidence: ComponentEvidence
    chunks: tuple[Chunk, ...]
    coverage: ClassVar[Literal["named-target-only"]] = "named-target-only"


def qualify_applied_component(
    ledger: ComponentLedger, paths: PathIndex, evidence: ComponentEvidence, *,
    scope: str, max_rows: int,
    point_scope: PointScope | None = None,
) -> AppliedComponentSnapshot:
    """Qualify exact ordering, native, checkpoint and routed-coverage owners."""
    config, store = ledger._config, ledger._store
    if (type(max_rows) is not int or max_rows < 1
            or not isinstance(scope, str) or not scope.strip()
            or Path(paths._db_path).resolve() != (config.data_dir / "path_index.db").resolve()
            or not Path(paths._db_path).is_file()):
        raise ValueError("Component qualification requires configured path/scope/point budget")
    content = evidence.content
    point = content.component.source, content.component.path
    with ledger._source_domain.operation(store.content_store.db, point_scope=point_scope):
        if ((evidence.scope, evidence.policy) != (scope, _policy(config))
                or ledger.get(*point, point_scope=point_scope) != evidence):
            raise SourceLayoutError("Component application evidence changed or is unqualified")
        projection = capture_file_projection(
            config, store, *point, max_rows=max_rows,
            retained_chunk_ids=evidence.projection.vector_chunk_ids,
            point_scope=point_scope,
        )
        if projection != evidence.projection:
            raise SourceLayoutError("Component application native projection changed")
        try:
            chunks = _qualified_chunks(
                projection, config, store, EmbeddingCoverage(paths), point_scope=point_scope,
                read_only=True,
            )
            checkpoint = paths.get_checkpoint_rows([point], read_only=True).get(point)
        except sqlite3.Error as exc:
            raise SourceLayoutError("Component application path evidence is unavailable") from exc
        if content.deleted:
            if chunks or any(model.rows != b"[]" for model in projection.models):
                raise SourceLayoutError("Component deletion still has native content or vectors")
            complete = checkpoint is None
        else:
            complete = checkpoint is not None and checkpoint[:2] == (
                content.fingerprint, len(chunks),
            )
        if not complete:
            raise SourceLayoutError("Component application checkpoint is incomplete")
        return AppliedComponentSnapshot(evidence, tuple(chunks))


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
        point_claim: PendingPointClaim | None = None,
    ) -> None:
        if point_claim is not None:
            if type(point_claim) is not PendingPointClaim or family != point_claim.hints.canonical_family:
                raise ValueError("Point application requires a whole typed claim and its family")
            manifest = deepcopy(manifest)
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
        self.manifest = manifest
        self.point_scope = point_claim.hints.scope if point_claim is not None else None
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
        with self.domain.operation(store.content_store.db, point_scope=self.point_scope):
            self.checkpoints = paths.get_checkpoint_rows(list(self.contents))
            for point, content in self.contents.items():
                previous = self.ledger.get(*point, point_scope=self.point_scope)
                if previous is None:
                    projection = self._capture(point, previous)
                else:
                    projection = qualify_applied_component(
                        self.ledger, paths, previous, scope=scope, max_rows=max_rows,
                        point_scope=self.point_scope,
                    ).evidence.projection
                self.previous[point], self.projections[point] = previous, projection
                if previous is None:
                    if projection.content_rows != b"[]" or any(
                        model.rows != b"[]" for model in projection.models
                    ) or point in self.checkpoints:
                        raise SourceLayoutError("Component has native state without application evidence")
                else:
                    if previous.content.component.semantics != content.component.semantics:
                        raise SourceLayoutError("Component application evidence changed or is unqualified")
                    if content.covered_by(previous.content):
                        continue
                self.selected.add(point)
        # Parent decoding/binding remains with the existing subordinate cache owner.
        self.prepared = PreparedApplication(
            manifest, scope=scope, config=config, paths=paths, store=store,
            clients=self.clients, full=False, progress_cb=progress_cb,
            before_embed=before_embed, before_model_embed=before_model_embed,
            component_selection=frozenset(self.selected & entries.keys()),
            point_claim=point_claim,
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

    def _capture(
        self, point: Point, previous: ComponentEvidence | None,
        *, store: MultiModelStore | None = None,
    ) -> RawFileProjection:
        return capture_file_projection(
            self.config, self.store if store is None else store, *point, max_rows=self.max_rows,
            retained_chunk_ids=previous.projection.vector_chunk_ids if previous else (),
            point_scope=self.point_scope,
        )

    def publish(self) -> tuple[int, int]:
        """Return applied/covered point counts, never successful claim acknowledgement."""
        with self.domain.operation(self.store.content_store.db, point_scope=self.point_scope):
            if _policy(self.config) != self.policy:
                raise PendingCrawlConflict("Component indexing policy changed during preparation")
            if Path(self.paths._db_path).resolve() != (
                self.config.data_dir / "path_index.db"
            ).resolve():
                raise PendingCrawlConflict("Component configured path owner changed during preparation")
            # Reconstructing validates the current configured native model/table owners.
            ComponentLedger(self.config, self.store)
            self.prepared.require_current_checkpoints()
            if self.point_scope is not None:
                PreparedPoints(self.prepared.journal).bind_application(self.manifest)
            if self.paths.get_checkpoint_rows(list(self.contents)) != self.checkpoints:
                raise PendingCrawlConflict("Component checkpoints changed during preparation")
            for point in self.contents:
                previous = self.previous[point]
                if previous is not None:
                    qualify_applied_component(
                        self.ledger, self.paths, previous,
                        scope=self.scope, max_rows=self.max_rows,
                        point_scope=self.point_scope,
                    )
                elif (self.ledger.get(*point, point_scope=self.point_scope) is not None
                        or self._capture(point, None) != self.projections[point]):
                    raise PendingCrawlConflict("Component native point changed during preparation")
            if not self.selected and self.point_scope is None:
                return 0, len(self.contents)
            # Selected capacity and task-local native owners require the existing writer.
            refusal: Exception | None = None
            with self.domain._write(
                self.store.content_store.db, point_scope=self.point_scope,
            ) if self.point_scope is not None else nullcontext():
                sequence = self.domain._local.effect_sequence if self.point_scope is not None else None
                try:
                    store = self.store if self.point_scope is None else _point_pair_store(
                        self.store, self.config.model_profiles, point_scope=self.point_scope,
                    )
                    ledger = self.ledger if self.point_scope is None else ComponentLedger(self.config, store)
                    if self.point_scope is not None:
                        for point in self.contents:
                            if (ledger.get(*point, point_scope=self.point_scope) != self.previous[point]
                                    or self._capture(point, self.previous[point], store=store)
                                    != self.projections[point]):
                                raise PendingCrawlConflict("Component native point changed during preparation")
                    planned_rows = sum(len(chunks) for chunks in self.chunks.values())
                    if self.selected:
                        require_file_projection_capacity(
                            store, {point: len(chunks) for point, chunks in self.chunks.items()},
                            max_rows=self.max_rows, point_scope=self.point_scope,
                        )
                        prepare_ingestion_indices(
                            store, max_rows=0,
                            reserve_segment=self.point_scope is None and planned_rows > 0,
                            point_scope=self.point_scope,
                        )
                        if self.point_scope is not None:
                            self._require_point_segments(store)
                        ledger_state, _ = inspect_index(ledger._bound_table(), column="point_key")
                        if ledger_state.segments + len(self.selected) > MAX_SEGMENTS:
                            raise ScalarIndexPreparationRequired("Component ledger needs explicit index consolidation")
                except Exception as exc:
                    if (self.point_scope is None or self.domain._local.effect_sequence != sequence
                            or self.domain._local.failed_effect):
                        raise
                    # Only no-effect preflight may finish the writer before refusing.
                    refusal = exc
                else:
                    return self._publish(store, ledger, planned_rows) if self.selected else (0, len(self.contents))
            assert refusal is not None
            raise refusal

    def _require_point_segments(self, store: MultiModelStore) -> None:
        """Reserve the whole selected plan, including each routed batch and cleanup."""
        assert self.point_scope is not None
        growth = {self.config.content_table: 0, **{
            profile.table_name: 0 for profile in self.config.model_profiles.values()
        }}
        required = dict(growth)
        for point in sorted(self.selected):
            chunks = coalesce_chunks(self.chunks[point])
            projection = self.projections[point]
            old = {
                row["chunk_id"]: {key: value for key, value in row.items() if key != "indexed_at"}
                for row in json.loads(projection.content_rows)
            }
            keep = {chunk.chunk_id for chunk in chunks}
            name = self.config.content_table
            growth[name] += int(any(old.get(chunk.chunk_id) != content_record(chunk) for chunk in chunks))
            required[name] = max(required[name], growth[name] + bool(old.keys() - keep))
            for model in projection.models:
                name = model.table_name
                rows = {row["chunk_id"]: row for row in json.loads(model.rows)}
                growth[name] += int(any(
                    chunk.chunk_id in rows and (
                        rows[chunk.chunk_id]["source"], rows[chunk.chunk_id]["language"]
                    ) != (chunk.source, chunk.language)
                    for chunk in chunks
                ))
                for start in range(0, len(chunks), self.config.stream_batch_size):
                    routed = routed_model_chunks(
                        chunks[start:start + self.config.stream_batch_size],
                        model.model_id, self.config.model_profiles,
                    )
                    if routed and self.coverage.missing(
                        store.get_vector_table(model.model_id), routed,
                        self.config.model_profiles[model.model_id], bounded=True,
                        point_scope=self.point_scope, wal_aware=True,
                    ):
                        growth[name] += 1
                # Cleanup reserves a free slot even when native deletion adds no segment.
                required[name] = max(required[name], growth[name] + bool(rows.keys() - keep))
        owners: list[ContentStore | VectorTable] = [
            store.content_store, *(store.get_vector_table(model) for model in store.model_ids),
        ]
        for owner in owners:
            columns = ["chunk_id", "file_path", "source"] if owner is store.content_store else ["chunk_id"]
            for column in columns:
                state, _ = inspect_index(owner._get_or_create_table(), column=column)
                if column == "source" and not state.prepared:
                    continue
                if state.segments + required[owner._table_name] > MAX_SEGMENTS:
                    raise ScalarIndexPreparationRequired(
                        f"Component {owner._table_name} {column} index needs explicit consolidation"
                    )

    def _publish(
        self, store: MultiModelStore, ledger: ComponentLedger, planned_rows: int,
    ) -> tuple[int, int]:
        with self.domain._write(store.content_store.db, point_scope=self.point_scope):
            for point in sorted(self.selected):
                if self.progress_cb:
                    self.progress_cb.check_cancelled()
                self.paths.invalidate_batch([point])
                chunks = self.chunks[point]
                if self.point_scope is None:
                    embed_and_store_batch(
                        chunks, store, self.clients, self.config.model_profiles,
                        stream_batch_size=self.config.stream_batch_size, coverage=self.coverage,
                        skip_identical_content=True, prepared_vectors=self.prepared.vectors,
                    )
                    store.reconcile_file_chunks(*point, {chunk.chunk_id for chunk in chunks})
                else:
                    self._publish_point(store, point, chunks)
                self.coverage.retain(*point, {chunk.chunk_id for chunk in chunks})
            prepare_ingestion_indices(
                store, max_rows=planned_rows, point_scope=self.point_scope,
            )
            if self.progress_cb:
                self.progress_cb.check_cancelled()
            targets = {}
            for point in sorted(self.selected):
                projection = self._capture(point, self.previous[point], store=store)
                actual = _qualified_chunks(
                    projection, self.config, store, self.coverage, point_scope=self.point_scope,
                )
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
                self.config, store, ledger, max_rows=self.max_rows,
            )
            if self.progress_cb:
                self.progress_cb.check_cancelled()
            return result

    def _publish_point(self, store: MultiModelStore, point: Point, chunks: list[Chunk]) -> None:
        from .work_counts import current_counts

        assert self.point_scope is not None
        written = store.upsert_point_content(chunks, point_scope=self.point_scope)
        counts = current_counts()
        if counts is not None:
            counts.content_rows_upserted += written
        prepare_ingestion_indices(store, max_rows=len(chunks), point_scope=self.point_scope)

        def after_store(table: VectorTable, rows: int) -> None:
            prepare_index(table, column="chunk_id", max_rows=rows, point_scope=self.point_scope)

        embed_and_store_pairs(
            chunks, store, self.clients, self.config.model_profiles,
            stream_batch_size=self.config.stream_batch_size, coverage=self.coverage,
            bounded=True, prepared_vectors=self.prepared.vectors, point_scope=self.point_scope,
            after_store=after_store,
        )
        reconcile_point_file(
            self.config, store, *point, tuple(chunk.chunk_id for chunk in chunks),
            max_rows=self.max_rows,
            retained_chunk_ids=self.projections[point].vector_chunk_ids,
            point_scope=self.point_scope,
        )
