"""A complete retained point candidate can prepare, but cannot publish or acknowledge."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pytest

from agent_index.engine.client import EngineClient
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.component_manifest import ComponentIdentity, ComponentVersion
from agent_index.indexing.embedding_coverage import EmbeddingCoverage, input_hash, vector_hash
from agent_index.indexing.file_batches import FileBatch
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_claim import PendingPointClaim
from agent_index.indexing.point_hints import (
    CommentVersionTarget, PointBinding, PointChangeHints, WorkItemRevisionTarget,
)
from agent_index.indexing.prepared_application import PreparedApplication, publish_prepared_manifest
from agent_index.indexing.prepared_journal import PreparedJournal
from agent_index.indexing.prepared_points import PointAcquisition, PreparedPoints
from agent_index.indexing.task_store import TaskStatus, TaskStore
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store

FAMILY, SCOPE = "sample:components", "account/endpoint/query"


@dataclass
class Fixture:
    config: IndexConfig
    store: MultiModelStore
    paths: PathIndex
    tasks: TaskStore
    points: PreparedPoints
    candidate: CrawlManifest
    calls: list[tuple[str, list[str]]] = field(default_factory=list)
    hook: Callable[[str], None] | None = None

    def prepare(self, **kwargs: Any) -> PreparedApplication:
        options: dict[str, Any] = {
            "manifest": self.candidate, "scope": SCOPE, "config": self.config,
            "paths": self.paths, "store": self.store, "full": False,
            "clients": {model: cast(EngineClient, Client(self, model))
                        for model in self.config.model_profiles},
            "component_selection": frozenset((e.source, e.path) for e in self.candidate.upserts),
            "point_claim": self.points.claim,
        }
        return PreparedApplication(**(options | kwargs))

    def cache(self) -> list[str]:
        with closing(self.paths._connect()) as conn:
            return list(conn.iterdump())


class Client:
    def __init__(self, fixture: Fixture, model: str) -> None:
        self.fixture, self.model = fixture, model

    def embed_texts(self, texts: list[str]) -> np.ndarray[Any, Any]:
        f = self.fixture
        domain = f.store._source_domain
        assert domain is not None and not domain.writing
        assert not getattr(domain._local, "operation", False)
        f.calls.append((self.model, texts))
        if f.hook:
            f.hook(self.model)
        return np.asarray([[len(text), 1] for text in texts], dtype=np.float32)


@pytest.fixture
def application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> Fixture:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    targets = (
        WorkItemRevisionTarget("one", 1, ("old-event",)),
        WorkItemRevisionTarget("one", 2, ("event-a",)),
        WorkItemRevisionTarget("one", 2, ("event-b",)),
        CommentVersionTarget("one", "two", 3, ("comment-event",)),
    )
    hints = PointChangeHints(
        source="selected", canonical_family=FAMILY, account_id="account-a",
        binding=PointBinding("https://example.test", "project", "eligible-query/v1"),
        targets=targets,
    )
    tasks = TaskStore(tmp_path / "tasks.db")
    task = tasks.enqueue(hints.source, change_hints=hints)
    claim = PendingPointClaim.from_task(tasks.pin_point_claim(task.id, expected_hints=hints))
    config = IndexConfig(
        data_dir=tmp_path / "owned", content_table="canonical", stream_batch_size=1,
        model_profiles={
            name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
            for name in ("first", "second")
        },
    )
    store = initialize_source_store(config, point_scope=claim.hints.scope)
    prepare_ingestion_indices(store, max_rows=0, point_scope=claim.hints.scope)
    paths = PathIndex(config.data_dir / "paths.db")
    observations = []
    for target in targets:
        comment = isinstance(target, CommentVersionTarget)
        deleted = comment and getattr(request, "param", True)
        version = target.version if isinstance(target, CommentVersionTarget) else target.revision
        path = "opaque-comment" if comment else "opaque-body"
        component = ComponentVersion(
            FAMILY, path, version, "fixture/v1",
            ComponentIdentity(target.kind, "one", "two" if comment else None),
        )
        observations.append(PointAcquisition(
            target, component,
            None if deleted else FileEntry(
                path, f"Version {version}", "text", FAMILY,
                {"key": True, "nested": {"original": [True]}},
            ),
            DeletedFile(FAMILY, path) if deleted else None,
        ))
    first = observations[0]
    assert first.entry is not None
    manifest = CrawlManifest(
        hints.source, accounting_family=FAMILY, commit="unchanged-poll",
        upserts=[first.entry], component_versions=[first.component],
    )
    pending = PendingCrawls(paths)
    pending.save(manifest, scope=SCOPE, full=False, point_claim=claim)
    payload = pending.read_payload(manifest.source, SCOPE)
    assert payload is not None
    points = PreparedPoints(PreparedJournal(paths, manifest.source, SCOPE, payload, point_claim=claim))
    for observed in observations:
        points.retain(observed, claim=claim)
    config.state_file.write_text('{"poll":"unchanged"}')
    return Fixture(config, store, paths, tasks, points, points.compose())


def test_interrupted_point_preparation_reopens_exact_cache_without_native_or_queue_effects(
    application: Fixture,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None
    receipt = domain.receipt()
    versions = {name: f.store.content_store.db.open_table(name).version for name in receipt["versions"]}
    poll = f.config.state_file.read_bytes()
    parent = f.points.journal

    def fail(model: str) -> None:
        if model == "second":
            raise RuntimeError("fixture interrupted second model")

    f.hook = fail
    with pytest.raises(RuntimeError, match="interrupted second model"):
        f.prepare()
    assert [model for model, _texts in f.calls] == ["first", "second"]
    f.paths = PathIndex(f.paths._db_path)
    f.store = initialize_source_store(f.config, point_scope=f.points.claim.hints.scope)
    f.calls.clear()
    f.hook = None
    prepared = f.prepare()
    assert [model for model, _texts in f.calls] == ["second"]
    assert all("Version 2" in text for _model, texts in f.calls for text in texts)
    assert prepared.journal.generation == parent.generation
    prepared.require_current_checkpoints()
    batches = list(prepared.batches())
    assert len(batches) == 1
    for model in f.config.model_profiles:
        assert prepared.vectors(model, batches[0].chunks).shape == (1, 2)
    f.calls.clear()
    f.prepare()
    assert f.calls == []
    assert domain.receipt() == receipt
    assert {name: f.store.content_store.db.open_table(name).version for name in versions} == versions
    assert f.paths.get_all_entries() == {}
    assert f.config.state_file.read_bytes() == poll
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent.payload
    task = f.tasks.get_task(f.points.claim.task_id)
    assert task is not None and task.status == TaskStatus.QUEUED_HINTS_V2
    for target in f.points.claim.hints.targets:
        assert f.points.read(target) is not None
    with pytest.raises(ValueError, match="completion is not supported"):
        PendingCrawls(f.paths).clear("selected")
    with pytest.raises(ValueError, match="component"):
        publish_prepared_manifest(
            manifest=f.candidate, full=False, store=f.store, prepared=prepared,
            application=lambda **kwargs: pytest.fail("Point publication is not admitted"),
        )


@pytest.mark.parametrize("change", [
    "default", "untyped", "no-selection", "full", "nonboolean-full", "scope",
    "claim", "family", "candidate", "metadata-type", "incomplete", "legacy-store",
])
def test_invalid_point_preparation_refuses_before_cache_reset_or_embedding(
    application: Fixture, change: str,
) -> None:
    f = application
    prepared = f.prepare()
    f.calls.clear()
    options: dict[str, Any] = {}
    if change == "default":
        options["point_claim"] = None
    elif change == "untyped":
        options["point_claim"] = f.points.claim.hints
    elif change == "no-selection":
        options["component_selection"] = None
    elif change in ("full", "nonboolean-full"):
        options["full"] = True if change == "full" else 0
    elif change == "scope":
        options["scope"] = "other-account"
    elif change == "claim":
        options["point_claim"] = replace(f.points.claim, point_claim_id="b" * 32)
    elif change == "family":
        options["point_claim"] = replace(
            f.points.claim, hints=replace(f.points.claim.hints, canonical_family="other:components"),
        )
    elif change in ("candidate", "metadata-type"):
        entry = f.candidate.upserts[0]
        changed = replace(entry, content="changed") if change == "candidate" else replace(
            entry, metadata={"key": 1},
        )
        options["manifest"] = replace(f.candidate, upserts=[changed])
    elif change == "incomplete":
        with closing(f.paths._connect()) as conn, conn:
            conn.execute("DELETE FROM prepared_crawl_rows WHERE kind = 'named-point-acquisition/v1'")
    else:
        options["store"] = initialize_source_store(replace(f.config, data_dir=f.config.data_dir / "legacy"))
    before = f.cache()
    with pytest.raises((ValueError, SourceLayoutError, PendingCrawlConflict)):
        f.prepare(**options)
    assert f.calls == []
    assert f.cache() == before
    assert list(prepared.batches())


def test_point_cache_keeps_existing_checkpoint_and_parent_incarnation_checks(
    application: Fixture,
) -> None:
    f = application
    prepared = f.prepare()
    f.paths.mark_indexed(FAMILY, "opaque-body", content_hash="changed", chunk_count=1)
    with pytest.raises(PendingCrawlConflict, match="checkpoints changed"):
        prepared.require_current_checkpoints()
    with closing(f.paths._connect()) as conn, conn:
        conn.execute("UPDATE prepared_crawl_parents SET generation = 'different'")
    f.calls.clear()
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        list(prepared.batches())
    assert f.calls == []


def test_point_preparation_reuses_only_native_vectors_with_exact_coverage(
    application: Fixture,
) -> None:
    f = application
    prepared = f.prepare()
    chunks = list(prepared.batches())[0].chunks
    assert len(chunks) == 1
    chunk = chunks[0]
    domain = f.store._source_domain
    assert domain is not None
    scope = f.points.claim.hints.scope
    vector = np.array([1, 0], dtype=np.float32)
    with domain._write(f.store.content_store.db, point_scope=scope):
        domain.effect("fixture-point-vector-seed")
        for profile in f.config.model_profiles.values():
            f.store.content_store.db.open_table(profile.table_name).add([{
                "chunk_id": chunk.chunk_id, "vector": vector.tolist(), "indexed_at": 0.0,
                "source": chunk.source, "language": chunk.language,
            }])
    prepare_ingestion_indices(f.store, max_rows=1, point_scope=scope)
    with closing(f.paths._connect()) as conn, conn:
        conn.execute("DELETE FROM prepared_crawl_rows WHERE kind = 'vector'")
        for model, profile in f.config.model_profiles.items():
            conn.execute("INSERT INTO embedding_coverage VALUES (?, ?, ?, ?, ?, ?)", (
                profile.table_name, chunk.chunk_id, chunk.source, chunk.file_path,
                input_hash(chunk, profile), vector_hash(vector) if model == "first" else "invalid",
            ))
        coverage = list(conn.execute("SELECT * FROM embedding_coverage"))
    receipt = domain.receipt()
    f.calls.clear()
    prepared = f.prepare()
    assert [model for model, _texts in f.calls] == ["second"]
    assert np.array_equal(prepared.vectors("first", chunks), np.asarray([vector]))
    assert domain.receipt() == receipt
    with closing(f.paths._connect()) as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM embedding_coverage")] == [
            tuple(row) for row in coverage
        ]
    assert f.paths.get_all_entries() == {}


@pytest.mark.parametrize("stage", ["before", "during-recomposition"])
def test_dirty_point_domain_refuses_before_changing_existing_preparation(
    application: Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    f = application
    f.prepare()
    before = f.cache()
    domain = f.store._source_domain
    assert domain is not None
    def dirty() -> None:
        with pytest.raises(RuntimeError, match="fixture uncertain effect"):
            with domain._write(f.store.content_store.db, point_scope=f.points.claim.hints.scope):
                domain.effect("fixture-point-uncertainty")
                raise RuntimeError("fixture uncertain effect")

    if stage == "before":
        dirty()
    else:
        compose = PreparedPoints.compose

        def interleave(self: PreparedPoints) -> CrawlManifest:
            manifest = compose(self)
            dirty()
            return manifest

        monkeypatch.setattr(PreparedPoints, "compose", interleave)
    f.calls.clear()
    with pytest.raises(SourceLayoutError):
        f.prepare()
    assert f.calls == []
    assert f.cache() == before


def test_point_preparation_snapshots_metadata_before_binding_and_embedding(application: Fixture) -> None:
    f = application
    entry = f.candidate.upserts[0]

    def mutate(_model: str) -> None:
        entry.metadata["key"] = 1
        entry.metadata["nested"]["original"][0] = False
        entry.metadata["extra"] = {"changed": [False]}

    f.hook = mutate
    prepared = f.prepare()
    chunks = list(prepared.batches())[0].chunks
    assert chunks and all(
        chunk.metadata == {"key": True, "nested": {"original": [True]}} for chunk in chunks
    )
    assert entry.metadata == {
        "key": 1, "nested": {"original": [False]}, "extra": {"changed": [False]},
    }
    with pytest.raises(PendingCrawlConflict, match="manifest does not match"):
        f.prepare()


@pytest.mark.parametrize("application", [False], indirect=True)
def test_point_batch_plan_replacement_invalidates_only_old_plan_handles(application: Fixture) -> None:
    f = application
    body = (FAMILY, "opaque-body")
    comment = (FAMILY, "opaque-comment")
    first = f.prepare(component_selection=frozenset({body}))
    first_chunks = list(first.batches())[0].chunks
    second = f.prepare(component_selection=frozenset({comment}))
    assert {chunk.file_path for batch in second.batches() for chunk in batch.chunks} == {comment[1]}
    second.require_current_checkpoints()
    for read in (lambda: list(first.batches()), first.require_current_checkpoints):
        with pytest.raises(PendingCrawlConflict, match="batch plan was replaced"):
            read()
    # Vector inputs belong to the exact claim cache, not the replaceable file plan.
    assert first.vectors("first", first_chunks).shape == (1, 2)


def test_point_recomposition_does_not_exclude_native_readers(
    application: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None
    compose = PreparedPoints.compose
    observed = []

    def read_native() -> None:
        with domain.read(f.store.content_store.db, point_scope=f.points.claim.hints.scope):
            observed.append(domain.receipt()["state"])

    def compose_without_native_fence(self: PreparedPoints) -> CrawlManifest:
        assert not getattr(domain._local, "reading", False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(read_native).result(timeout=5)
        return compose(self)

    monkeypatch.setattr(PreparedPoints, "compose", compose_without_native_fence)
    f.prepare()
    assert observed == ["clean"]


def test_point_preparation_reads_coverage_with_an_open_wal_without_relaxing_qualification(
    application: Fixture,
) -> None:
    f = application
    with closing(f.paths._connect()) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        prepared = f.prepare()
        chunks = list(prepared.batches())[0].chunks
        prepared.require_current_checkpoints()
        profile = f.config.model_profiles["first"]
        with pytest.raises(sqlite3.OperationalError, match="checkpointed path evidence"):
            EmbeddingCoverage(f.paths).missing(
                f.store.get_vector_table("first"), chunks, profile,
                point_scope=f.points.claim.hints.scope, read_only=True,
            )
    assert [model for model, _texts in f.calls] == ["first", "second"]
    assert f.paths.get_all_entries() == {}


@pytest.mark.parametrize("bind_second", [False, True])
def test_legacy_journal_plan_replacement_also_revokes_only_bound_plans(
    tmp_path: Path, bind_second: bool,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    pending = PendingCrawls(paths)
    manifest = CrawlManifest(
        "sample:legacy", accounting_family="sample:legacy",
        upserts=[FileEntry("one.md", "Legacy input", "text", "sample:legacy")],
    )
    pending.save(manifest, scope=SCOPE, full=False)
    payload = pending.read_payload(manifest.source, SCOPE)
    assert payload is not None
    first = PreparedJournal(paths, manifest.source, SCOPE, payload)
    first.reset_batches(bind_plan=True)
    batch = FileBatch([], [("sample:legacy", "one.md", "fingerprint", 0)])
    first.put_batch(0, batch)
    first.put_vectors(["pair"], np.array([[1, 0]], dtype=np.float32), 2)
    second = PreparedJournal(paths, manifest.source, SCOPE, payload)
    if not bind_second:
        unbound_operations: tuple[Callable[[], None], ...] = (
            second.reset_batches, lambda: second.put_batch(0, batch),
        )
        for unbound_operation in unbound_operations:
            with pytest.raises(PendingCrawlConflict, match="bound application"):
                unbound_operation()
        assert list(first.batches(1)) == [batch]
        assert np.array_equal(first.vectors(["pair"], 2)["pair"], [1, 0])
        return
    second.reset_batches(bind_plan=True)
    second.put_batch(0, batch)
    bound_operations: tuple[Callable[[], object], ...] = (
        lambda: list(first.batches(1)), lambda: first.put_batch(0, batch),
    )
    for bound_operation in bound_operations:
        with pytest.raises(PendingCrawlConflict, match="batch plan was replaced"):
            bound_operation()
    assert list(second.batches(1)) == [batch]
    assert np.array_equal(first.vectors(["pair"], 2)["pair"], [1, 0])


@pytest.mark.parametrize("operation", ["put-batch", "put", "immutable", "reset"])
def test_unbound_point_cache_cannot_mutate_an_application_plan(
    application: Fixture, operation: str,
) -> None:
    f = application
    prepared = f.prepare()
    before = f.cache()
    unbound = f.points.journal
    payload = b'{"version":1,"chunks":[],"checkpoints":[["wrong","wrong","fp",0]]}'
    with pytest.raises(PendingCrawlConflict, match="bound application"):
        if operation == "put-batch":
            unbound.put_batch(0, FileBatch([], [("wrong", "wrong", "fp", 0)]))
        elif operation == "put":
            unbound.put("batch", [("0", payload)])
        elif operation == "immutable":
            unbound.put_immutable("batch", "1", payload)
        else:
            unbound.reset_batches()
    assert f.cache() == before
    assert {chunk.file_path for batch in prepared.batches() for chunk in batch.chunks} == {"opaque-body"}


@pytest.mark.parametrize("point", [False, True])
def test_source_aware_plan_reset_excludes_native_writers(
    application: Fixture, monkeypatch: pytest.MonkeyPatch, point: bool,
) -> None:
    f = application
    options: dict[str, Any] = {"clients": {}}
    store = f.store
    scope = f.points.claim.hints.scope if point else None
    if not point:
        config = replace(f.config, data_dir=f.config.data_dir / "ordinary")
        store = initialize_source_store(config)
        paths = PathIndex(config.data_dir / "paths.db")
        manifest = replace(f.candidate, component_versions=None)
        PendingCrawls(paths).save(manifest, scope=SCOPE, full=False)
        options.update(
            config=config, store=store, paths=paths, manifest=manifest,
            point_claim=None, component_selection=None,
        )
    domain = store._source_domain
    assert domain is not None
    receipt = domain.receipt()
    reset = PreparedJournal.reset_batches
    blocked = []

    def try_writer() -> None:
        with pytest.raises(SourceLayoutError, match="busy"):
            with domain._write(store.content_store.db, point_scope=scope):
                pytest.fail("Native writer entered during plan replacement")
        blocked.append(True)

    def reset_under_exclusion(self: PreparedJournal, *, bind_plan: bool = False) -> None:
        assert getattr(domain._local, "reading", False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(try_writer).result(timeout=5)
        reset(self, bind_plan=bind_plan)

    monkeypatch.setattr(PreparedJournal, "reset_batches", reset_under_exclusion)
    f.prepare(**options).require_current_checkpoints()
    assert blocked == [True]
    assert domain.receipt() == receipt
