from __future__ import annotations

import json
from types import SimpleNamespace

from agent_index import __main__ as cli


def _hit(chunk_id: str = "chunk-1") -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        score=0.75,
        file_path="src/example.py",
        line_start=3,
        line_end=7,
        source="git:repo",
        chunk_type="code",
        language="python",
        content="def example(): pass",
    )


def test_search_dispatches_and_emits_json(monkeypatch, capsys) -> None:
    from agent_index.search import engine as search_engine

    calls = []

    class FakeEngine:
        def search(self, query: str, **kwargs):
            calls.append((query, kwargs))
            return [_hit()]

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    rc = cli.main([
        "search",
        "needle",
        "--source",
        "git:repo",
        "--language",
        "python",
        "--repo",
        "repo",
        "--limit",
        "3",
        "--json",
    ])

    assert rc == 0
    assert calls == [
        (
            "needle",
            {"limit": 3, "source": "git:repo", "language": "python", "repo": "repo"},
        )
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "id": "chunk-1",
            "chunk_id": "chunk-1",
            "score": 0.75,
            "file_path": "src/example.py",
            "line_start": 3,
            "line_end": 7,
            "source": "git:repo",
            "chunk_type": "code",
            "language": "python",
            "content": "def example(): pass",
        }
    ]


def test_similar_dispatches_and_emits_json(monkeypatch, capsys) -> None:
    from agent_index.search import engine as search_engine

    calls = []

    class FakeEngine:
        def find_similar(self, chunk_id: str, **kwargs):
            calls.append((chunk_id, kwargs))
            return [_hit("nearby")]

    monkeypatch.setattr(search_engine, "create_search_engine", lambda: FakeEngine())

    rc = cli.main(["similar", "chunk-1", "--source", "git:repo", "--limit", "2"])

    assert rc == 0
    assert calls == [("chunk-1", {"limit": 2, "source": "git:repo"})]
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "nearby"
    assert payload[0]["chunk_id"] == "nearby"


def test_index_dispatches_and_emits_json(monkeypatch, capsys) -> None:
    from agent_index.indexing import engine as indexing_engine

    calls = []

    def fake_run_reindex(*, full: bool, source: str | None, progress_cb=None):
        calls.append({"full": full, "source": source, "progress_cb": progress_cb})
        return {"chunks_total": 4, "chunks_deleted": 1, "files_crawled": 2}

    monkeypatch.setattr(indexing_engine, "run_reindex", fake_run_reindex)

    rc = cli.main(["index", "--full", "--source", "git:repo"])

    assert rc == 0
    assert calls == [{"full": True, "source": "git:repo", "progress_cb": None}]
    assert json.loads(capsys.readouterr().out) == {
        "chunks_total": 4,
        "chunks_deleted": 1,
        "files_crawled": 2,
    }


def test_index_signals_source_failure(monkeypatch, capsys) -> None:
    """A per-source failure (swallowed by the run loop so other sources still
    index) is surfaced as ``sources_failed`` + a non-zero exit, instead of
    masquerading as a clean ``files_crawled: 0`` (#1350)."""
    from agent_index.indexing import engine as indexing_engine

    monkeypatch.setattr(
        indexing_engine,
        "run_reindex",
        lambda *, full, source, progress_cb=None: {
            "chunks_total": 0,
            "files_crawled": 0,
            "sources_failed": [{"source": "git", "error": "boom"}],
        },
    )

    rc = cli.main(["index", "--full"])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["sources_failed"] == [{"source": "git", "error": "boom"}]


def test_clusters_dispatches_and_emits_json(monkeypatch, capsys) -> None:
    import agent_index.store.cluster_store as cluster_store

    calls = {}

    stored = SimpleNamespace(
        cluster_id="cid-1",
        bucket="git",
        model_id="code",
        size=2,
        rep_source="git:repo",
        rep_file_path="a.py",
        has_exact_dupes=True,
        avg_score=0.95,
        created_at=0.0,
        members=(
            SimpleNamespace(
                source="git:repo", file_path="a.py", score=1.0, is_exact_dupe=False
            ),
            SimpleNamespace(
                source="git:repo", file_path="b.py", score=0.95, is_exact_dupe=True
            ),
        ),
    )

    class FakeStore:
        def __init__(self, path):
            calls["path"] = path

        def list_clusters(self, **kwargs):
            calls["kwargs"] = kwargs
            return [stored]

    monkeypatch.setattr(cluster_store, "ClusterStore", FakeStore)

    rc = cli.main(["clusters", "--source", "git:repo", "--model", "code",
                   "--exact-dupes-only", "--limit", "7"])

    assert rc == 0
    assert calls["kwargs"]["bucket"] == "git:repo"
    assert calls["kwargs"]["model_id"] == "code"
    assert calls["kwargs"]["has_exact_dupes"] is True
    assert calls["kwargs"]["limit"] == 7
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["cluster_id"] == "cid-1"
    assert payload[0]["representative"]["file_path"] == "a.py"
    assert payload[0]["members"][1]["is_exact_dupe"] is True


def test_unavailable_search_emits_clean_json_error(monkeypatch, capsys) -> None:
    from agent_index.search import engine as search_engine

    def raise_unavailable():
        raise RuntimeError("index unavailable")

    monkeypatch.setattr(search_engine, "create_search_engine", raise_unavailable)

    rc = cli.main(["search", "needle", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["hits"] == []
    assert "RuntimeError: index unavailable" == payload["error"]
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
