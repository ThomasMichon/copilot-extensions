from __future__ import annotations

import asyncio

import pytest

import agent_index.mcp_app as mcp_app

_TOOLS = {
    "agent_index_search",
    "agent_index_find_similar",
    "agent_index_clusters",
    "agent_index_status",
    "agent_index_reindex",
}


def test_all_tools_registered() -> None:
    tools = asyncio.run(mcp_app.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert _TOOLS <= names


def test_search_delegates_and_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        assert path == "/search"
        assert params["q"] == "hello world"
        assert params["limit"] == 5
        assert params["source"] == "git:repo"
        return {
            "available": True,
            "hits": [
                {
                    "chunk_id": "c1",
                    "score": 0.912,
                    "file_path": "src/app.py",
                    "line_start": 10,
                    "line_end": 20,
                    "language": "python",
                    "chunk_type": "function",
                    "source": "git:repo",
                    "content": "def handler(): ...",
                }
            ],
        }

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    out = asyncio.run(mcp_app.agent_index_search("hello world", limit=5, source="git:repo"))
    assert "Found 1 result(s) for: hello world" in out
    assert "src/app.py (L10-20)" in out
    assert "score=0.912" in out
    assert "id=c1" in out
    assert "def handler()" in out


def test_search_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        return {"available": True, "hits": []}

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    assert "No results for: zzz" in asyncio.run(mcp_app.agent_index_search("zzz"))


def test_search_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        return {"available": False, "error": "engine cold"}

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    out = asyncio.run(mcp_app.agent_index_search("x"))
    assert "unavailable" in out
    assert "engine cold" in out


def test_find_similar_delegates_and_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        assert path == "/similar"
        assert params["id"] == "c1"
        return {
            "available": True,
            "hits": [
                {
                    "chunk_id": "c2",
                    "score": 0.8,
                    "file_path": "docs/readme.md",
                    "source": "git:repo",
                    "content": "notes",
                }
            ],
        }

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    out = asyncio.run(mcp_app.agent_index_find_similar("c1"))
    assert "similar item(s) for chunk c1" in out
    assert "docs/readme.md" in out


def test_status_delegates_and_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        assert path == "/status"
        return {
            "plugin": "agent-index",
            "version": "0.1.0-dev11",
            "draining": False,
            "index": {
                "available": True,
                "chunks": 42,
                "sources": {"git:repo": {"chunk_count": 42}},
            },
            "indexing": {"running": False, "paused": False, "active_task_id": None},
        }

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    out = asyncio.run(mcp_app.agent_index_status())
    assert "0.1.0-dev11" in out
    assert "Total chunks: 42" in out
    assert "git:repo: 42" in out


def test_reindex_delegates_and_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(path: str, body: dict) -> dict:
        assert path == "/reindex"
        assert body["full"] is True
        return {"accepted": True, "task": {"id": "t1", "source": "all"}}

    monkeypatch.setattr(mcp_app, "_post", fake_post)
    out = asyncio.run(mcp_app.agent_index_reindex(full=True))
    assert "accepted" in out.lower()
    assert "t1" in out


def test_reindex_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(path: str, body: dict) -> dict:
        return {"accepted": False, "error": "service is draining"}

    monkeypatch.setattr(mcp_app, "_post", fake_post)
    out = asyncio.run(mcp_app.agent_index_reindex())
    assert "not accepted" in out
    assert "draining" in out


def test_clusters_delegates_and_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        assert path == "/clusters"
        assert params["source"] == "git:repo"
        assert params["exact_dupes_only"] is True
        assert params["limit"] == 5
        return {
            "available": True,
            "count": 1,
            "clusters": [
                {
                    "cluster_id": "cid-1",
                    "bucket": "git",
                    "model_id": "code",
                    "size": 2,
                    "avg_score": 0.951,
                    "has_exact_dupes": True,
                    "representative": {"source": "git:repo", "file_path": "a.py"},
                    "members": [
                        {"source": "git:repo", "file_path": "a.py", "score": 1.0,
                         "is_exact_dupe": False},
                        {"source": "git:repo", "file_path": "b.py", "score": 0.95,
                         "is_exact_dupe": True},
                    ],
                }
            ],
        }

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    out = asyncio.run(
        mcp_app.agent_index_clusters(source="git:repo", exact_dupes_only=True, limit=5)
    )
    assert "Found 1 cluster(s)" in out
    assert "git / code -- 2 items" in out
    assert "avg=0.951" in out
    assert "[has exact dupes]" in out
    assert "rep: git:repo :: a.py" in out
    assert "b.py (score=0.950) (exact)" in out


def test_clusters_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        return {"available": True, "count": 0, "clusters": []}

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    assert "No clusters found" in asyncio.run(mcp_app.agent_index_clusters())


def test_clusters_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(path: str, params: dict) -> dict:
        return {"available": False, "error": "no cluster db"}

    monkeypatch.setattr(mcp_app, "_get", fake_get)
    out = asyncio.run(mcp_app.agent_index_clusters())
    assert "unavailable" in out
    assert "no cluster db" in out


def test_endpoint_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_ENDPOINT", "http://remote:9000/")
    assert mcp_app._endpoint() == "http://remote:9000"


def test_endpoint_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_ENDPOINT", raising=False)
    monkeypatch.setattr(mcp_app, "client_url", lambda: None)
    with pytest.raises(RuntimeError):
        mcp_app._endpoint()
