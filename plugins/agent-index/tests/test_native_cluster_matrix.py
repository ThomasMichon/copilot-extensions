"""Opt-in ordinary engine/cluster composition, excluding the global seed algorithm."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace

import numpy as np
import pytest
from test_native_delta_matrix import (
    FixtureClient, RoundFixture, SOURCE, assert_reference, measure_round, no_lifecycle,
)

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.derived_maintenance import refresh_clusters
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.store.cluster_slice_inputs import cluster_slice_inputs, slice_manifest
from agent_index.store.cluster_store import ClusterStore, cluster_id_for
from agent_index.store.clustering import Cluster, ClusterMember, build_clusters, ClusterEntry
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import prepare_index
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.store.source_bucket_index import prepare_source_index
from agent_index.store.source_layout import LAYOUT

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]
OTHER = "other:repo"


class ClusterClient(FixtureClient):
    def embed_texts(self, texts):
        return np.array([
            [0, 1] if "orthogonal" in text else [len(text), 1] for text in texts
        ], dtype=np.float32)


class ClusterFixture(RoundFixture):
    def run(self):
        result = engine.run_reindex(
            source=SOURCE, config=self.config,
            source_specs=[engine.SourceSpec(SOURCE, type="sample")],
            embedding_runtime=EmbeddingRuntime(
                lambda _: ClusterClient(), no_lifecycle, no_lifecycle, manage_engines=False,
            ),
        )
        assert "sources_failed" not in result, result
        return result


def artifact_rows(config, bucket):
    with sqlite3.connect(config.clusters_db) as conn:
        return (
            conn.execute(
                "SELECT * FROM clusters WHERE bucket = ? ORDER BY cluster_id", (bucket,),
            ).fetchall(),
            conn.execute(
                "SELECT m.* FROM cluster_members m JOIN clusters c USING (cluster_id) "
                "WHERE c.bucket = ? ORDER BY cluster_id, member_rank", (bucket,),
            ).fetchall(),
        )


def seed_artifact(fixture, size):
    """Construct the known large component without the quadratic full-seed pass."""
    store = fixture.store()
    unrelated = [
        Chunk("Unchanged fixture content.", f"old-{index}.md", "text", "markdown", 1, 1, OTHER)
        for index in range(size)
    ]
    store.upsert_content(unrelated)
    for model in fixture.config.model_profiles:
        store.upsert_vectors(model, unrelated, np.ones((size, 2), dtype=np.float32))
    for owner in [store.content_store, *store._vector_tables.values()]:
        for column in (("chunk_id", "file_path") if owner is store.content_store else ("chunk_id",)):
            prepare_index(owner, column=column, max_rows=size + 2)
    prepare_source_index(store.content_store, max_rows=size + 2)
    store.ensure_fts_index()
    clusters = []
    counts = {}
    with store._source_domain.operation(store.content_store.db):
        for model in fixture.config.model_profiles:
            large = Cluster(OTHER, model, tuple(
                ClusterMember(row.source, row.file_path, 1.0, True) for row in unrelated
            ), True)
            clusters.append(large)
            entries = [
                ClusterEntry(rep.source, rep.file_path, rep.centroids[model], rep.content_hash)
                for rep in store.iter_bucket_item_representations(
                    SOURCE, model_ids=(model,), row_budget=2,
                )
            ]
            selected = build_clusters(
                entries, bucket=SOURCE, model_id=model,
                threshold=fixture.config.cluster_threshold_for(SOURCE),
                min_size=fixture.config.cluster_min_size,
            )
            clusters.extend(selected)
            counts[(SOURCE, model)] = (2, len(selected))
            counts[(OTHER, model)] = (size, 1)
        manifest = slice_manifest(
            cluster_slice_inputs(store, fixture.config), counts, {SOURCE: 2, OTHER: size},
        )
        ClusterStore(fixture.config.clusters_db).replace_all(
            clusters, coverage=manifest.coverage, slice_manifest=manifest,
        )
    return unrelated, {cluster_id_for(cluster) for cluster in clusters if cluster.bucket == OTHER}


@pytest.mark.skipif(sys.platform != "linux", reason="native process I/O qualification is Linux-only")
def test_cluster_enabled_engine_rounds_at_three_corpus_sizes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    first = FileEntry("active.md", "Useful original text.", "markdown", SOURCE)
    second = replace(first, path="second.md", content="Second useful item.")
    changed = replace(first, content="An orthogonal changed item.")
    metadata = replace(changed, metadata={"state": "closed"})
    profiles = {
        model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
        for model in ("a", "b")
    }
    baselines = {}
    measurements = {}
    for size in (100, 10_000, 100_000):
        fixture = ClusterFixture(IndexConfig(
            data_dir=tmp_path / str(size), model_profiles=profiles,
            cluster_enabled=False, cluster_slice_row_budget=2,
        ), True, SourceDelta(upserts=[first, second], commit="initial"))
        monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
        fixture.run()
        unrelated, unchanged_cluster_ids = seed_artifact(fixture, size)
        fixture.config = replace(fixture.config, cluster_enabled=True)
        untouched = artifact_rows(fixture.config, OTHER)
        assert {row[0] for row in untouched[0]} == unchanged_cluster_ids
        assert len(untouched[1]) == size * len(profiles)
        cases = [
            ("noop", SourceDelta(commit="initial"), [first, second], set()),
            ("edit", SourceDelta(upserts=[changed], commit="edit"), [changed, second], set()),
            ("metadata", SourceDelta(upserts=[metadata], commit="metadata"), [metadata, second], set()),
            ("replay", SourceDelta(upserts=[metadata], commit="metadata"), [metadata, second], set()),
            ("delete", SourceDelta(
                deletions=[DeletedFile(SOURCE, first.path)], commit="delete",
            ), [second], {first.path}),
        ]
        measurements[size] = {}
        for name, delta, expected, deleted in cases:
            fixture.delta = delta
            observed = {"canonical": 0, "rows": 0, "steps": 0}
            iterate = MultiModelStore.iter_bucket_item_representations
            connect = ClusterStore._connect

            def bounded(owner, bucket, **kwargs):
                assert bucket == SOURCE
                assert kwargs == {
                    "model_ids": ("a", "b"), "row_budget": 2, "include_unembedded": True,
                }
                observed["canonical"] += 1
                for rep in iterate(owner, bucket, **kwargs):
                    observed["rows"] += 1
                    yield rep

            def progress():
                observed["steps"] += 1
                return 0

            def counted_connect(owner):
                conn = connect(owner)
                conn.set_progress_handler(progress, 1)
                return conn

            def forbidden(*_args, **_kwargs):
                raise AssertionError("ordinary engine round attempted a global cluster sweep")

            with monkeypatch.context() as patch:
                patch.setattr(MultiModelStore, "iter_bucket_item_representations", bounded)
                patch.setattr(MultiModelStore, "iter_item_representations", forbidden)
                patch.setattr(ClusterStore, "replace_all", forbidden)
                patch.setattr(ClusterStore, "_connect", counted_connect)
                measurement = measure_round(fixture.run, patch)
            measurements[size][name] = measurement
            counts = measurement["counts"]
            no_change = name in {"noop", "replay"}
            assert counts["cluster_cache_hits"] == int(no_change)
            assert counts["cluster_pass_attempts"] == counts["cluster_pass_completed"] == int(not no_change)
            assert counts["cluster_pass_failures"] == 0
            assert observed["canonical"] == int(not no_change)
            assert observed["rows"] == (0 if no_change else len(expected))
            assert observed["steps"] < 4096
            if name in {"noop", "metadata", "replay", "delete"}:
                assert all(model["chunks_embedded"] == model["vector_rows_upserted"] == 0
                           for model in counts["models"].values())
            if no_change:
                assert counts["fts_ensure_attempts"] == counts["content_rows_upserted"] == 0
            if size == 100:
                baselines[name] = measurement
            else:
                assert counts == baselines[name]["counts"], (size, name, measurement)
            with capsys.disabled():
                print("NATIVE_CLUSTER_WORK " + json.dumps({
                    "size": size, "case": name, "native": measurement["native"],
                    "io": measurement["io"], "cluster": observed,
                }, sort_keys=True))

            assert artifact_rows(fixture.config, OTHER) == untouched
            reference = ClusterFixture(replace(
                fixture.config, data_dir=tmp_path / f"reference-{size}-{name}", cluster_enabled=False,
            ), True, SourceDelta(upserts=expected, commit="rebuilt"))
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: reference)
            reference.run()
            refresh_clusters(reference.store(), replace(reference.config, cluster_enabled=True), force=True)
            monkeypatch.setattr("agent_index.sources.get_connector", lambda *_a, **_k: fixture)
            store = fixture.store()
            assert_reference(store, reference.store(), expected, deleted)
            actual_rows = artifact_rows(fixture.config, SOURCE)
            expected_rows = artifact_rows(reference.config, SOURCE)
            assert len(expected_rows[0]) == (len(profiles) if name == "noop" else 0)
            assert len(expected_rows[1]) == (2 * len(profiles) if name == "noop" else 0)
            assert [row[:-1] for row in actual_rows[0]] == [row[:-1] for row in expected_rows[0]]
            assert actual_rows[1] == expected_rows[1]
            assert store.content_store.source_counts() == {SOURCE: len(expected), OTHER: size}
            ids = {unrelated[0].chunk_id, unrelated[-1].chunk_id}
            assert store.content_store.get_by_ids(list(ids)).keys() == ids
            for model in profiles:
                vectors = store.get_vector_table(model).get_vectors(list(ids), strict=True)
                assert vectors.keys() == ids
                for vector in vectors.values():
                    np.testing.assert_array_equal(vector, [1, 1])

    for size, rounds in measurements.items():
        for name, measurement in rounds.items():
            assert measurement["native"]["rows_scanned"] < 64, (size, name, measurement)
            assert measurement["native"]["read_iops"] < 64, (size, name, measurement)
            assert measurement["io"]["rchar"] < (
                baselines[name]["io"]["rchar"] + 2 * 1024 * 1024
            ), (size, name, measurement)
            assert measurement["io"]["wchar"] < (
                baselines[name]["io"]["wchar"] + 512 * 1024
            ), (size, name, measurement)
