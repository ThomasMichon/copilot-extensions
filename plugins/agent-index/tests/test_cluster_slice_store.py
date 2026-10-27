"""Partial artifact publication, old-writer invalidation and bounded receipts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import numpy as np
import pytest

from agent_index.index_config import IndexConfig
from agent_index.store.cluster_slice_coverage import (
    MAX_CLUSTER_COVERAGE_BYTES,
    MAX_CLUSTER_SLICES,
    ClusterSliceCoverage,
    ClusterSliceManifest,
)
from agent_index.store.cluster_store import ClusterStore
from agent_index.store.clustering import ClusterEntry, build_clusters
from agent_index.store.projection_snapshot import ClusterCoverage
from agent_index.store.source_layout import LAYOUT, initialize_source_store


def clusters(bucket="git", model="small", prefix="old"):
    return build_clusters(
        [ClusterEntry(f"{bucket}:sample", f"{prefix}-{index}.md",
                      np.array([1, 0], np.float32), "shared")
         for index in range(2)],
        bucket=bucket, model_id=model, threshold=0.8, min_size=2,
    )


def manifest(*, revision="a", entries=None, counts=None):
    if entries is None:
        entries = (
            ClusterSliceCoverage("git", "small", "1" * 64, 2, 1),
            ClusterSliceCoverage("git", "other", "2" * 64, 2, 1),
            ClusterSliceCoverage("issues", "small", "3" * 64, 2, 1),
        )
    if counts is None:
        counts = (("git", 2), ("issues", 2))
    return ClusterSliceManifest(
        "0" * 64,
        ClusterCoverage(revision * 64, sum(n for _bucket, n in counts),
                        sum(entry.items > 0 for entry in entries),
                        sum(entry.clusters for entry in entries)),
        entries, counts,
    )


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def artifact(request, tmp_path, monkeypatch):
    if request.param:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
        config = IndexConfig(data_dir=tmp_path)
        config.ensure_dirs()
        initialize_source_store(config)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = ClusterStore(tmp_path / "clusters.db")
    return store


def seed(store, snapshot=None):
    snapshot = snapshot or manifest()
    rows = [cluster for entry in snapshot.slices
            for cluster in (clusters(entry.bucket, entry.model_id) if entry.clusters else [])]
    store.replace_all(rows, coverage=snapshot.coverage, slice_manifest=snapshot)
    return snapshot


def changed(previous):
    return manifest(revision="b", entries=tuple(
        replace(entry, signature="4" * 64) if entry.key == ("git", "small") else entry
        for entry in previous.slices
    ))


def raw_rows(store):
    with sqlite3.connect(store._db_path) as conn:
        return (
            conn.execute("SELECT * FROM clusters ORDER BY cluster_id").fetchall(),
            conn.execute("SELECT * FROM cluster_members ORDER BY cluster_id, member_rank").fetchall(),
        )


def test_partial_replacement_preserves_unselected_rows_and_restart_receipt(artifact, monkeypatch):
    monkeypatch.setattr("agent_index.store.cluster_store.time.time", lambda: 10.0)
    previous = seed(artifact)
    before = raw_rows(artifact)
    after_manifest = changed(previous)
    monkeypatch.setattr("agent_index.store.cluster_store.time.time", lambda: 20.0)
    assert artifact.replace_slices(
        clusters(prefix="new"), changed=(("git", "small"),),
        previous=previous, manifest=after_manifest,
    ) == 1
    after = raw_rows(artifact)
    untouched = {row[0] for row in before[0] if row[1:3] != ("git", "small")}
    assert [row for row in before[0] if row[0] in untouched] == [
        row for row in after[0] if row[0] in untouched
    ]
    assert [row for row in before[1] if row[0] in untouched] == [
        row for row in after[1] if row[0] in untouched
    ]
    assert [row[-1] for row in after[0] if row[0] not in untouched] == [20.0]
    reopened = ClusterStore(artifact._db_path)
    assert reopened.slice_manifest() == after_manifest
    assert reopened.covered_stats(previous.coverage.signature) is None
    assert reopened.covered_stats(after_manifest.coverage.signature) == after_manifest.coverage.stats()


def test_empty_slice_and_deleted_bucket_are_retained_without_artifact_rows(artifact):
    previous = seed(artifact)
    empty = replace(previous.slices[2], items=0, clusters=0, signature="4" * 64)
    snapshot = manifest(revision="b", entries=(*previous.slices[:2], empty),
                        counts=(("git", 2), ("issues", 0)))
    assert artifact.replace_slices(
        [], changed=(empty.key,), previous=previous, manifest=snapshot,
    ) == 0
    assert artifact.list_clusters(bucket="issues") == []
    assert artifact.slice_manifest() == snapshot
    assert snapshot.coverage == ClusterCoverage("b" * 64, 2, 2, 2)


def test_new_bucket_and_slice_removal_are_explicit(artifact):
    previous = seed(artifact)
    added = ClusterSliceCoverage("files", "small", "4" * 64, 2, 1)
    snapshot = manifest(revision="b", entries=(*previous.slices[:2], added),
                        counts=(("git", 2), ("files", 2)))
    artifact.replace_slices(
        clusters("files"), changed=(("issues", "small"), ("files", "small")),
        previous=previous, manifest=snapshot,
    )
    assert artifact.slice_manifest() == snapshot
    assert artifact.list_clusters(bucket="issues") == []
    assert len(artifact.list_clusters(bucket="files")) == 1


@pytest.mark.parametrize("event", ["INSERT", "UPDATE", "DELETE"])
@pytest.mark.parametrize("table", ["clusters", "cluster_members", "cluster_coverage"])
def test_raw_artifact_or_global_coverage_mutation_revokes_slices(artifact, table, event):
    previous = seed(artifact)
    if event == "DELETE":
        sql = f"DELETE FROM {table}"
    elif event == "UPDATE":
        column = {"clusters": "avg_score", "cluster_members": "score",
                  "cluster_coverage": "items"}[table]
        sql = f"UPDATE {table} SET {column} = {column} + 1"
    elif table == "clusters":
        sql = (
            "INSERT INTO clusters SELECT cluster_id || '-copy', bucket, model_id, size, "
            "rep_source, rep_file_path, has_exact_dupes, avg_score, created_at FROM clusters"
        )
    elif table == "cluster_members":
        sql = (
            "INSERT INTO cluster_members SELECT cluster_id, source, file_path || '-copy', "
            "score, is_exact_dupe, member_rank FROM cluster_members"
        )
    else:
        sql = "INSERT OR REPLACE INTO cluster_coverage VALUES (1, 'other', 0, 0, 0)"
    with sqlite3.connect(artifact._db_path) as conn:
        conn.execute(sql)
    assert artifact.slice_manifest() is None
    before = raw_rows(artifact)
    with pytest.raises(RuntimeError, match="full repair is required"):
        artifact.replace_slices(clusters(prefix="new"), changed=(("git", "small"),),
                                previous=previous, manifest=changed(previous))
    assert raw_rows(artifact) == before


def test_old_empty_full_writer_revokes_empty_slice_receipts(artifact):
    empty = ClusterSliceCoverage("git", "small", "1" * 64, 2, 0)
    previous = seed(artifact, manifest(entries=(empty,), counts=(("git", 2),)))
    # The prior full writer knows only these three tables, even with no rows.
    with sqlite3.connect(artifact._db_path) as conn:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM cluster_members")
        conn.execute("DELETE FROM clusters")
        conn.execute("DELETE FROM cluster_coverage")
        conn.execute("INSERT INTO cluster_coverage VALUES (1, ?, 0, 0, 0)", ("b" * 64,))
    assert artifact.slice_manifest() is None
    assert artifact.covered_stats(previous.coverage.signature) is None
    assert artifact.covered_stats("b" * 64) == ClusterCoverage("b" * 64, 0, 0, 0).stats()


def test_full_writer_without_slice_optin_clears_previous_manifest(artifact):
    previous = seed(artifact)
    artifact.replace_all([], coverage=ClusterCoverage("b" * 64, 0, 0, 0))
    assert artifact.slice_manifest() is None
    assert artifact.covered_stats(previous.coverage.signature) is None


def test_concurrent_replacement_cannot_publish_from_stale_receipt(artifact):
    previous = seed(artifact)
    new = changed(previous)
    artifact.replace_slices(clusters(prefix="new"), changed=(("git", "small"),),
                            previous=previous, manifest=new)
    before = raw_rows(artifact)
    with pytest.raises(RuntimeError, match="coverage changed"):
        artifact.replace_slices(clusters(prefix="stale"), changed=(("git", "small"),),
                                previous=previous, manifest=new)
    assert raw_rows(artifact) == before


def test_partial_insert_cannot_replace_an_unselected_cluster_identity(artifact, monkeypatch):
    previous = seed(artifact)
    before = raw_rows(artifact)
    other_id = next(row[0] for row in before[0] if row[1] == "issues")
    monkeypatch.setattr("agent_index.store.cluster_store.cluster_id_for", lambda cluster: other_id)
    with pytest.raises(sqlite3.IntegrityError):
        artifact.replace_slices(clusters(prefix="new"), changed=(("git", "small"),),
                                previous=previous, manifest=changed(previous))
    assert raw_rows(artifact) == before


@pytest.mark.parametrize("failure_table", ["clusters", "cluster_members", "cluster_coverage",
                                         "cluster_slice_coverage"])
def test_partial_failure_rolls_rows_and_both_receipts_back(artifact, failure_table):
    previous = seed(artifact)
    before = raw_rows(artifact)
    with sqlite3.connect(artifact._db_path) as conn:
        conn.execute(
            f"CREATE TRIGGER fixture_fail BEFORE INSERT ON {failure_table} "
            "BEGIN SELECT RAISE(ABORT, 'fixture interruption'); END",
        )
    with pytest.raises(sqlite3.IntegrityError, match="fixture interruption"):
        artifact.replace_slices(clusters(prefix="new"), changed=(("git", "small"),),
                                previous=previous, manifest=changed(previous))
    assert raw_rows(artifact) == before
    # A source-aware failure deliberately leaves the source owner dirty, even
    # though SQLite rolled back; inspect receipts without bypassing that latch.
    with sqlite3.connect(artifact._db_path) as conn:
        assert conn.execute("SELECT manifest FROM cluster_slice_coverage").fetchone()[0] == previous.encode()
        assert conn.execute("SELECT signature FROM cluster_coverage").fetchone()[0] == previous.coverage.signature


def test_partial_api_does_not_read_or_rewrite_unselected_artifact_rows(artifact, monkeypatch):
    previous = seed(artifact)
    statements = []
    connect = artifact._connect

    def traced():
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(artifact, "_connect", traced)
    artifact.replace_slices(clusters(prefix="new"), changed=(("git", "small"),),
                            previous=previous, manifest=changed(previous))
    assert not any("SELECT * FROM clusters" in sql for sql in statements)
    assert not any("SELECT * FROM cluster_members" in sql for sql in statements)
    assert any("WHERE bucket = 'git' AND model_id = 'small'" in sql for sql in statements)
    assert not any("WHERE bucket = 'issues'" in sql or "model_id = 'other'" in sql
                   for sql in statements)


@pytest.mark.parametrize("payload", [
    "{}", '{"version":2}', "null", "[]",
    json.dumps({"version": 1, "basis": "a" * 64, "coverage": None,
                "slices": [], "bucket_items": []}),
    "[" * 2000 + "0" + "]" * 2000,
    " " * (MAX_CLUSTER_COVERAGE_BYTES + 1),
])
def test_invalid_or_future_manifest_requests_explicit_full_repair(artifact, payload, caplog):
    seed(artifact)
    with sqlite3.connect(artifact._db_path) as conn:
        conn.execute("UPDATE cluster_slice_coverage SET manifest = ?", (payload,))
    assert artifact.slice_manifest() is None
    assert "full repair is required" in caplog.text


def test_absent_optional_manifest_keeps_legacy_full_coverage(artifact):
    coverage = ClusterCoverage("a" * 64, 0, 0, 0)
    artifact.replace_all([], coverage=coverage)
    assert artifact.slice_manifest() is None
    assert artifact.covered_stats(coverage.signature) == coverage.stats()


def test_manifest_roundtrip_and_unique_item_counts_across_models():
    snapshot = manifest(entries=(
        ClusterSliceCoverage("git", "small", "1" * 64, 2, 1),
        ClusterSliceCoverage("git", "other", "2" * 64, 2, 1),
    ), counts=(("git", 3),))
    assert snapshot.coverage.items == 3
    assert ClusterSliceManifest.decode(snapshot.encode()) == snapshot
    with pytest.raises(ValueError, match="item union"):
        manifest(entries=snapshot.slices, counts=(("git", 5),))


def test_manifest_slice_and_byte_budgets_are_hard_limits():
    entries = tuple(ClusterSliceCoverage("git", str(n), "1" * 64, 0, 0)
                    for n in range(MAX_CLUSTER_SLICES))
    snapshot = manifest(entries=entries, counts=(("git", 0),))
    assert ClusterSliceManifest.decode(snapshot.encode()) == replace(
        snapshot, slices=tuple(sorted(entries, key=lambda entry: entry.key)),
    )
    with pytest.raises(ValueError, match="Invalid cluster slice manifest"):
        manifest(entries=(*entries, replace(entries[0], model_id="extra")), counts=(("git", 0),))
    with pytest.raises(ValueError, match="byte budget"):
        manifest(entries=(replace(entries[0], model_id="x" * MAX_CLUSTER_COVERAGE_BYTES),),
                 counts=(("git", 0),))


@pytest.mark.parametrize("change_kind", ["basis", "unselected", "bucket_count", "unknown", "empty",
                                       "duplicate", "wrong_rows"])
def test_invalid_partial_publication_refuses_before_artifact_changes(artifact, change_kind):
    previous = seed(artifact)
    new = changed(previous)
    selected = (("git", "small"),)
    rows = clusters(prefix="new")
    if change_kind == "basis":
        new = replace(new, basis="5" * 64)
    elif change_kind == "unselected":
        new = replace(new, slices=(*new.slices[:2], replace(new.slices[2], signature="5" * 64)))
    elif change_kind == "bucket_count":
        new = manifest(revision="b", entries=(*new.slices[:2], replace(new.slices[2], items=3)),
                       counts=(("git", 2), ("issues", 3)))
    elif change_kind == "unknown":
        selected = (("unknown", "small"),)
    elif change_kind == "empty":
        selected = ()
    elif change_kind == "duplicate":
        selected = (selected[0], selected[0])
    else:
        rows = clusters("issues", prefix="new")
    before = raw_rows(artifact)
    with pytest.raises(ValueError):
        artifact.replace_slices(rows, changed=selected, previous=previous, manifest=new)
    assert raw_rows(artifact) == before
