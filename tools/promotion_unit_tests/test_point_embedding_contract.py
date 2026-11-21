"""Actor-free pair/coverage composition; native owners remain opt-in."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "plugins/agent-index/src/agent_index/indexing"


def load(name: str, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_promotion_" + name, SOURCE / (name + ".py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_selected_pairs_forward_scope_and_prepare_before_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    scope = ("components", "points", "account", object())
    items = [SimpleNamespace(content="text", chunk_type="text")]
    model = SimpleNamespace(content_types=frozenset())
    for name, fields in (
        ("agent_index.indexing.work_counts", {"current_counts": lambda: None}),
        ("agent_index.engine.client", {"EngineUnavailableError": type("Unavailable", (Exception,), {})}),
        ("agent_index.store.content_mutations", {"coalesce_chunks": lambda chunks: chunks}),
    ):
        stub = ModuleType(name)
        for key, value in fields.items():
            setattr(stub, key, value)
        monkeypatch.setitem(sys.modules, name, stub)
    module = load("embedding_batch", monkeypatch)

    @contextmanager
    def writer(db: object, *, point_scope: object) -> Iterator[None]:
        assert db == "db" and point_scope is scope
        events.append("writer")
        yield
        events.append("end")

    table = object()
    covered = False

    def upsert(model_id: str, chunks: object, vectors: object, *, point_scope: object) -> int:
        assert (model_id, chunks, vectors) == ("a", items, [[1.0]])
        assert point_scope is scope
        events.append("write")
        return 1

    store = SimpleNamespace(
        _source_domain=SimpleNamespace(_write=writer), content_store=SimpleNamespace(db="db"),
        get_vector_table=lambda key: table, upsert_point_vectors=upsert,
    )

    def qualify(original: object, profiles: object, *, point_scope: object) -> object:
        assert original is store and profiles == {"a": model} and point_scope is scope
        events.append("qualify")
        return store

    def current(original: object, chunks: object, *, point_scope: object) -> None:
        assert original is store and chunks == items and point_scope is scope
        events.append("current")

    def missing(owner: object, chunks: object, profile: object, **kwargs: object) -> object:
        assert owner is table and chunks == items and profile is model
        assert kwargs == {"bounded": True, "point_scope": scope, "wal_aware": True}
        events.append("missing")
        return [] if covered else items

    def record(owner: object, chunks: object, profile: object, vectors: object, **kwargs: object) -> None:
        nonlocal covered
        assert owner is table and chunks == items and profile is model and vectors == [[1.0]]
        assert kwargs == {"bounded": True, "point_scope": scope}
        events.append("record")
        covered = True

    def embed(texts: list[str]) -> list[list[float]]:
        assert texts == ["text"]
        events.append("embed")
        return [[1.0]]

    def prepare(owner: object, rows: int) -> None:
        assert owner is table and rows == 1
        events.append("prepare")

    monkeypatch.setattr(module, "_point_pair_store", qualify)
    monkeypatch.setattr(module, "_require_current_input", current)
    coverage = SimpleNamespace(
        missing=missing, invalidate=lambda *args: events.append("invalidate"), record=record,
    )
    kwargs = dict(
        coverage=coverage, bounded=True, point_scope=scope, after_store=prepare,
        before_embed=lambda: events.append("ready"),
    )
    assert module.embed_and_store_pairs(items, store, {"a": SimpleNamespace(embed_texts=embed)}, {"a": model}, **kwargs) == 1
    assert events == [
        "qualify", "writer", "current", "missing", "ready", "embed", "write",
        "invalidate", "prepare", "record", "end",
    ]
    events.clear()
    assert module.embed_and_store_pairs(items, store, {"a": object()}, {"a": model}, **kwargs) == 0
    assert events == ["qualify", "writer", "current", "missing", "end"]


def test_selected_coverage_checks_stored_vectors_before_opening_receipt_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load("embedding_coverage", monkeypatch)
    scope = ("components", "points", "account", object())
    events: list[str] = []
    item = SimpleNamespace(chunk_id="one", source="points", file_path="one.md")
    supplied, qualified, profile = object(), SimpleNamespace(table_name="vectors"), object()
    actual: dict[str, str] = {}

    def qualify(table: object, chunks: object, model: object, *, point_scope: object) -> object:
        assert table is supplied and chunks == [item] and model is profile and point_scope is scope
        events.append("qualify")
        return qualified

    def read(ids: list[str], *, point_scope: object) -> dict[str, str]:
        assert ids == ["one"] and point_scope is scope
        events.append("read")
        return actual

    def connect() -> object:
        events.append("connect")
        return SimpleNamespace(
            executemany=lambda sql, rows: events.append("receipt"),
            commit=lambda: events.append("commit"), close=lambda: events.append("close"),
        )

    qualified.get_point_vectors = read
    monkeypatch.setattr(module, "_point_coverage_table", qualify)
    monkeypatch.setattr(module, "vector_hash", lambda vector: vector)
    monkeypatch.setattr(module, "input_hash", lambda chunk, model: "input")
    coverage = module.EmbeddingCoverage(SimpleNamespace(_connect=connect))
    for actual in ({}, {"one": "wrong"}):
        events.clear()
        with pytest.raises(RuntimeError, match="Stored embeddings"):
            coverage.record(supplied, [item], profile, ["expected"], bounded=True, point_scope=scope)
        assert events == ["qualify", "read"]
    actual = {"one": "expected"}
    events.clear()
    coverage.record(supplied, [item], profile, ["expected"], bounded=True, point_scope=scope)
    assert events == ["qualify", "read", "connect", "receipt", "commit", "close"]
