from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_index.server import build_app


def _stored_cluster() -> SimpleNamespace:
    members = (
        SimpleNamespace(
            source="git:repo", file_path="a.py", score=1.0, is_exact_dupe=False
        ),
        SimpleNamespace(
            source="git:repo", file_path="b.py", score=0.95, is_exact_dupe=True
        ),
    )
    return SimpleNamespace(
        cluster_id="cid-1",
        bucket="git",
        model_id="code",
        size=2,
        rep_source="git:repo",
        rep_file_path="a.py",
        has_exact_dupes=True,
        avg_score=0.95,
        created_at=0.0,
        members=members,
    )


def test_clusters_endpoint_returns_shape(monkeypatch) -> None:
    calls = {}

    class FakeStore:
        def __init__(self, path):
            calls["path"] = path

        def list_clusters(self, **kwargs):
            calls["kwargs"] = kwargs
            return [_stored_cluster()]

    import agent_index.store.cluster_store as cluster_store

    monkeypatch.setattr(cluster_store, "ClusterStore", FakeStore)

    response = TestClient(build_app()).get(
        "/clusters",
        params={
            "source": "git:repo",
            "model": "code",
            "exact_dupes_only": True,
            "limit": 10,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    # source collapses to its bucket; exact_dupes_only maps to has_exact_dupes
    assert calls["kwargs"]["bucket"] == "git:repo"
    assert calls["kwargs"]["model_id"] == "code"
    assert calls["kwargs"]["has_exact_dupes"] is True
    assert calls["kwargs"]["limit"] == 10
    assert payload["available"] is True
    assert payload["count"] == 1
    cluster = payload["clusters"][0]
    assert cluster["cluster_id"] == "cid-1"
    assert cluster["bucket"] == "git"
    assert cluster["model_id"] == "code"
    assert cluster["size"] == 2
    assert cluster["has_exact_dupes"] is True
    assert cluster["representative"]["file_path"] == "a.py"
    assert len(cluster["members"]) == 2
    assert cluster["members"][1]["is_exact_dupe"] is True


def test_clusters_endpoint_no_filters(monkeypatch) -> None:
    class FakeStore:
        def __init__(self, path):
            pass

        def list_clusters(self, **kwargs):
            assert kwargs["bucket"] is None
            assert kwargs["has_exact_dupes"] is None
            return []

    import agent_index.store.cluster_store as cluster_store

    monkeypatch.setattr(cluster_store, "ClusterStore", FakeStore)

    response = TestClient(build_app()).get("/clusters")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["count"] == 0
    assert payload["clusters"] == []


def test_clusters_endpoint_degrades_cleanly(monkeypatch) -> None:
    class FakeStore:
        def __init__(self, path):
            raise RuntimeError("no cluster db")

    import agent_index.store.cluster_store as cluster_store

    monkeypatch.setattr(cluster_store, "ClusterStore", FakeStore)

    response = TestClient(build_app()).get("/clusters")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["clusters"] == []
    assert "no cluster db" in payload["error"]
