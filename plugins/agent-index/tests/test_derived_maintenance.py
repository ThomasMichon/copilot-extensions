"""Actual projection coverage, not task counts, governs derived maintenance."""

from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from lancedb.index import FTS

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.store.cluster_store import ClusterStore
from agent_index.store.clustering import ClusterEntry, build_clusters
from agent_index.store.content_store import ContentStore
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.projection_snapshot import ClusterCoverage, cluster_signature
from agent_index.store.source_layout import LAYOUT, initialize_source_store


def item(path: str) -> Chunk:
    return Chunk(f"sample text for {path}", path, "text", "markdown", 1, 1, "git:sample")


def reopen(config: IndexConfig, selected: bool) -> MultiModelStore:
    if selected:
        return initialize_source_store(config)
    store = MultiModelStore(config.lance_dir)
    for profile in config.model_profiles.values():
        store.register_model(profile)
    return store


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def corpus(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    selected = request.param
    if selected:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(
        data_dir=tmp_path / "data",
        model_profiles={"small": ModelProfile("small", "fixture-model", dim=2)},
        cluster_enabled=True,
    )
    config.ensure_dirs()
    store = reopen(config, selected)
    store.upsert("small", [item("one.md"), item("two.md")], np.array([[1, 0], [1, 0]], np.float32))
    return config, store, selected


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("covered no-op attempted expensive maintenance")


def test_cluster_artifact_reused_after_restart_without_item_enumeration(corpus, monkeypatch):
    config, store, selected = corpus
    first = engine._refresh_clusters(store, config)
    assert first is not None and first["clusters"] == 1
    fresh = reopen(config, selected)
    before = fresh.projection_snapshot()
    monkeypatch.setattr(fresh, "iter_item_representations", forbidden)
    second = engine._refresh_clusters(fresh, config)
    assert second == {
        "items": 2, "slices": 1, "clusters": 1, "elapsed_ms": 0, "maintenance_skipped": 1,
    }
    assert fresh.projection_snapshot() == before


def test_vector_only_mutation_and_policy_change_invalidate_cluster_coverage(corpus):
    config, store, _selected = corpus
    assert engine._refresh_clusters(store, config)["clusters"] == 1
    before = store.projection_snapshot()
    store.upsert_vectors("small", [item("two.md")], np.array([[0, 1]], np.float32))
    after = store.projection_snapshot()
    assert before != after
    updated = engine._refresh_clusters(store, config)
    assert updated is not None and "maintenance_skipped" not in updated
    assert updated["clusters"] == 0
    assert engine._refresh_clusters(store, config)["maintenance_skipped"] == 1
    changed_policy = replace(config, cluster_min_size=3)
    updated = engine._refresh_clusters(store, changed_policy)
    assert updated is not None and "maintenance_skipped" not in updated


def test_model_registration_invalidates_cluster_coverage(corpus):
    config, store, selected = corpus
    assert engine._refresh_clusters(store, config) is not None
    config.model_profiles["other"] = ModelProfile("other", "fixture-model-other", dim=2)
    fresh = reopen(config, selected)
    result = engine._refresh_clusters(fresh, config)
    assert result is not None and "maintenance_skipped" not in result
    assert engine._refresh_clusters(fresh, config)["maintenance_skipped"] == 1


def test_explicit_full_refresh_does_not_reuse_covered_clusters(corpus):
    config, store, _selected = corpus
    assert engine._refresh_clusters(store, config) is not None
    forced = engine._refresh_clusters(store, config, force=True)
    assert forced is not None and "maintenance_skipped" not in forced


def test_mutation_during_cluster_computation_never_publishes_new_coverage(corpus, monkeypatch):
    config, store, selected = corpus
    first = engine._refresh_clusters(store, config)
    assert first is not None
    old_key = cluster_signature(store, config)
    iterate = store.iter_item_representations

    def mutate(**kwargs):
        yield from iterate(**kwargs)
        store.upsert_vectors("small", [item("one.md")], np.array([[0, 1]], np.float32))

    monkeypatch.setattr(store, "iter_item_representations", mutate)
    if selected:
        with pytest.raises(RuntimeError, match="Projection changed"):
            engine._refresh_clusters(store, config, force=True)
    else:
        assert engine._refresh_clusters(store, config, force=True) is None
    artifact = ClusterStore(config.clusters_db)
    assert artifact.covered_stats(old_key) is not None
    assert artifact.covered_stats(cluster_signature(store, config)) is None


def test_cluster_rows_and_coverage_roll_back_together(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = ClusterStore(tmp_path / "clusters.db")
    clusters = build_clusters([
        ClusterEntry("git:sample", path, np.array([1, 0], np.float32), path)
        for path in ("one.md", "two.md")
    ], bucket="git", model_id="small", threshold=0.8, min_size=2)
    old = ClusterCoverage("a" * 64, 2, 1, 1)
    store.replace_all(clusters, coverage=old)
    with sqlite3.connect(tmp_path / "clusters.db") as conn:
        conn.execute(
            "CREATE TRIGGER fail_coverage BEFORE INSERT ON cluster_coverage "
            "BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END",
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture interruption"):
        store.replace_all([], coverage=ClusterCoverage("b" * 64, 0, 0, 0))
    assert store.covered_stats(old.signature) == old.stats()
    assert store.covered_stats("b" * 64) is None
    with sqlite3.connect(tmp_path / "clusters.db") as conn:
        assert conn.execute("SELECT count(*) FROM clusters").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM cluster_members").fetchone()[0] == 2
    store.replace_all([])
    assert store.covered_stats(old.signature) is None


def test_precoverage_writer_invalidates_newer_artifact_coverage(corpus):
    config, store, _selected = corpus
    assert engine._refresh_clusters(store, config)["clusters"] == 1
    signature = cluster_signature(store, config)
    artifact = ClusterStore(config.clusters_db)
    assert artifact.covered_stats(signature) is not None
    domain = store._source_domain
    with domain.write(store.content_store.db) if domain else nullcontext():
        if domain:
            domain.effect("cluster-replace")
        with sqlite3.connect(config.clusters_db) as conn:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM cluster_members")
            conn.execute("DELETE FROM clusters")
    assert artifact.covered_stats(signature) is None
    assert cluster_signature(store, config) == signature
    repaired = engine._refresh_clusters(store, config)
    assert repaired is not None and "maintenance_skipped" not in repaired
    assert repaired["clusters"] == 1


@pytest.mark.parametrize("sql", [
    "DELETE FROM clusters",
    "DELETE FROM cluster_members",
    "UPDATE clusters SET avg_score = 0.5",
    "UPDATE cluster_members SET score = 0.5",
    "INSERT INTO clusters SELECT cluster_id || '-copy', bucket, model_id, size, "
    "rep_source, rep_file_path, has_exact_dupes, avg_score, created_at FROM clusters",
    "INSERT INTO cluster_members SELECT cluster_id, source, file_path || '-copy', "
    "score, is_exact_dupe, member_rank FROM cluster_members",
])
def test_artifact_row_mutations_revoke_coverage_at_database_boundary(tmp_path, monkeypatch, sql):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    artifact = ClusterStore(tmp_path / "clusters.db")
    clusters = build_clusters([
        ClusterEntry("git:sample", path, np.array([1, 0], np.float32), path)
        for path in ("one.md", "two.md")
    ], bucket="git", model_id="small", threshold=0.8, min_size=2)
    coverage = ClusterCoverage("a" * 64, 2, 1, 1)
    artifact.replace_all(clusters, coverage=coverage)
    assert artifact.covered_stats(coverage.signature) is not None
    with sqlite3.connect(tmp_path / "clusters.db") as conn:
        conn.execute(sql)
    assert artifact.covered_stats(coverage.signature) is None


def test_native_fts_coverage_survives_restart_and_becomes_due_after_write(corpus, monkeypatch):
    config, store, selected = corpus
    content = store.content_store
    table = content._get_or_create_table()
    domain = content._source_domain
    if domain is None:
        table.create_index("content", config=FTS(), replace=True)
    else:
        with domain.write(content.db):
            domain.effect("fts-builder")
            table.create_index("content", config=FTS(), replace=True)
            domain.cover_fts(content)
    fresh = reopen(config, selected)
    content = fresh.content_store
    before = fresh.projection_snapshot()
    assert content.fts_current() is True
    monkeypatch.setattr("agent_index.store.content_fts_worker.subprocess.run", forbidden)
    content._run_fts_build(full=False)
    assert fresh.projection_snapshot() == before
    fresh.upsert_content([item("new.md")])
    assert content.fts_current() is False


def test_unknown_fts_coverage_never_becomes_current(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = ContentStore(tmp_path / "lance")
    store.upsert([item("one.md")])
    table = store._get_or_create_table()
    table.create_index("content", config=FTS(), replace=True)
    monkeypatch.setattr(type(table), "index_stats", lambda *args: {})
    assert store.fts_current() is False
    assert "coverage metadata is unavailable" in caplog.text


@pytest.mark.parametrize("count", [100, 10_000, 100_000])
@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
def test_native_covered_noop_has_bounded_metadata_and_zero_maintenance(
    tmp_path, monkeypatch, count, selected,
):
    if selected:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(
        data_dir=tmp_path / "data",
        model_profiles={"small": ModelProfile("small", "fixture-model", dim=2)},
        cluster_enabled=True,
    )
    config.ensure_dirs()
    store = reopen(config, selected)
    for start in range(0, count, 2000):
        store.upsert_content([item(f"{index}.md") for index in range(start, min(start + 2000, count))])
    content = store.content_store
    table = content._get_or_create_table()
    domain = content._source_domain
    if domain is None:
        table.create_index("content", config=FTS(), replace=True)
    else:
        with domain.write(content.db):
            domain.effect("fts-builder")
            table.create_index("content", config=FTS(), replace=True)
            domain.cover_fts(content)
    assert table.count_rows() == count
    # With no vector rows the clustering iterator has no eligible items.
    artifact = ClusterStore(config.clusters_db)
    artifact.replace_all([], coverage=ClusterCoverage(cluster_signature(store, config), 0, 0, 0))
    fresh = reopen(config, selected)
    before = fresh.projection_snapshot()
    def index_metadata():
        return [
            (index.index_uuid, index.index_version, index.num_indexed_rows,
             index.num_unindexed_rows, index.size_bytes)
            for index in table.list_indices()
        ]

    before_indices = index_metadata()
    before_artifact = config.clusters_db.stat()
    calls = {"metadata": 0}
    table_type = type(table)
    checkout_latest = table_type.checkout_latest

    def counted_checkout(handle):
        calls["metadata"] += 1
        return checkout_latest(handle)

    monkeypatch.setattr(table_type, "checkout_latest", counted_checkout)
    monkeypatch.setattr(table_type, "to_arrow", forbidden)
    monkeypatch.setattr(table_type, "to_pandas", forbidden)
    monkeypatch.setattr(table_type, "create_index", forbidden)
    monkeypatch.setattr(table_type, "optimize", forbidden)
    monkeypatch.setattr(fresh, "iter_item_representations", forbidden)
    monkeypatch.setattr(ClusterStore, "replace_all", forbidden)
    monkeypatch.setattr("agent_index.store.content_fts_worker.subprocess.run", forbidden)
    for _ in range(2):
        assert engine._refresh_clusters(fresh, config) == {
            "items": 0, "slices": 0, "clusters": 0, "elapsed_ms": 0, "maintenance_skipped": 1,
        }
        assert fresh.content_store.fts_current()
        fresh.content_store._run_fts_build(full=False)
    assert calls["metadata"] == (24 if selected else 12)
    assert fresh.projection_snapshot() == before
    assert index_metadata() == before_indices
    after_artifact = config.clusters_db.stat()
    assert (after_artifact.st_size, after_artifact.st_mtime_ns) == (
        before_artifact.st_size, before_artifact.st_mtime_ns,
    )


def test_metadata_only_content_write_invalidates_coverage_without_changing_search(corpus):
    config, store, _selected = corpus
    assert engine._refresh_clusters(store, config) is not None
    before = store.get_document("git:sample", "one.md")
    old_key = cluster_signature(store, config)
    store.upsert_content([replace(item("one.md"), metadata={"title": "updated"})])
    after = store.get_document("git:sample", "one.md")
    assert after.content == before.content
    assert after.content_hash == before.content_hash
    assert after.metadata == {"title": "updated"}
    assert cluster_signature(store, config) != old_key
    refreshed = engine._refresh_clusters(store, config)
    assert refreshed is not None and "maintenance_skipped" not in refreshed
    assert refreshed["clusters"] == 1


def test_failed_cluster_pass_keeps_old_coverage_and_retry_remains_due(corpus, monkeypatch):
    config, store, selected = corpus
    assert engine._refresh_clusters(store, config) is not None
    old_key = cluster_signature(store, config)
    store.upsert_vectors("small", [item("two.md")], np.array([[0, 1]], np.float32))
    new_key = cluster_signature(store, config)
    from agent_index.indexing import cluster_pass

    build = cluster_pass.build_clusters

    def fail(*args, **kwargs):
        raise RuntimeError("fixture failed cluster computation")

    monkeypatch.setattr(cluster_pass, "build_clusters", fail)
    if selected:
        with pytest.raises(RuntimeError, match="fixture failed"):
            engine._refresh_clusters(store, config)
    else:
        assert engine._refresh_clusters(store, config) is None
    artifact = ClusterStore(config.clusters_db)
    assert artifact.covered_stats(old_key) is not None
    assert artifact.covered_stats(new_key) is None
    monkeypatch.setattr(cluster_pass, "build_clusters", build)
    retried = engine._refresh_clusters(store, config)
    assert retried is not None and "maintenance_skipped" not in retried
    assert artifact.covered_stats(new_key)["maintenance_skipped"] == 1


@pytest.mark.parametrize("phase", ["items", "chunks", "vectors"])
def test_native_read_failure_cannot_publish_covered_partial_clusters(corpus, monkeypatch, phase):
    config, store, selected = corpus
    assert engine._refresh_clusters(store, config)["clusters"] == 1
    old_key = cluster_signature(store, config)
    store.upsert_vectors("small", [item("two.md")], np.array([[0, 1]], np.float32))
    new_key = cluster_signature(store, config)
    table = (
        store.get_vector_table("small")._get_or_create_table() if phase == "vectors"
        else store.content_store._get_or_create_table()
    )
    native_search = type(table).search
    calls = 0

    def fail_once(handle, *args, **kwargs):
        nonlocal calls
        if handle.name == table.name:
            calls += 1
            if calls == (2 if phase == "chunks" else 1):
                raise RuntimeError(f"fixture {phase} read failure")
        return native_search(handle, *args, **kwargs)

    monkeypatch.setattr(type(table), "search", fail_once)
    if selected:
        with pytest.raises(RuntimeError, match=f"fixture {phase} read failure"):
            engine._refresh_clusters(store, config)
    else:
        assert engine._refresh_clusters(store, config) is None
    artifact = ClusterStore(config.clusters_db)
    assert artifact.covered_stats(old_key) is not None
    assert artifact.covered_stats(new_key) is None
    retried = engine._refresh_clusters(store, config)
    assert retried is not None and "maintenance_skipped" not in retried
    assert artifact.covered_stats(new_key) is not None


@pytest.mark.parametrize("phase", ["items", "chunks"])
def test_strict_content_reads_refuse_truncated_results(tmp_path, monkeypatch, phase):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    content = ContentStore(tmp_path / "lance")
    content.upsert([item("one.md"), item("two.md")])
    table = content._get_or_create_table()
    native_search = type(table).search

    def truncated_search(handle, *args, **kwargs):
        query = native_search(handle, *args, **kwargs)
        native_to_list = query.to_list
        query.to_list = lambda: native_to_list()[:1]
        return query

    monkeypatch.setattr(type(table), "search", truncated_search)
    if phase == "items":
        with pytest.raises(RuntimeError, match="Incomplete content item"):
            content.distinct_items(strict=True)
    else:
        content.upsert([replace(item("one.md"), line_start=2, line_end=2)])
        with pytest.raises(RuntimeError, match="Incomplete file chunk"):
            content.get_chunks_for_file("git:sample", "one.md", strict=True)


def test_recreated_table_same_name_and_version_invalidates_local_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(
        data_dir=tmp_path / "data",
        model_profiles={"small": ModelProfile("small", "fixture-model", dim=2)},
        cluster_enabled=True,
    )
    config.ensure_dirs()
    store = reopen(config, False)
    assert engine._refresh_clusters(store, config) is not None
    before = store.projection_snapshot()
    owner = store.get_vector_table("small")
    table = owner._get_or_create_table()
    version = table.version
    schema = table.schema
    owner._db.drop_table(owner._table_name)
    owner._db.create_table(owner._table_name, schema=schema)
    fresh = reopen(config, False)
    assert fresh.get_vector_table("small")._get_or_create_table().version == version
    assert fresh.projection_snapshot() != before
    refreshed = engine._refresh_clusters(fresh, config)
    assert refreshed is not None and "maintenance_skipped" not in refreshed
