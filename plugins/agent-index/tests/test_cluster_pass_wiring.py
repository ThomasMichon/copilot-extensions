from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_index.indexing import engine as indexing_engine


def _config(tmp_path: Path, *, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        cluster_enabled=enabled,
        clusters_db=tmp_path / "clusters.db",
    )


def test_refresh_clusters_runs_pass(monkeypatch, tmp_path) -> None:
    import agent_index.indexing.cluster_pass as cluster_pass
    import agent_index.store.cluster_store as cluster_store

    calls = {}

    class FakeClusterStore:
        def __init__(self, path):
            calls["path"] = path

    def fake_run_clustering_pass(multi_store, store, config):
        calls["args"] = (multi_store, store, config)
        return {"items": 3, "slices": 1, "clusters": 1, "elapsed_ms": 5}

    monkeypatch.setattr(cluster_store, "ClusterStore", FakeClusterStore)
    monkeypatch.setattr(cluster_pass, "run_clustering_pass", fake_run_clustering_pass)

    multi_store = object()
    config = _config(tmp_path)
    stats = indexing_engine._refresh_clusters(multi_store, config)

    assert stats == {"items": 3, "slices": 1, "clusters": 1, "elapsed_ms": 5}
    assert calls["path"] == config.clusters_db
    assert calls["args"][0] is multi_store


def test_refresh_clusters_disabled_skips(monkeypatch, tmp_path) -> None:
    import agent_index.indexing.cluster_pass as cluster_pass

    def boom(*a, **k):
        raise AssertionError("clustering should not run when disabled")

    monkeypatch.setattr(cluster_pass, "run_clustering_pass", boom)

    assert indexing_engine._refresh_clusters(object(), _config(tmp_path, enabled=False)) is None


def test_refresh_clusters_failure_is_swallowed(monkeypatch, tmp_path) -> None:
    import agent_index.store.cluster_store as cluster_store

    class FakeClusterStore:
        def __init__(self, path):
            raise RuntimeError("no db")

    monkeypatch.setattr(cluster_store, "ClusterStore", FakeClusterStore)

    # A clustering failure must never fail the reindex it follows.
    assert indexing_engine._refresh_clusters(object(), _config(tmp_path)) is None
