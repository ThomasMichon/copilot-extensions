"""Native maintenance composes witnessed deltas, prepared reads and atomic publication."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.derived_maintenance import refresh_clusters
from agent_index.indexing.work_counts import WorkCounts, _current
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.cluster_slice_inputs import ClusterSliceRepairRequired, cluster_slice_inputs
from agent_index.store.cluster_store import ClusterStore
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired
from agent_index.store.source_bucket_index import prepare_source_index
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store


def chunk(bucket="forge:issues", path="one.md"):
    return Chunk(f"text {bucket} {path}", path, "text", "markdown", 1, 2, bucket + ":repo")


def forbidden(*args, **kwargs):
    raise AssertionError("bounded maintenance attempted unrelated work")


def prepare(store, count=4):
    prepare_source_index(store.content_store, max_rows=count)
    for model in store._vector_tables:
        prepare_chunk_id_index(store.get_vector_table(model), max_rows=count)


def rows(config):
    with sqlite3.connect(config.clusters_db) as conn:
        return (
            conn.execute("SELECT * FROM clusters ORDER BY cluster_id").fetchall(),
            conn.execute("SELECT * FROM cluster_members ORDER BY cluster_id, member_rank").fetchall(),
        )


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path, cluster_enabled=True, cluster_slice_row_budget=2,
        model_profiles={
            model: ModelProfile(model, f"fixture-{model}", dim=2, table_name=f"vectors_{model}")
            for model in ("a", "b")
        },
    )
    store = initialize_source_store(config)
    chunks = [chunk(bucket, path) for bucket in ("forge:issues", "other:items")
              for path in ("one.md", "two.md")]
    for model in ("a", "b"):
        store.upsert(model, chunks, np.array([[1, 0]] * 4, np.float32))
    return config, store, chunks


def seed(config, store):
    assert refresh_clusters(store, config, force=True)["clusters"] == 4
    prepare(store)
    return ClusterStore(config.clusters_db).slice_manifest()


def test_opt_in_requires_explicit_seed_even_when_global_coverage_is_current(corpus, monkeypatch):
    config, store, _chunks = corpus
    legacy = replace(config, cluster_slice_row_budget=None)
    assert refresh_clusters(store, legacy)["clusters"] == 4
    before = rows(config)
    monkeypatch.setattr(store, "iter_item_representations", forbidden)
    with pytest.raises(ClusterSliceRepairRequired, match="coverage is missing"):
        refresh_clusters(store, config)
    assert rows(config) == before


@pytest.mark.parametrize("budget", [-1, True, 1.5, "2"])
def test_invalid_opt_in_budget_never_reads_or_writes(corpus, monkeypatch, budget):
    config, store, _chunks = corpus
    monkeypatch.setattr(store, "iter_item_representations", forbidden)
    with pytest.raises(ValueError, match="budget"):
        refresh_clusters(store, replace(config, cluster_slice_row_budget=budget), force=True)


def test_opt_in_refuses_legacy_domain_without_implicit_migration(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path / "lance")
    config = IndexConfig(data_dir=tmp_path, cluster_slice_row_budget=2, cluster_enabled=True)
    with pytest.raises(ClusterSliceRepairRequired, match="participating"):
        refresh_clusters(store, config, force=True)
    assert not config.clusters_db.exists()


def test_restart_noop_never_reads_items_or_publishes_and_counts_cache_hit(corpus, monkeypatch):
    config, store, _chunks = corpus
    previous = seed(config, store)
    fresh = initialize_source_store(config)
    before = rows(config)
    monkeypatch.setattr(fresh, "iter_bucket_item_representations", forbidden)
    monkeypatch.setattr(fresh, "iter_item_representations", forbidden)
    monkeypatch.setattr(ClusterStore, "replace_all", forbidden)
    monkeypatch.setattr(ClusterStore, "replace_slices", forbidden)
    counts = WorkCounts()
    token = _current.set(counts)
    try:
        assert refresh_clusters(fresh, config) == previous.coverage.stats()
    finally:
        _current.reset(token)
    assert counts.cluster_cache_hits == 1 and counts.cluster_pass_attempts == 0
    assert rows(config) == before


def test_vector_delta_reads_only_one_model_and_preserves_every_unselected_row(corpus, monkeypatch):
    config, store, chunks = corpus
    previous = seed(config, store)
    before = rows(config)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    prepare_chunk_id_index(store.get_vector_table("a"), max_rows=1)
    selected = []
    iterate = store.iter_bucket_item_representations

    def bounded(bucket, **kwargs):
        selected.append((bucket, kwargs))
        yield from iterate(bucket, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "iter_bucket_item_representations", bounded)
        patch.setattr(store, "iter_item_representations", forbidden)
        patch.setattr(store.get_vector_table("b"), "get_vectors", forbidden)
        patch.setattr(ClusterStore, "replace_all", forbidden)
        actual = refresh_clusters(store, config)
    assert selected == [("forge:issues", {
        "model_ids": ("a",), "row_budget": 2, "include_unembedded": True,
    })]
    assert actual["slices_refreshed"] == actual["buckets_refreshed"] == 1
    assert actual["items"] == 4 and actual["slices"] == 4 and actual["clusters"] == 3
    after = rows(config)
    untouched = {row[0] for row in before[0] if row[1:3] != ("forge:issues", "a")}
    assert [row for row in before[0] if row[0] in untouched] == [
        row for row in after[0] if row[0] in untouched
    ]
    assert [row for row in before[1] if row[0] in untouched] == [
        row for row in after[1] if row[0] in untouched
    ]
    current = ClusterStore(config.clusters_db).slice_manifest()
    assert current.basis == previous.basis and current.coverage.signature != previous.coverage.signature
    assert refresh_clusters(store, config)["maintenance_skipped"] == 1
    full = refresh_clusters(store, config, force=True)
    assert {key: full[key] for key in ("items", "slices", "clusters")} == {
        key: actual[key] for key in ("items", "slices", "clusters")
    }
    rebuilt = rows(config)
    assert [row[:-1] for row in after[0]] == [row[:-1] for row in rebuilt[0]]
    assert after[1] == rebuilt[1]


def test_content_delta_refreshes_all_models_in_only_its_bucket(corpus, monkeypatch):
    config, store, chunks = corpus
    seed(config, store)
    store.upsert_content([replace(chunks[0], metadata={"title": "updated"})])
    prepare_source_index(store.content_store, max_rows=1)
    calls = []
    iterate = store.iter_bucket_item_representations

    def bounded(bucket, **kwargs):
        calls.append((bucket, kwargs["model_ids"]))
        yield from iterate(bucket, **kwargs)

    monkeypatch.setattr(store, "iter_bucket_item_representations", bounded)
    monkeypatch.setattr(store, "iter_item_representations", forbidden)
    actual = refresh_clusters(store, config)
    assert calls == [("forge:issues", ("a", "b"))]
    assert actual["slices_refreshed"] == 2 and actual["buckets_refreshed"] == 1
    assert actual["items"] == 4


def test_new_and_deleted_buckets_leave_explicit_empty_slice_coverage(corpus):
    config, store, chunks = corpus
    seed(config, store)
    added = [chunk("new:items", path) for path in ("a.md", "b.md")]
    store.upsert("a", added, np.array([[1, 0]] * 2, np.float32))
    prepare(store, 2)
    assert refresh_clusters(store, config)["items"] == 6
    for row in chunks[:2]:
        store.delete_by_file(row.source, row.file_path)
    actual = refresh_clusters(store, config)
    assert actual["items"] == 4 and actual["slices_refreshed"] == 2
    manifest = ClusterStore(config.clusters_db).slice_manifest()
    assert dict(manifest.bucket_items)["forge:issues"] == 0
    assert all(entry.items == entry.clusters == 0 for entry in manifest.slices
               if entry.bucket == "forge:issues")
    assert refresh_clusters(initialize_source_store(config), config)["maintenance_skipped"] == 1


@pytest.mark.parametrize("deleted", [False, True], ids=["unembedded-only", "deleted"])
def test_full_seed_covers_witnessed_zero_buckets_without_later_unrelated_reads(
    corpus, monkeypatch, deleted,
):
    config, store, chunks = corpus
    empty = [chunk("empty:items", f"{index}.md") for index in range(3)]
    store.upsert_content(empty)
    if deleted:
        for row in empty:
            store.delete_by_file(row.source, row.file_path)
    for _ in range(2):
        assert refresh_clusters(store, config, force=True)["items"] == 4
        manifest = ClusterStore(config.clusters_db).slice_manifest()
        assert dict(manifest.bucket_items)["empty:items"] == 0
        assert {entry.model_id for entry in manifest.slices if entry.bucket == "empty:items"} == {"a", "b"}
    prepare(store, 7)
    fresh = initialize_source_store(config)
    fresh.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    prepare_chunk_id_index(fresh.get_vector_table("a"), max_rows=1)
    iterate = fresh.iter_bucket_item_representations
    calls = []

    def bounded(bucket, **kwargs):
        assert bucket == "forge:issues" and kwargs["model_ids"] == ("a",)
        calls.append(bucket)
        yield from iterate(bucket, **kwargs)

    monkeypatch.setattr(fresh, "iter_bucket_item_representations", bounded)
    monkeypatch.setattr(fresh, "iter_item_representations", forbidden)
    monkeypatch.setattr(fresh.get_vector_table("b"), "get_vectors", forbidden)
    result = refresh_clusters(fresh, config)
    assert result["slices_refreshed"] == result["buckets_refreshed"] == 1
    assert result["items"] == 4 and calls == ["forge:issues"]
    assert dict(ClusterStore(config.clusters_db).slice_manifest().bucket_items)["empty:items"] == 0


@pytest.mark.parametrize("cause", ["policy", "coarse", "roster", "missing", "corrupt", "raw"])
def test_unqualified_invalidation_refuses_without_full_enumeration_or_publication(
    corpus, monkeypatch, cause,
):
    config, store, chunks = corpus
    seed(config, store)
    if cause == "policy":
        config = replace(config, cluster_min_size=3)
    elif cause == "coarse":
        domain = store._source_domain
        with domain.write(store.content_store.db):
            domain.effect("fixture-unannotated")
            store.get_vector_table("a")._get_or_create_table().update(
                where=f"chunk_id = '{chunks[0].chunk_id}'", values={"vector": [0.0, 1.0]},
            )
    elif cause == "roster":
        profile = ModelProfile("c", "fixture-c", dim=2, table_name="vectors_c")
        config.model_profiles["c"] = profile
        store = initialize_source_store(config)
    elif cause in ("missing", "corrupt"):
        receipt = store._source_domain.receipt()
        if cause == "missing":
            receipt["logical_projection"].pop("bucket_generations")
        else:
            receipt["logical_projection"]["bucket_generations"] = {"version": 99}
        store._source_domain.save(receipt)
    else:
        store.get_vector_table("a")._get_or_create_table().update(
            where=f"chunk_id = '{chunks[0].chunk_id}'", values={"vector": [0.0, 1.0]},
        )
    before = rows(config)
    monkeypatch.setattr(store, "iter_item_representations", forbidden)
    monkeypatch.setattr(store, "iter_bucket_item_representations", forbidden)
    monkeypatch.setattr(ClusterStore, "replace_all", forbidden)
    monkeypatch.setattr(ClusterStore, "replace_slices", forbidden)
    with pytest.raises((ClusterSliceRepairRequired, SourceLayoutError)):
        refresh_clusters(store, config)
    assert rows(config) == before


@pytest.mark.parametrize("prepared,budget", [(False, 2), (True, 1)])
def test_missing_preparation_or_exceeded_budget_does_not_fall_back(corpus, prepared, budget):
    config, store, chunks = corpus
    refresh_clusters(store, config, force=True)
    if prepared:
        prepare(store)
    before = rows(config)
    previous = ClusterStore(config.clusters_db).slice_manifest()
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    with pytest.raises(ScalarIndexPreparationRequired):
        refresh_clusters(store, replace(config, cluster_slice_row_budget=budget))
    assert rows(config) == before
    assert ClusterStore(config.clusters_db).slice_manifest() == previous


def test_ambiguous_mixed_union_refuses_then_explicit_full_repair_is_exact(corpus, monkeypatch):
    config, store, chunks = corpus
    added = chunk(path="unembedded.md")
    store.upsert_content([added])
    refresh_clusters(store, config, force=True)
    prepare(store, 5)
    config = replace(config, cluster_slice_row_budget=3)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    prepare_chunk_id_index(store.get_vector_table("a"), max_rows=1)
    before = rows(config)
    previous = ClusterStore(config.clusters_db).slice_manifest()
    with monkeypatch.context() as patch:
        patch.setattr(store.get_vector_table("b"), "get_vectors", forbidden)
        patch.setattr(store, "iter_item_representations", forbidden)
        with pytest.raises(ClusterSliceRepairRequired, match="item union"):
            refresh_clusters(store, config)
    assert rows(config) == before
    assert ClusterStore(config.clusters_db).slice_manifest() == previous
    assert refresh_clusters(store, config, force=True)["items"] == 4
    assert refresh_clusters(store, config)["maintenance_skipped"] == 1


@pytest.mark.parametrize("single_model", [False, True])
def test_unembedded_items_are_counted_without_inflating_eligible_union(corpus, single_model):
    config, store, chunks = corpus
    if single_model:
        config = replace(config, model_profiles={"a": config.model_profiles["a"]})
        store = initialize_source_store(config)
    store.upsert_content([chunk(path="unembedded.md")])
    refresh_clusters(store, config, force=True)
    prepare(store, 5)
    store.upsert_content([replace(chunks[0], metadata={"title": "fresh"})])
    prepare_source_index(store.content_store, max_rows=1)
    result = refresh_clusters(store, replace(config, cluster_slice_row_budget=3))
    assert result["items"] == 4
    assert result["slices_refreshed"] == (1 if single_model else 2)
    assert dict(ClusterStore(config.clusters_db).slice_manifest().bucket_items)["forge:issues"] == 2


def test_roster_budget_refuses_before_input_reads_and_full_seed_overflow_preserves_rows(
    corpus, monkeypatch,
):
    import agent_index.indexing.cluster_slice_pass as planner
    import agent_index.store.cluster_slice_coverage as coverage

    config, store, chunks = corpus
    previous = seed(config, store)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    before = rows(config)
    with monkeypatch.context() as patch:
        patch.setattr(planner, "MAX_CLUSTER_SLICES", 3)
        patch.setattr(store, "iter_bucket_item_representations", forbidden)
        with pytest.raises(ClusterSliceRepairRequired, match="roster"):
            refresh_clusters(store, config)
    with monkeypatch.context() as patch:
        patch.setattr(coverage, "MAX_CLUSTER_SLICES", 3)
        with pytest.raises(ValueError, match="manifest"):
            refresh_clusters(store, config, force=True)
    assert rows(config) == before
    assert ClusterStore(config.clusters_db).slice_manifest() == previous
    assert store._source_domain.receipt()["state"] == "clean"


def test_incomplete_selected_reader_or_late_source_change_cannot_publish(corpus, monkeypatch):
    config, store, chunks = corpus
    seed(config, store)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    prepare_chunk_id_index(store.get_vector_table("a"), max_rows=1)
    before = rows(config)
    previous = ClusterStore(config.clusters_db).slice_manifest()
    iterate = store.iter_bucket_item_representations

    def interrupted(*args, **kwargs):
        yield next(iterate(*args, **kwargs))
        raise OSError("fixture incomplete reader")

    with monkeypatch.context() as patch:
        patch.setattr(store, "iter_bucket_item_representations", interrupted)
        with pytest.raises(OSError, match="incomplete reader"):
            refresh_clusters(store, config)
    assert rows(config) == before
    assert ClusterStore(config.clusters_db).slice_manifest() == previous

    def changed(*args, **kwargs):
        yield from iterate(*args, **kwargs)
        store.upsert_vectors("a", [chunks[1]], np.array([[0, 1]], np.float32))

    monkeypatch.setattr(store, "iter_bucket_item_representations", changed)
    with pytest.raises(RuntimeError, match="Projection changed"):
        refresh_clusters(store, config)
    assert rows(config) == before
    assert ClusterStore(config.clusters_db).slice_manifest() == previous


def test_source_exclusion_is_held_during_computation_and_publication(corpus, monkeypatch):
    import agent_index.indexing.cluster_slice_pass as planner

    config, store, chunks = corpus
    seed(config, store)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    prepare_chunk_id_index(store.get_vector_table("a"), max_rows=1)
    build = planner.build_clusters
    publish = ClusterStore.replace_slices
    checks = []

    def competing_writer():
        with pytest.raises(SourceLayoutError, match="busy"):
            store.upsert_vectors("a", [chunks[1]], np.array([[0, 1]], np.float32))
        checks.append(True)

    def blocked_writer():
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(competing_writer).result(timeout=10)

    def compute(*args, **kwargs):
        blocked_writer()
        return build(*args, **kwargs)

    def commit(*args, **kwargs):
        blocked_writer()
        return publish(*args, **kwargs)

    monkeypatch.setattr(planner, "build_clusters", compute)
    monkeypatch.setattr(ClusterStore, "replace_slices", commit)
    assert refresh_clusters(store, config)["slices_refreshed"] == 1
    assert checks == [True, True]


def test_receipt_loss_at_sql_boundary_rolls_back_without_claiming_success(corpus):
    config, store, chunks = corpus
    seed(config, store)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    prepare_chunk_id_index(store.get_vector_table("a"), max_rows=1)
    before = rows(config)
    previous = ClusterStore(config.clusters_db).slice_manifest()
    with sqlite3.connect(config.clusters_db) as conn:
        conn.execute(
            "CREATE TRIGGER fail_slice BEFORE INSERT ON cluster_slice_coverage "
            "BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END",
        )
    counts = WorkCounts()
    token = _current.set(counts)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture interruption"):
            refresh_clusters(store, config)
    finally:
        _current.reset(token)
    assert rows(config) == before
    assert counts.cluster_pass_attempts == counts.cluster_pass_failures == 1
    assert counts.cluster_pass_completed == 0
    with sqlite3.connect(config.clusters_db) as conn:
        raw = conn.execute("SELECT manifest FROM cluster_slice_coverage").fetchone()[0]
        assert raw == previous.encode()
    assert store._source_domain.receipt()["state"] == "dirty"


def test_input_witnesses_isolate_bucket_and_model_and_keep_existing_global_signature(corpus):
    from agent_index.store.projection_snapshot import cluster_signature

    config, store, chunks = corpus
    with store._source_domain.operation(store.content_store.db):
        before = cluster_slice_inputs(store, config)
        assert before.signature == cluster_signature(store, config)
    store.upsert_vectors("a", [chunks[0]], np.array([[0, 1]], np.float32))
    with store._source_domain.operation(store.content_store.db):
        after = cluster_slice_inputs(store, config)
    assert after.basis == before.basis and after.signature != before.signature
    assert after.slice_signature("forge:issues", "a") != before.slice_signature("forge:issues", "a")
    for bucket, model in (("forge:issues", "b"), ("other:items", "a"), ("other:items", "b")):
        assert after.slice_signature(bucket, model) == before.slice_signature(bucket, model)
