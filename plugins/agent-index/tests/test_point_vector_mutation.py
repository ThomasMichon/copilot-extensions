"""Selected vector effects retain native ownership and uncertain failures."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.point_hints import PointBinding
from agent_index.store.content_mutations import content_record
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired
from agent_index.store.source_layout import (
    LAYOUT, MARKER, PointScope, SourceLayoutError, initialize_source_store,
)
from agent_index.store.vector_table import VectorTable

SCOPE: PointScope = (
    "sample:components", "sample:points", "account",
    PointBinding("https://example.test/project", "union", "context"),
)
VALUES = np.array([[0.0, 1.0]], dtype=np.float32)


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MultiModelStore, list[Chunk]]:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path / "store", model_profiles={
        "first": ModelProfile("first", "fixture", dim=2, table_name="vectors_first"),
        "second": ModelProfile("second", "fixture", dim=2, table_name="vectors_second"),
    })
    store = initialize_source_store(config, point_scope=SCOPE)
    domain = store._source_domain
    assert domain is not None
    chunks = [Chunk(str(i), f"{i}.md", "text", "markdown", 1, 1, SCOPE[1]) for i in range(20)]
    with domain._write(store.content_store.db, point_scope=SCOPE):
        domain.effect("fixture-seed")
        store.content_store._get_or_create_table().add([
            {**content_record(chunk), "indexed_at": 0.0} for chunk in chunks
        ])
        for model in store.model_ids:
            store.get_vector_table(model)._get_or_create_table().add([
                {"chunk_id": chunk.chunk_id, "vector": [1.0, 0.0], "indexed_at": 0.0,
                 "source": chunk.source, "language": chunk.language} for chunk in chunks
            ])
    prepare_ingestion_indices(store, max_rows=len(chunks), point_scope=SCOPE)
    return store, chunks


def test_selected_effect_uses_one_indexed_input_and_exact_opened_vector_handle(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    vector = store.get_vector_table("first")
    other_table = db.open_table("vectors_second")
    vector._table = other_table
    before = db.open_table("vectors_first").search().limit(None).to_list()
    other_version = db.open_table("vectors_second").version
    search = type(other_table).search
    plans: list[str] = []

    class ObservedQuery:
        def __init__(self, query: Any) -> None:
            self.query = query

        def where(self, predicate: str) -> ObservedQuery:
            assert predicate.startswith("chunk_id IN (")
            self.query = self.query.where(predicate)
            return self

        def select(self, columns: list[str]) -> ObservedQuery:
            assert set(columns) == set(content_record(chunks[0]))
            self.query = self.query.select(columns)
            return self

        def limit(self, rows: int) -> ObservedQuery:
            assert rows == 2
            self.query = self.query.limit(rows)
            return self

        def to_list(self) -> Any:
            plan = self.query.analyze_plan()
            scanned = re.findall(r"\brows_scanned=(\d+)", plan)
            assert "ScalarIndexQuery" in plan and scanned and sum(map(int, scanned)) <= 1, plan
            plans.append(plan)
            return self.query.to_list()

    def observed_search(self: Any, *args: Any, **kwargs: Any) -> Any:
        query = search(self, *args, **kwargs)
        return ObservedQuery(query) if self.name == "chunks" else query

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("qualified point mutation must not reopen or create through generic fallback")

    with domain._write(db, point_scope=SCOPE):
        with monkeypatch.context() as patch:
            patch.setattr(type(other_table), "search", observed_search)
            patch.setattr(VectorTable, "_get_or_create_table", forbidden)
            assert store.upsert_point_vectors("first", chunks[:1], VALUES, point_scope=SCOPE) == 1
            with pytest.raises(ScalarIndexPreparationRequired):
                store.upsert_point_vectors("first", chunks[:1], VALUES, point_scope=SCOPE)
        vector._table = db.open_table("vectors_first")
        prepare_ingestion_indices(store, max_rows=1, point_scope=SCOPE)
    assert len(plans) == 1
    after = {row["chunk_id"]: row for row in db.open_table("vectors_first").search().limit(None).to_list()}
    assert len(after) == len(before)
    for row in before:
        if row["chunk_id"] == chunks[0].chunk_id:
            assert after[row["chunk_id"]]["vector"] == VALUES[0].tolist()
            assert after[row["chunk_id"]]["source"] == SCOPE[1]
        else:
            assert after[row["chunk_id"]] == row
    assert db.open_table("vectors_second").version == other_version
    assert domain.receipt()["state"] == "clean"


def test_selected_effect_refuses_unowned_scope_input_and_model_before_mutation(
    prepared: tuple[MultiModelStore, list[Chunk]],
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    vector = store.get_vector_table("first")
    empty = np.zeros((0, 2), dtype=np.float32)
    for items, values in ((chunks[:1], VALUES), ([], empty)):
        before = snapshot(domain.path)
        with pytest.raises(SourceLayoutError, match="owning source writer"):
            vector.upsert_point(items, values, point_scope=SCOPE)
        assert snapshot(domain.path) == before
    with domain._write(db, point_scope=SCOPE):
        before = snapshot(domain.path)
        for items, values in ((chunks[:1], VALUES), ([], empty)):
            for scope in (None, (SCOPE[0], SCOPE[1], "foreign", SCOPE[3])):
                with pytest.raises(SourceLayoutError):
                    vector.upsert_point(items, values, point_scope=scope)
                assert snapshot(domain.path) == before
            for model_id, table, dim in (
                (None, "vectors_first", 2), ("alias", "vectors_first", 2),
                ("first", "vectors_second", 2), ("first", "vectors_first", 3),
                ("first", "chunks", 2),
            ):
                owner = VectorTable(db, table_name=table, dim=dim, model_id=model_id, source_domain=domain)
                with pytest.raises(SourceLayoutError, match="model binding"):
                    owner.upsert_point(items, values, point_scope=SCOPE)
                assert snapshot(domain.path) == before
        for changed in (
            replace(chunks[0], content="stale"),
            replace(chunks[0], metadata={"title": "stale"}),
            replace(chunks[0], source="foreign:points"),
        ):
            with pytest.raises(SourceLayoutError):
                vector.upsert_point([changed], VALUES, point_scope=SCOPE)
            assert snapshot(domain.path) == before
        assert vector.upsert_point([], empty, point_scope=SCOPE) == 0
        assert snapshot(domain.path) == before


@pytest.mark.parametrize("binding_change", ["absent", "swapped"])
def test_cached_selected_effect_rechecks_current_model_receipt(
    prepared: tuple[MultiModelStore, list[Chunk]], binding_change: str,
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    owner = store.get_vector_table("first")
    value = domain.receipt()
    if binding_change == "absent":
        value.pop("point_models")
    else:
        models = value["point_models"]["models"]
        models["first"], models["second"] = models["second"], models["first"]
    (domain.path / MARKER).write_text(json.dumps(value))
    with domain._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(domain.path)
        for items, vectors in ((chunks[:1], VALUES), ([], np.zeros((0, 2), dtype=np.float32))):
            with pytest.raises(SourceLayoutError, match="model binding"):
                owner.upsert_point(items, vectors, point_scope=SCOPE)
            assert snapshot(domain.path) == before


def test_selected_effect_propagates_native_open_failure_even_with_cached_handle(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    owner = store.get_vector_table("first")
    assert owner._table is not None

    class FailedConnection:
        uri = db.uri

        def list_tables(self) -> Any:
            return db.list_tables()

        def open_table(self, name: str) -> Any:
            if name == "vectors_first":
                raise RuntimeError("native open failed")
            return db.open_table(name)

        def create_table(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("an open failure must never create a replacement")

    monkeypatch.setattr(owner, "_db", FailedConnection())
    with domain._write(db, point_scope=SCOPE):
        before = snapshot(domain.path)
        with pytest.raises(RuntimeError, match="native open failed"):
            owner.upsert_point(chunks[:1], VALUES, point_scope=SCOPE)
        assert snapshot(domain.path) == before


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_caught_add_failure_retains_dirty_uncertainty_and_refuses_retry(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
    failure: type[BaseException],
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    table = db.open_table("vectors_first")
    add = type(table).add

    def fail_add(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.name == "vectors_first":
            raise failure("injected post-delete failure")
        return add(self, *args, **kwargs)

    monkeypatch.setattr(type(table), "add", fail_add)
    with pytest.raises(SourceLayoutError, match="failed nested effect"):
        with domain._write(db, point_scope=SCOPE):
            with pytest.raises(failure, match="post-delete"):
                store.upsert_point_vectors("first", chunks[:1], VALUES, point_scope=SCOPE)
            with domain._write(db, point_scope=SCOPE):
                pass
    receipt = domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
    assert not db.open_table("vectors_first").search().where(
        f"chunk_id = '{chunks[0].chunk_id}'",
    ).to_list()
    reopened = MultiModelStore(domain.path, point_scope=SCOPE)
    assert reopened._source_domain is not None
    with pytest.raises(SourceLayoutError):
        with reopened._source_domain.read(reopened.content_store.db, point_scope=SCOPE):
            pass
    before = snapshot(domain.path)
    with pytest.raises(SourceLayoutError, match="explicit recovery"):
        with domain._write(db, point_scope=SCOPE):
            store.upsert_point_vectors("first", chunks[:1], VALUES, point_scope=SCOPE)
    assert snapshot(domain.path) == before
