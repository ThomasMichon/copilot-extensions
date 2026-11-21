"""Explicit native point application never retires its complete parked claim."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
import numpy as np

import test_point_prepared_application as prepared_fixture

from agent_index.engine.client import EngineClient
from agent_index.chunking.base import Chunk
from agent_index.indexing.component_application import (
    PreparedComponentApplication, _policy, qualify_applied_component,
)
from agent_index.indexing.component_manifest import ComponentContent, ComponentVersion
from agent_index.indexing.component_publication import reconcile_component_publication
from agent_index.indexing.embedding_batch import embed_and_store_pairs
from agent_index.indexing.embedding_coverage import EmbeddingCoverage
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.point_hints import WorkItemRevisionTarget
from agent_index.indexing.task_store import TaskStatus
from agent_index.indexing.work_counts import measure_reindex
from agent_index.store.component_ledger import ComponentEvidence, ComponentLedger, initialize_component_ledger
from agent_index.store.file_projection import capture_file_projection
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired, inspect_index, prepare_index
from agent_index.store.source_layout import MARKER, SourceLayoutError
from agent_index.store.vector_table import VectorTable

FAMILY, SCOPE = prepared_fixture.FAMILY, prepared_fixture.SCOPE
point_preparation = prepared_fixture.application


@pytest.fixture
def application(point_preparation: prepared_fixture.Fixture) -> prepared_fixture.Fixture:
    f = point_preparation
    initialize_component_ledger(f.config, f.store, point_scope=f.points.claim.hints.scope)
    return f


def prepare(f: prepared_fixture.Fixture, **kwargs: Any) -> PreparedComponentApplication:
    options: dict[str, Any] = {
        "manifest": f.candidate, "scope": SCOPE, "family": FAMILY, "max_rows": 8,
        "config": f.config, "paths": f.paths, "store": f.store,
        "clients": {model: cast(EngineClient, prepared_fixture.Client(f, model))
                    for model in f.config.model_profiles},
        "point_claim": f.points.claim,
    }
    return PreparedComponentApplication(**(options | kwargs))


def reconcile(f: prepared_fixture.Fixture, **kwargs: Any) -> tuple[int, int]:
    options: dict[str, Any] = {
        "manifest": f.candidate, "scope": SCOPE, "family": FAMILY, "max_rows": 8,
        "config": f.config, "paths": f.paths, "store": f.store, "point_claim": f.points.claim,
    }
    return reconcile_component_publication(**(options | kwargs))


def reopen(f: prepared_fixture.Fixture) -> None:
    f.paths = PathIndex(f.paths._db_path)
    f.store = MultiModelStore(
        f.config.lance_dir, content_table=f.config.content_table,
        point_scope=f.points.claim.hints.scope,
    )
    for profile in f.config.model_profiles.values():
        f.store.register_model(profile)
    f.calls.clear()


@pytest.mark.parametrize("point_preparation", [False, True], indirect=True)
def test_complete_point_application_reopens_without_retiring_parent_or_trailing_targets(
    application: prepared_fixture.Fixture,
) -> None:
    f = application
    parent = f.points.journal.payload
    poll = f.config.state_file.read_bytes()

    @measure_reindex
    def apply() -> dict[str, Any]:
        wall, cpu = time.perf_counter(), time.process_time()
        prepared = prepare(f)
        preparation = time.perf_counter() - wall, time.process_time() - cpu
        calls = list(f.calls)
        wall, cpu = time.perf_counter(), time.process_time()
        result = prepared.publish()
        publication = time.perf_counter() - wall, time.process_time() - cpu
        assert f.calls == calls
        return {"result": result, "preparation_wall_cpu": preparation,
                "publication_wall_cpu": publication}

    first = cast(dict[str, Any], apply())
    assert first["result"] == (2, 0) and f.calls
    assert first["work_counts"]["content_rows_upserted"] == len(f.candidate.upserts)
    assert sum(
        counts["vector_rows_upserted"] for counts in first["work_counts"]["models"].values()
    ) == 2 * len(f.candidate.upserts)
    scope = f.points.claim.hints.scope
    ledger = ComponentLedger(f.config, f.store)
    for version in f.candidate.component_versions or []:
        evidence = ledger.get(version.source, version.path, point_scope=scope)
        assert evidence is not None and evidence.content.component == version
        snapshot = qualify_applied_component(
            ledger, f.paths, evidence, scope=SCOPE, max_rows=8, point_scope=scope,
        )
        assert bool(snapshot.chunks) != evidence.content.deleted
    f.tasks.enqueue("selected", change_hints=replace(
        f.points.claim.hints, targets=(WorkItemRevisionTarget("one", 4, ("trailing-event",)),),
    ))
    pinned = f.tasks.get_task(f.points.claim.task_id)
    assert pinned is not None and pinned.status == TaskStatus.QUEUED_HINTS_V2
    assert pinned.point_claim_id == f.points.claim.point_claim_id
    assert pinned.change_hints == f.points.claim.hints
    reopen(f)
    domain = f.store._source_domain
    assert domain is not None
    before = domain.receipt()
    covered = cast(dict[str, Any], apply())
    assert covered["result"] == (0, 2)
    assert covered["work_counts"]["content_rows_upserted"] == 0
    assert covered["work_counts"]["models"] == {}
    assert not f.calls and domain.receipt() == before
    assert PendingCrawls(f.paths).read_payload("selected", SCOPE) == parent
    assert f.config.state_file.read_bytes() == poll
    with pytest.raises(ValueError, match="completion is not supported"):
        PendingCrawls(f.paths).clear("selected")
    with pytest.raises(SourceLayoutError, match="not admitted"):
        ledger.get(FAMILY, "opaque-body")
    print(json.dumps({"unapplied": first, "covered_reopen": covered}, default=str, sort_keys=True))


@pytest.mark.parametrize("drift", ["default", "claim", "candidate", "checkpoint", "policy", "acquisition"])
def test_selected_preflight_refuses_before_native_or_checkpoint_effects(
    application: prepared_fixture.Fixture, drift: str,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None
    if drift in ("default", "claim", "candidate"):
        options: dict[str, Any] = {
            "point_claim": replace(f.points.claim, point_claim_id="b" * 32),
        } if drift == "claim" else {"point_claim": None} if drift == "default" else {
            "manifest": replace(f.candidate, upserts=[
                replace(f.candidate.upserts[0], content="Different input"),
            ]),
        }
        before = domain.receipt()
        with pytest.raises((PendingCrawlConflict, SourceLayoutError, ValueError)):
            prepare(f, **options)
    else:
        prepared = prepare(f)
        if drift == "checkpoint":
            f.paths.mark_indexed(FAMILY, "opaque-body", content_hash="foreign", chunk_count=7)
        elif drift == "policy":
            f.config.model_profiles["first"] = replace(f.config.model_profiles["first"], revision="next")
        else:
            with closing(f.paths._connect()) as conn, conn:
                conn.execute("DELETE FROM prepared_crawl_rows WHERE kind = 'named-point-acquisition/v1'")
        before = domain.receipt()
        with pytest.raises((PendingCrawlConflict, SourceLayoutError, ValueError)):
            prepared.publish()
    assert domain.receipt() == before
    assert f.store.content_store.db.open_table(f.config.content_table).count_rows() == 0


def test_concurrent_application_observation_cannot_replace_newer_applied_evidence(
    application: prepared_fixture.Fixture,
) -> None:
    f = application
    first, stale = prepare(f), prepare(f)
    assert first.publish() == (2, 0)
    domain = f.store._source_domain
    assert domain is not None
    before = domain.receipt(), f.paths.get_all_entries()
    with pytest.raises(PendingCrawlConflict):
        stale.publish()
    assert (domain.receipt(), f.paths.get_all_entries()) == before


def test_full_ledger_refusal_preserves_clean_receipt_and_checkpoints(
    application: prepared_fixture.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    scope = f.points.claim.hints.scope
    domain = f.store._source_domain
    assert domain is not None
    ledger = ComponentLedger(f.config, f.store)
    with domain._write(f.store.content_store.db, point_scope=scope):
        for index in range(3):
            path = f"unrelated-tombstone-{index}"
            projection = capture_file_projection(
                f.config, f.store, FAMILY, path, max_rows=8, point_scope=scope,
            )
            ledger.put(ComponentEvidence(
                ComponentContent(ComponentVersion(FAMILY, path, 1, "fixture/v1"), "deleted", True),
                SCOPE, _policy(f.config), projection,
            ), expected=None, max_rows=8, point_scope=scope)
    state, _ = inspect_index(ledger._bound_table(), column="point_key")
    prepared = prepare(f)
    before = (domain.path / MARKER).read_bytes(), domain.receipt(), f.paths.get_all_entries()
    with monkeypatch.context() as patch:
        patch.setattr("agent_index.indexing.component_application.MAX_SEGMENTS", state.segments)
        with pytest.raises(ScalarIndexPreparationRequired, match="ledger"):
            prepared.publish()
    assert ((domain.path / MARKER).read_bytes(), domain.receipt(), f.paths.get_all_entries()) == before
    reopen(f)
    assert prepare(f).publish() == (2, 0)


@pytest.mark.parametrize("point_preparation", [False], indirect=True)
@pytest.mark.parametrize("bottleneck", ["canonical", "vectors_first"])
def test_whole_plan_capacity_refuses_before_first_point_effect(
    application: prepared_fixture.Fixture, monkeypatch: pytest.MonkeyPatch, bottleneck: str,
) -> None:
    f = application
    scope = f.points.claim.hints.scope
    domain = f.store._source_domain
    assert domain is not None
    seed_count = 2 if bottleneck == "canonical" else 3
    chunks = [Chunk(f"seed {index}", f"seed-{index}", "text", "text", 0, 1, FAMILY)
              for index in range(seed_count)]
    with domain._write(f.store.content_store.db, point_scope=scope):
        content_batches = [[chunk] for chunk in chunks] if bottleneck == "canonical" else [chunks]
        for batch in content_batches:
            f.store.upsert_point_content(batch, point_scope=scope)
            prepare_ingestion_indices(f.store, max_rows=len(batch), point_scope=scope)
        for model in f.store.model_ids:
            batches = [[chunk] for chunk in chunks] if bottleneck != "canonical" else [chunks]
            for batch in batches:
                f.store.upsert_point_vectors(
                    model, batch, np.ones((len(batch), 2), dtype=np.float32), point_scope=scope,
                )
                prepare_index(
                    f.store.get_vector_table(model), column="chunk_id", max_rows=len(batch),
                    point_scope=scope,
                )
    table = f.store.content_store.db.open_table(bottleneck)
    state, _ = inspect_index(table, column="chunk_id")
    assert state.segments == seed_count
    prepared = prepare(f)
    if bottleneck != "canonical":
        canonical, _ = inspect_index(f.store.content_store._get_or_create_table(), column="chunk_id")
        assert canonical.segments + 2 <= state.segments + 1
    before = (domain.path / MARKER).read_bytes(), domain.receipt(), f.paths.get_all_entries()
    with monkeypatch.context() as patch:
        # A smaller test budget retains real native segments and the two-point plan.
        patch.setattr("agent_index.indexing.component_application.MAX_SEGMENTS", state.segments + 1)
        with pytest.raises(ScalarIndexPreparationRequired, match=bottleneck):
            prepared.publish()
    assert ((domain.path / MARKER).read_bytes(), domain.receipt(), f.paths.get_all_entries()) == before
    reopen(f)
    assert prepare(f).publish() == (2, 0)


def test_single_point_reserves_each_routed_stream_batch(
    application: prepared_fixture.Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    f = application
    domain = f.store._source_domain
    assert domain is not None

    def split(content: str, file_path: str, *, source: str) -> list[Chunk]:
        return [Chunk(f"{content} part {index}", file_path, "text", "text", index, index, source)
                for index in range(3)]

    monkeypatch.setattr(
        "agent_index.chunking.get_chunker", lambda path: (SimpleNamespace(chunk=split), "text"),
    )
    prepared = prepare(f)
    assert sorted(len(chunks) for chunks in prepared.chunks.values()) == [0, 3]
    before = (domain.path / MARKER).read_bytes(), domain.receipt(), f.paths.get_all_entries()
    with monkeypatch.context() as patch:
        patch.setattr("agent_index.indexing.component_application.MAX_SEGMENTS", 2)
        with pytest.raises(ScalarIndexPreparationRequired, match="vectors_first"):
            prepared.publish()
    assert ((domain.path / MARKER).read_bytes(), domain.receipt(), f.paths.get_all_entries()) == before
    assert prepared.publish() == (2, 0)


@pytest.mark.parametrize("stage", ["preflight-effect", "path", "native"])
def test_application_failure_after_preflight_keeps_dirty_latch(
    application: prepared_fixture.Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    f = application
    prepared = prepare(f)
    domain = f.store._source_domain
    assert domain is not None
    original = prepared._publish_point

    def interrupt(*args: Any, **kwargs: Any) -> None:
        if stage == "native":
            original(*args, **kwargs)
        elif stage == "preflight-effect":
            domain.effect("fixture-unexpected-preflight-effect")
        raise RuntimeError("fixture unsealed interruption")

    monkeypatch.setattr(
        prepared, "_require_point_segments" if stage == "preflight-effect" else "_publish_point", interrupt,
    )
    with pytest.raises(RuntimeError, match="unsealed interruption"):
        prepared.publish()
    assert domain.receipt()["state"] == "dirty"
    assert "reconciliation" not in domain.receipt()
    assert bool(f.store.content_store.db.open_table(f.config.content_table).count_rows()) == (stage == "native")


def test_selected_replacement_cleans_prior_vectors_and_retains_ordered_history(
    application: prepared_fixture.Fixture,
) -> None:
    f = application
    entry = replace(f.candidate.upserts[0], content="Prior input")
    version = next(
        version for version in f.candidate.component_versions or [] if version.path == entry.path
    )
    prior = replace(version, version=1)
    chunk = Chunk(entry.content, entry.path, "text", "text", 0, 1, entry.source)
    scope = f.points.claim.hints.scope
    domain = f.store._source_domain
    assert domain is not None
    ledger = ComponentLedger(f.config, f.store)
    coverage = EmbeddingCoverage(f.paths)

    def after_store(table: VectorTable, rows: int) -> None:
        prepare_index(table, column="chunk_id", max_rows=rows, point_scope=scope)

    fingerprint = file_fingerprint(
        entry, f.config.model_profiles, chunker_revision=f.config.chunker_revision,
    )
    # Seed qualified older native state without replacing or retiring a pending parent.
    with domain._write(f.store.content_store.db, point_scope=scope):
        f.store.upsert_point_content([chunk], point_scope=scope)
        prepare_ingestion_indices(f.store, max_rows=1, point_scope=scope)
        embed_and_store_pairs(
            [chunk], f.store, {model: cast(EngineClient, object()) for model in f.config.model_profiles},
            f.config.model_profiles, coverage=coverage, bounded=True, point_scope=scope,
            after_store=after_store,
            prepared_vectors=lambda _model, chunks: np.ones((len(chunks), 2), dtype=np.float32),
        )
        projection = capture_file_projection(
            f.config, f.store, entry.source, entry.path, max_rows=8, point_scope=scope,
        )
        ledger.put(
            ComponentEvidence(ComponentContent(prior, fingerprint, False), SCOPE, _policy(f.config), projection),
            expected=None, max_rows=8, point_scope=scope,
        )
        f.paths.mark_indexed(entry.source, entry.path, content_hash=fingerprint, chunk_count=1)
    assert prepare(f).publish() == (2, 0)
    evidence = ledger.get(entry.source, entry.path, point_scope=scope)
    assert evidence is not None and evidence.content.component == version
    assert chunk.chunk_id in evidence.projection.vector_chunk_ids
    assert all(
        chunk.chunk_id not in f.store.get_vector_table(model).get_point_vectors(
            [chunk.chunk_id], point_scope=scope,
        )
        for model in f.config.model_profiles
    )


@pytest.mark.parametrize("stage", ["ordering", "checkpoint", "clean"])
def test_selected_sealed_suffix_reopens_without_engines_or_native_replay(
    application: prepared_fixture.Fixture, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    f = application
    prepared = prepare(f)
    domain = f.store._source_domain
    assert domain is not None

    def interrupt(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("fixture sealed interruption")

    with monkeypatch.context() as patch:
        if stage == "ordering":
            patch.setattr(ComponentLedger, "put", interrupt)
        elif stage == "checkpoint":
            patch.setattr(f.paths, "mark_indexed_batch", interrupt)
        else:
            save = domain.save

            def refuse_clean(value: dict[str, Any]) -> None:
                if value["state"] == "clean":
                    interrupt()
                save(value)

            patch.setattr(domain, "save", refuse_clean)
        with pytest.raises(RuntimeError, match="sealed interruption"):
            prepared.publish()
    assert domain.receipt()["state"] == "dirty" and domain.receipt()["reconciliation"]
    versions = {
        name: f.store.content_store.db.open_table(name).version
        for name in domain.receipt()["versions"] if name != "component_ordering_evidence"
    }
    reopen(f)
    with pytest.raises((ValueError, SourceLayoutError)):
        reconcile(f, point_claim=None)
    assert reconcile(f) == (2, 0)
    assert not f.calls
    assert {
        name: f.store.content_store.db.open_table(name).version for name in versions
    } == versions
    assert domain.receipt()["state"] == "clean"
    assert prepare(f).publish() == (0, 2)
    assert not f.calls


@pytest.mark.parametrize("drift", ["claim", "acquisition", "native", "coverage", "checkpoint"])
def test_selected_reconciliation_cannot_bless_changed_owners(
    application: prepared_fixture.Fixture, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    f = application
    prepared = prepare(f)
    with monkeypatch.context() as patch:
        def interrupt(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("fixture sealed interruption")

        patch.setattr(f.paths, "mark_indexed_batch", interrupt)
        with pytest.raises(RuntimeError, match="sealed interruption"):
            prepared.publish()
    options: dict[str, Any] = {}
    if drift == "claim":
        options["point_claim"] = replace(f.points.claim, point_claim_id="b" * 32)
    elif drift in ("acquisition", "coverage"):
        with closing(f.paths._connect()) as conn, conn:
            conn.execute(
                "DELETE FROM prepared_crawl_rows WHERE kind = 'named-point-acquisition/v1'"
                if drift == "acquisition" else "DELETE FROM embedding_coverage",
            )
    elif drift == "native":
        table = f.store.content_store.db.open_table(f.config.content_table)
        table.add(table.search().limit(1).to_list())
    else:
        f.paths.mark_indexed(FAMILY, "opaque-body", content_hash="foreign", chunk_count=7)
    reopen(f)
    domain = f.store._source_domain
    assert domain is not None
    before = domain.receipt(), f.paths.get_all_entries()
    with pytest.raises((PendingCrawlConflict, SourceLayoutError, ValueError)):
        reconcile(f, **options)
    assert (domain.receipt(), f.paths.get_all_entries()) == before
    assert not f.calls
