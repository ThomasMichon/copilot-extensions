"""Explicit native ordinary-round composition, not a full-seed complexity claim."""

from __future__ import annotations

import re
import sqlite3

import numpy as np
import pytest

from test_cluster_slice_pass import chunk, forbidden, prepare

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.derived_maintenance import refresh_clusters
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.cluster_slice_inputs import cluster_slice_inputs, slice_manifest
from agent_index.store.cluster_store import ClusterStore, cluster_id_for
from agent_index.store.clustering import Cluster, ClusterEntry, ClusterMember, build_clusters
from agent_index.store.source_bucket_index import bucket_predicate
from agent_index.store.source_layout import LAYOUT, initialize_source_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_native_planner_reads_and_publishes_only_changed_bucket_model_at_scale(
    tmp_path, monkeypatch, size,
):
    from lancedb.query import LanceEmptyQueryBuilder

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path, cluster_enabled=True, cluster_slice_row_budget=2,
        model_profiles={
            model: ModelProfile(model, f"fixture-{model}", dim=2, table_name=f"vectors_{model}")
            for model in ("a", "b")
        },
    )
    store = initialize_source_store(config)
    unrelated = [chunk("other:items", f"{index}.md") for index in range(size)]
    selected = [chunk(path=path) for path in ("one.md", "two.md")]
    store.upsert_content([*unrelated, *selected])
    store.upsert_vectors("b", [*unrelated, *selected], np.array([[1, 0]] * (size + 2), np.float32))
    store.upsert_vectors("a", selected, np.array([[1, 0]] * 2, np.float32))
    prepare(store, size + 2)

    # Known identical-vector fixture: its one large component is constructed without
    # running the explicitly global, quadratic seed algorithm at this cardinality.
    large = Cluster("other:items", "b", tuple(
        ClusterMember(row.source, row.file_path, 1.0, False) for row in unrelated
    ), False)
    clusters = [large]
    for model in ("a", "b"):
        clusters.extend(build_clusters([
            ClusterEntry(row.source, row.file_path, np.array([1, 0], np.float32), row.content_hash)
            for row in selected
        ], bucket="forge:issues", model_id=model, threshold=0.92))
    with store._source_domain.operation(store.content_store.db):
        inputs = cluster_slice_inputs(store, config)
        manifest = slice_manifest(inputs, {
            ("forge:issues", "a"): (2, 1), ("forge:issues", "b"): (2, 1),
            ("other:items", "a"): (0, 0), ("other:items", "b"): (size, 1),
        }, {"forge:issues": 2, "other:items": size})
        ClusterStore(config.clusters_db).replace_all(
            clusters, coverage=manifest.coverage, slice_manifest=manifest,
        )
    unrelated_id = cluster_id_for(large)
    with sqlite3.connect(config.clusters_db) as conn:
        expected_cluster = conn.execute(
            "SELECT * FROM clusters WHERE cluster_id = ?", (unrelated_id,),
        ).fetchone()
        expected_members = conn.execute(
            "SELECT * FROM cluster_members WHERE cluster_id = ? ORDER BY member_rank", (unrelated_id,),
        ).fetchall()
    del unrelated, large, clusters

    for iteration, vector in enumerate(([0, 1], [1, 0])):
        fresh = initialize_source_store(config)
        fresh.upsert_vectors("a", selected[:1], np.array([vector], np.float32))
        prepare_chunk_id_index(fresh.get_vector_table("a"), max_rows=1)
        content = fresh.content_store._get_or_create_table()
        vectors = fresh.get_vector_table("a")._get_or_create_table()
        predicate = bucket_predicate("forge:issues")
        ids = ", ".join(f"'{row.chunk_id}'" for row in selected)
        for _ in range(2):
            for query in (
                content.search().where(predicate).limit(3),
                vectors.search().where(f"chunk_id IN ({ids})").limit(3).select(["chunk_id", "vector"]),
            ):
                plan = query.analyze_plan()
                assert "ScalarIndexQuery" in plan, plan
                assert sum(map(int, re.findall(r"\brows_scanned=(\d+)", plan))) == 2, plan
                assert sum(map(int, re.findall(r"\biops=(\d+)", plan))) < 64, plan
        observed = {"canonical": 0, "vectors": 0, "steps": 0}
        batches = LanceEmptyQueryBuilder.to_batches
        get_vectors = fresh.get_vector_table("a").get_vectors
        connect = ClusterStore._connect

        def scoped_batches(query, **kwargs):
            if query._where == predicate:
                observed["canonical"] += 1
            else:
                assert query._where.startswith("chunk_id IN (")
            return batches(query, **kwargs)

        def scoped_vectors(ids, **kwargs):
            assert set(ids) <= {row.chunk_id for row in selected}
            assert kwargs == {"bounded": True}
            observed["vectors"] += len(ids)
            return get_vectors(ids, **kwargs)

        def progress():
            observed["steps"] += 1
            return 0

        def counted_connect(owner):
            conn = connect(owner)
            conn.set_progress_handler(progress, 1)
            return conn

        with monkeypatch.context() as patch:
            patch.setattr(LanceEmptyQueryBuilder, "to_batches", scoped_batches)
            patch.setattr(fresh.get_vector_table("a"), "get_vectors", scoped_vectors)
            patch.setattr(fresh.get_vector_table("b"), "get_vectors", forbidden)
            patch.setattr(fresh, "iter_item_representations", forbidden)
            patch.setattr(fresh.content_store, "distinct_items", forbidden)
            patch.setattr(fresh.content_store, "get_chunks_for_file", forbidden)
            patch.setattr(ClusterStore, "replace_all", forbidden)
            patch.setattr(ClusterStore, "_connect", counted_connect)
            result = refresh_clusters(fresh, config)
            assert result["items"] == size + 2
            assert result["clusters"] == 2 + iteration
            assert result["slices_refreshed"] == result["buckets_refreshed"] == 1
            assert observed["canonical"] == 1 and observed["vectors"] == 2
            assert observed["steps"] < 4096
            assert refresh_clusters(fresh, config)["maintenance_skipped"] == 1
            assert observed["canonical"] == 1 and observed["vectors"] == 2
        with sqlite3.connect(config.clusters_db) as conn:
            assert conn.execute(
                "SELECT * FROM clusters WHERE cluster_id = ?", (unrelated_id,),
            ).fetchone() == expected_cluster
            assert conn.execute(
                "SELECT * FROM cluster_members WHERE cluster_id = ? ORDER BY member_rank", (unrelated_id,),
            ).fetchall() == expected_members
