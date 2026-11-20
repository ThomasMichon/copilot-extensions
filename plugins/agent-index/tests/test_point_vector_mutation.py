"""Selected native effects retain ownership, provenance and uncertain failures."""

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
from agent_index.store.content_store import ContentStore
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


def test_selected_content_preserves_noops_and_refreshes_every_bound_model(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    before = {
        name: db.open_table(name).search().limit(None).to_list()
        for name in db.list_tables().tables
    }
    changed = replace(chunks[0], content="0 ", language="python", metadata={"nested": {"a": 1}})
    store.content_store._table = db.open_table("vectors_first")
    get_table = ContentStore._get_or_create_table
    search = type(db.open_table("chunks")).search
    plans: list[str] = []

    class ObservedQuery:
        def __init__(self, query: Any) -> None:
            self.query = query

        def where(self, predicate: str, **kwargs: Any) -> ObservedQuery:
            assert predicate.startswith("chunk_id IN (")
            self.query = self.query.where(predicate, **kwargs)
            return self

        def select(self, columns: list[str]) -> ObservedQuery:
            self.query = self.query.select(columns)
            return self

        def limit(self, rows: int) -> ObservedQuery:
            assert rows in (1, 2)
            self.query = self.query.limit(rows)
            return self

        def to_list(self) -> Any:
            plan = self.query.analyze_plan()
            scanned = re.findall(r"\brows_scanned=(\d+)", plan)
            assert "ScalarIndexQuery" in plan and scanned and sum(map(int, scanned)) <= 1, plan
            plans.append(plan)
            return self.query.to_list()

    def observed_search(self: Any, *args: Any, **kwargs: Any) -> ObservedQuery:
        return ObservedQuery(search(self, *args, **kwargs))

    def qualified_only(self: ContentStore) -> Any:
        assert self is not store.content_store and self._table is not None
        return get_table(self)

    with domain._write(db, point_scope=SCOPE):
        unchanged = snapshot(domain.path)
        assert store.upsert_point_content([chunks[0], chunks[0]], point_scope=SCOPE) == 0
        assert store.upsert_point_content([], point_scope=SCOPE) == 0
        assert snapshot(domain.path) == unchanged
        with monkeypatch.context() as patch:
            patch.setattr(type(db.open_table("chunks")), "search", observed_search)
            patch.setattr(ContentStore, "_get_or_create_table", qualified_only)
            assert store.upsert_point_content([changed, changed], point_scope=SCOPE) == 1
        with pytest.raises(ScalarIndexPreparationRequired):
            store.upsert_point_content([changed], point_scope=SCOPE)
        store.content_store._table = db.open_table("chunks")
        prepare_ingestion_indices(store, max_rows=1, point_scope=SCOPE)
        assert store.upsert_point_content([changed], point_scope=SCOPE) == 0
    assert len(plans) == 5
    for name, old_rows in before.items():
        after = {row["chunk_id"]: row for row in db.open_table(name).search().limit(None).to_list()}
        assert len(after) == len(old_rows)
        for row in old_rows:
            if row["chunk_id"] != changed.chunk_id:
                assert after[row["chunk_id"]] == row
            elif name == "chunks":
                assert {k: v for k, v in after[row["chunk_id"]].items() if k != "indexed_at"} == content_record(changed)
            else:
                assert after[row["chunk_id"]]["language"] == "python"
                assert after[row["chunk_id"]]["source"] == SCOPE[1]
                assert after[row["chunk_id"]]["vector"] == row["vector"]
    assert domain.receipt()["state"] == "clean"


def test_selected_content_refuses_unowned_input_and_conflicts_before_effects(
    prepared: tuple[MultiModelStore, list[Chunk]],
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    before = snapshot(domain.path)
    with pytest.raises(SourceLayoutError, match="owning source writer"):
        store.upsert_point_content(chunks[:1], point_scope=SCOPE)
    assert snapshot(domain.path) == before
    with domain._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(domain.path)
        for items in ([], chunks[:1]):
            for scope in (None, (SCOPE[0], SCOPE[1], "foreign", SCOPE[3])):
                with pytest.raises(SourceLayoutError):
                    store.upsert_point_content(items, point_scope=scope)
                assert snapshot(domain.path) == before
        with pytest.raises(SourceLayoutError, match="selected family"):
            store.upsert_point_content([replace(chunks[0], source="foreign")], point_scope=SCOPE)
        with pytest.raises(ValueError, match="Conflicting"):
            store.upsert_point_content([chunks[0], replace(chunks[0], metadata={"conflict": True})], point_scope=SCOPE)
        with pytest.raises(SourceLayoutError):
            store.upsert_content(chunks[:1])
        assert snapshot(domain.path) == before


@pytest.mark.parametrize("obstruction", ["missing-index", "tail", "segments", "schema", "unexpected-table"])
def test_selected_content_preflights_the_final_model_before_earlier_effects(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
    obstruction: str,
) -> None:
    import agent_index.store.point_content as point_content
    from agent_index.store.scalar_index import inspect_index

    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    with domain._write(db, point_scope=SCOPE):
        table = db.open_table("vectors_second")
        if obstruction == "missing-index":
            domain.effect("fixture-index-drop")
            table.drop_index("chunk_id_idx")
        elif obstruction == "tail":
            domain.effect("fixture-tail")
            table.add([{
                "chunk_id": "unrelated-tail", "vector": [1.0, 0.0], "indexed_at": 0.0,
                "source": SCOPE[1], "language": "markdown",
            }])
        elif obstruction == "schema":
            domain.effect("fixture-schema")
            table.add_columns({"extra": "'incompatible'"})
        elif obstruction == "unexpected-table":
            domain.effect("fixture-extra-table")
            db.create_table("unbound_model", schema=table.schema)

        def exhausted(owner: Any, *, column: str) -> Any:
            state, dataset = inspect_index(owner, column=column)
            if owner.name == "vectors_second":
                state = replace(state, segments=32)
            return state, dataset

        if obstruction == "segments":
            monkeypatch.setattr(point_content, "inspect_index", exhausted)
        before = snapshot(domain.path)
        if obstruction == "segments":
            assert store.upsert_point_content(chunks[:1], point_scope=SCOPE) == 0
        with pytest.raises((ScalarIndexPreparationRequired, SourceLayoutError)):
            store.upsert_point_content([replace(chunks[0], language="python")], point_scope=SCOPE)
        assert snapshot(domain.path) == before


def test_selected_content_inserts_missing_ids_and_refuses_conflicting_stored_sources(
    prepared: tuple[MultiModelStore, list[Chunk]],
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    added = replace(chunks[0], file_path="new.md", metadata={"flag": True})
    vector_versions = {model: db.open_table(store.get_vector_table(model).table_name).version for model in store.model_ids}
    with domain._write(db, point_scope=SCOPE):
        assert store.upsert_point_content([added], point_scope=SCOPE) == 1
        prepare_ingestion_indices(store, max_rows=1, point_scope=SCOPE)
        table = db.open_table("chunks")
        with domain.bucket_mutation("chunks", "content-upsert:chunks", [SCOPE[1]]):
            table.update(where=f"chunk_id = '{chunks[0].chunk_id}'", values={"source": "foreign"})
        prepare_ingestion_indices(store, max_rows=1, point_scope=SCOPE)
        before = snapshot(domain.path)
        with pytest.raises(SourceLayoutError, match="conflicting canonical source"):
            store.upsert_point_content(chunks[:1], point_scope=SCOPE)
        assert snapshot(domain.path) == before
    assert db.open_table("chunks").search().where(f"chunk_id = '{added.chunk_id}'").to_list()[0]["content"] == added.content
    assert all(
        db.open_table(store.get_vector_table(model).table_name).version == version
        for model, version in vector_versions.items()
    )


@pytest.mark.parametrize("binding_change", ["absent", "content-role"])
def test_selected_content_rechecks_current_binding_even_for_empty_cached_calls(
    prepared: tuple[MultiModelStore, list[Chunk]], binding_change: str,
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    value = domain.receipt()
    if binding_change == "absent":
        value.pop("point_models")
    else:
        value["point_models"]["content_table"] = "other_content"
    (domain.path / MARKER).write_text(json.dumps(value))
    with domain._write(store.content_store.db, point_scope=SCOPE):
        before = snapshot(domain.path)
        for selected in ([], chunks[:1]):
            with pytest.raises(SourceLayoutError, match="model binding"):
                store.upsert_point_content(selected, point_scope=SCOPE)
            assert snapshot(domain.path) == before


def test_selected_content_failed_open_never_uses_cached_table_or_creates_replacement(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db

    class FailedConnection:
        uri = db.uri

        def list_tables(self) -> Any:
            return db.list_tables()

        def open_table(self, name: str) -> Any:
            if name == "vectors_second":
                raise RuntimeError("native open failed")
            return db.open_table(name)

        def create_table(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("an open failure must never create a replacement")

    monkeypatch.setattr(store.content_store, "_db", FailedConnection())
    with domain._write(db, point_scope=SCOPE):
        before = snapshot(domain.path)
        with pytest.raises(RuntimeError, match="native open failed"):
            store.upsert_point_content(chunks[:1], point_scope=SCOPE)
        assert snapshot(domain.path) == before


@pytest.mark.parametrize("failure_table", ["chunks", "vectors_first", "vectors_second"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_selected_content_caught_partial_failure_cannot_publish_clean(
    prepared: tuple[MultiModelStore, list[Chunk]], monkeypatch: pytest.MonkeyPatch,
    failure_table: str, failure: type[BaseException],
) -> None:
    store, chunks = prepared
    domain = store._source_domain
    assert domain is not None
    db = store.content_store.db
    table_type = type(db.open_table("chunks"))
    operation = "add" if failure_table == "chunks" else "update"
    original = getattr(table_type, operation)

    def failed(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.name == failure_table:
            raise failure("injected partial native failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(table_type, operation, failed)
    changed = replace(chunks[0], language="python")
    with pytest.raises(SourceLayoutError, match="failed nested effect"):
        with domain._write(db, point_scope=SCOPE):
            with pytest.raises(failure, match="partial native"):
                store.upsert_point_content([changed], point_scope=SCOPE)
            with domain._write(db, point_scope=SCOPE):
                pass
    assert domain.receipt()["state"] == "dirty"
    assert domain.receipt()["uncertain_effect"] is True
    before = snapshot(domain.path)
    with pytest.raises(SourceLayoutError, match="explicit recovery"):
        with domain._write(db, point_scope=SCOPE):
            pass
    assert snapshot(domain.path) == before
