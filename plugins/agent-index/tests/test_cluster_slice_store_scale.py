"""Explicit native SQLite cardinality proof, separate from required unit CI."""

from dataclasses import replace
from contextlib import closing

import pytest

from test_cluster_slice_store import clusters, manifest

from agent_index.store.cluster_slice_coverage import ClusterSliceCoverage
from agent_index.store.cluster_store import ClusterStore

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_native_partial_artifact_work_is_bounded_by_selected_rows(
    tmp_path, monkeypatch, size,
):
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    monkeypatch.setattr("agent_index.store.cluster_store.time.time", lambda: 10.0)
    store = ClusterStore(tmp_path / "clusters.db")
    template = clusters("unrelated")[0]
    unchanged = [
        replace(template, members=tuple(
            replace(member, file_path=f"{index}-{rank}.md")
            for rank, member in enumerate(template.members)
        ))
        for index in range(size)
    ]
    initial = manifest(entries=(
        ClusterSliceCoverage("git", "small", "1" * 64, 2, 1),
        ClusterSliceCoverage("unrelated", "small", "2" * 64, 2 * size, size),
    ), counts=(("git", 2), ("unrelated", size * 2)))
    store.replace_all([*clusters(), *unchanged],
                      coverage=initial.coverage, slice_manifest=initial)
    del unchanged
    expected = initial
    for iteration, signature in enumerate(("b", "c")):
        fresh = ClusterStore(store._db_path)
        assert fresh.slice_manifest() == expected
        updated = manifest(revision=signature, entries=(
            replace(expected.slices[0], signature=signature * 64), expected.slices[1],
        ), counts=expected.bucket_items)
        steps = 0
        connections = []
        connect = fresh._connect

        def progress():
            nonlocal steps
            steps += 1
            return 0

        def counted():
            conn = connect()
            conn.set_progress_handler(progress, 1)
            connections.append(conn)
            return conn

        monkeypatch.setattr(fresh, "_connect", counted)
        monkeypatch.setattr("agent_index.store.cluster_store.time.time", lambda: 20.0)
        assert fresh.replace_slices(
            clusters(prefix=f"new-{iteration}"), changed=(("git", "small"),),
            previous=expected, manifest=updated,
        ) == 1
        assert len(connections) == 1
        assert steps < 4096
        # Inspect the unrelated slice outside the measured mutation.
        with closing(connect()) as conn:
            assert tuple(conn.execute(
                "SELECT count(*), min(created_at), max(created_at), sum(size) "
                "FROM clusters WHERE bucket = 'unrelated'",
            ).fetchone()) == (size, 10.0, 10.0, size * 2)
            assert conn.execute(
                "SELECT count(*) FROM cluster_members m JOIN clusters c USING (cluster_id) "
                "WHERE c.bucket = 'unrelated'",
            ).fetchone()[0] == size * 2
        expected = updated
    assert ClusterStore(store._db_path).slice_manifest() == expected
