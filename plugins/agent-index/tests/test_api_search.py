from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_index.server import build_app


def _hit() -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id="chunk-1",
        score=0.5,
        file_path="docs/example.md",
        line_start=10,
        line_end=12,
        source="git:repo",
        chunk_type="markdown",
        language="markdown",
        content="example content",
    )


def test_search_endpoint_returns_hit_shape(monkeypatch) -> None:
    from agent_index.search import engine as search_engine

    calls = []

    class FakeEngine:
        def search(self, query: str, **kwargs):
            calls.append((query, kwargs))
            return [_hit()]

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    response = TestClient(build_app()).get(
        "/search",
        params={
            "q": "needle",
            "source": "git:repo",
            "language": "markdown",
            "repo": "repo",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == [
        (
            "needle",
            {"limit": 5, "source": "git:repo", "language": "markdown", "repo": "repo"},
        )
    ]
    assert payload == {
        "query": "needle",
        "available": True,
        "hits": [
            {
                "id": "chunk-1",
                "chunk_id": "chunk-1",
                "score": 0.5,
                "file_path": "docs/example.md",
                "line_start": 10,
                "line_end": 12,
                "source": "git:repo",
                "chunk_type": "markdown",
                "language": "markdown",
                "content": "example content",
            }
        ],
    }


def test_similar_endpoint_returns_hit_shape(monkeypatch) -> None:
    from agent_index.search import engine as search_engine

    calls = []

    class FakeEngine:
        def find_similar(self, chunk_id: str, **kwargs):
            calls.append((chunk_id, kwargs))
            return [_hit()]

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    response = TestClient(build_app()).get("/similar", params={"id": "chunk-1", "limit": 2})

    assert response.status_code == 200
    payload = response.json()
    assert calls == [("chunk-1", {"limit": 2, "source": None})]
    assert payload["id"] == "chunk-1"
    assert payload["available"] is True
    assert payload["hits"][0]["chunk_id"] == "chunk-1"


def test_search_endpoint_degrades_cleanly(monkeypatch) -> None:
    from agent_index.search import engine as search_engine

    def raise_unavailable():
        raise RuntimeError("no durable index")

    monkeypatch.setattr(search_engine, "create_search_engine", raise_unavailable)

    response = TestClient(build_app()).get("/search", params={"q": "needle"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["hits"] == []
    assert payload["error"] == "RuntimeError: no durable index"
