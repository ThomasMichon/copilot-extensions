from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_index.server import build_app


def test_drain_health_and_search(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    from agent_index.search import engine as search_engine

    class FakeEngine:
        def search(self, *_args, **_kwargs):
            return [SimpleNamespace(chunk_id="c1", score=1.0, content="hit")]

        def find_similar(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    with TestClient(build_app()) as client:
        drained = client.post("/drain", json={"timeout": 1, "poll": 0.05})
        assert drained.status_code == 200
        assert drained.json()["drained"] is True

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "draining"

        search = client.get("/search", params={"q": "needle"})
        assert search.status_code == 200
        assert search.json()["available"] is True

        undrained = client.post("/undrain")
        assert undrained.status_code == 200
        assert client.get("/health").json()["status"] == "ok"
