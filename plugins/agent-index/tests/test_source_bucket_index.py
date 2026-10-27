"""Native bucket/model reads must do slice work, not merely return slice output."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.clustering import source_bucket
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import ScalarIndexPreparationRequired
from agent_index.store.source_bucket_index import (
    bucket_predicate,
    bucket_records,
    prepare_source_index,
)
from agent_index.store.source_layout import LAYOUT, initialize_source_store


def chunk(source: str, path: str = "item.md", *, line: int = 1) -> Chunk:
    return Chunk(f"text {source} {path} {line}", path, "text", "markdown", line, line + 2, source)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path, model_profiles={
        "a": ModelProfile("a", "fixture-a", dim=2, table_name="vectors_a"),
        "b": ModelProfile("b", "fixture-b", dim=2, table_name="vectors_b"),
    })
    store = initialize_source_store(config)
    chunks = [
        chunk("forge:issues:repo-a", line=4), chunk("forge:issues:repo-a"),
        chunk("forge:issues:repo-b"), chunk("forge:issues"),
        chunk("forge:issues-extra:repo-c"), chunk("forge:pulls:repo-a"),
        chunk("forge"), chunk("forge:other"), chunk("name_'%\\:kind:repo-a"),
        chunk("name_xxx:kind:repo-b"),
    ]
    store.upsert("a", chunks, np.array([[1, 0]] * len(chunks), np.float32))
    store.upsert_vectors("b", chunks[:1], np.array([[0, 1]], np.float32))
    return config, store, chunks


def prepare(store: MultiModelStore, count: int, models: tuple[str, ...] = ("a", "b")) -> None:
    prepare_source_index(store.content_store, max_rows=count)
    for model in models:
        prepare_chunk_id_index(store.get_vector_table(model), max_rows=count)


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("bucket representation attempted an unscoped read or index write")


@pytest.mark.parametrize("bucket", ["forge", "forge:issues", "name_'%\\:kind"])
def test_native_bucket_membership_matches_existing_definition(corpus, bucket: str) -> None:
    _config, store, chunks = corpus
    prepare(store, len(chunks))
    records = bucket_records(store.content_store, bucket, row_budget=len(chunks))
    assert {row.chunk_id for row in records} == {
        row.chunk_id for row in chunks if source_bucket(row.source) == bucket
    }


@pytest.mark.parametrize("bucket", ["", "a:b:c", "a\nb", "a" * 257, None, 4])
def test_invalid_bucket_refuses(bucket: Any) -> None:
    with pytest.raises(ValueError, match="bucket"):
        bucket_predicate(bucket)


def test_selected_models_and_bucket_match_full_representations(corpus, monkeypatch) -> None:
    config, store, chunks = corpus
    expected = {
        (rep.source, rep.file_path): rep for rep in store.iter_item_representations(strict=True)
        if source_bucket(rep.source) == "forge:issues"
    }
    prepare(store, len(chunks))
    fresh = initialize_source_store(config)
    before = fresh.projection_snapshot()
    monkeypatch.setattr(fresh._content, "distinct_items", forbidden)
    monkeypatch.setattr(fresh._content, "get_chunks_for_file", forbidden)
    monkeypatch.setattr(fresh.get_vector_table("b"), "get_vectors", forbidden)
    actual = list(fresh.iter_bucket_item_representations(
        "forge:issues", model_ids=("a", "a"), row_budget=4,
    ))
    assert {(rep.source, rep.file_path) for rep in actual} == expected.keys()
    for rep in actual:
        prior = expected[(rep.source, rep.file_path)]
        assert rep.content_hash == prior.content_hash and rep.chunk_count == prior.chunk_count
        assert rep.model_ids == ["a"]
        np.testing.assert_array_equal(rep.centroids["a"], prior.centroids["a"])
    assert fresh.projection_snapshot() == before


def test_mixed_items_keep_all_canonical_chunks_in_hash_and_weighting(corpus) -> None:
    _config, store, chunks = corpus
    expected = store.item_representation("forge:issues:repo-a", "item.md", strict=True)
    prepare(store, len(chunks))
    reps = list(store.iter_bucket_item_representations(
        "forge:issues", model_ids=("b", "a"), row_budget=4,
    ))
    actual = next(rep for rep in reps if rep.source == "forge:issues:repo-a")
    assert actual.is_mixed and actual.chunk_count == 2
    assert actual.content_hash == expected.content_hash
    for model in ("a", "b"):
        np.testing.assert_array_equal(actual.centroids[model], expected.centroids[model])


def test_missing_source_or_vector_preparation_never_scans_or_mutates(corpus, monkeypatch) -> None:
    _config, store, chunks = corpus
    before = store.projection_snapshot()
    monkeypatch.setattr(store._content, "distinct_items", forbidden)
    with pytest.raises(ScalarIndexPreparationRequired, match="Source index is missing"):
        list(store.iter_bucket_item_representations(
            "forge:issues", model_ids=("a",), row_budget=4,
        ))
    assert store.projection_snapshot() == before
    prepare_source_index(store.content_store, max_rows=len(chunks))
    before = store.projection_snapshot()
    with pytest.raises(ScalarIndexPreparationRequired, match="index is missing"):
        list(store.iter_bucket_item_representations(
            "forge:issues", model_ids=("a",), row_budget=4,
        ))
    assert store.projection_snapshot() == before


@pytest.mark.parametrize("models", [(), ("missing",), ("a", "missing"), ["a"], (1,)])
def test_invalid_models_refuse_before_bucket_read(corpus, monkeypatch, models: Any) -> None:
    _config, store, _chunks = corpus
    monkeypatch.setattr("agent_index.store.source_bucket_index.bucket_records", forbidden)
    with pytest.raises(ValueError, match="registered"):
        list(store.iter_bucket_item_representations(
            "forge:issues", model_ids=models, row_budget=4,
        ))


@pytest.mark.parametrize("budget", [3, -1, True, 1.5])
def test_row_budget_refuses_without_reading_canonical_rows(corpus, monkeypatch, budget: Any) -> None:
    _config, store, chunks = corpus
    prepare(store, len(chunks))
    table = store.content_store._get_or_create_table()
    before = store.projection_snapshot()
    monkeypatch.setattr(type(table), "search", forbidden)
    error = ScalarIndexPreparationRequired if budget == 3 else ValueError
    with pytest.raises(error, match="budget"):
        bucket_records(store.content_store, "forge:issues", row_budget=budget)
    assert store.projection_snapshot() == before


def test_empty_bucket_is_proven_without_vector_reads(corpus, monkeypatch) -> None:
    _config, store, chunks = corpus
    prepare_source_index(store.content_store, max_rows=len(chunks))
    monkeypatch.setattr(store.get_vector_table("a"), "get_vectors", forbidden)
    assert list(store.iter_bucket_item_representations(
        "absent:kind", model_ids=("a",), row_budget=0,
    )) == []


def test_source_index_tails_require_explicit_budget_and_preserve_logical_key(corpus) -> None:
    _config, store, chunks = corpus
    before = store.projection_snapshot()
    prepare(store, len(chunks))
    assert store.projection_snapshot() == before
    version = store.content_store._get_or_create_table().version
    assert prepare_source_index(store.content_store, max_rows=0).version == version
    added = chunk("forge:issues:new")
    store.upsert("a", [added], np.array([[1, 0]], np.float32))
    before = store.projection_snapshot()
    with pytest.raises(ScalarIndexPreparationRequired, match="budget"):
        bucket_records(store.content_store, "forge:issues", row_budget=5)
    with pytest.raises(ScalarIndexPreparationRequired, match="budget"):
        prepare_source_index(store.content_store, max_rows=0)
    assert store.projection_snapshot() == before
    assert prepare_source_index(store.content_store, max_rows=1).segments == 2
    prepare_chunk_id_index(store.get_vector_table("a"), max_rows=1)
    assert len(list(store.iter_bucket_item_representations(
        "forge:issues", model_ids=("a",), row_budget=5,
    ))) == 4
    assert store.projection_snapshot() == before
    changed = replace(added, metadata={"fresh": True})
    store.upsert_content([changed])
    prepare_source_index(store.content_store, max_rows=1)
    rows = bucket_records(store.content_store, "forge:issues", row_budget=5)
    assert next(row for row in rows if row.chunk_id == added.chunk_id).metadata == {"fresh": True}
    store.delete_by_file(added.source, added.file_path)
    rows = bucket_records(store.content_store, "forge:issues", row_budget=4)
    assert added.chunk_id not in {row.chunk_id for row in rows}


def test_nonparticipating_owner_cannot_opt_into_bucket_reads(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path)
    with pytest.raises(ScalarIndexPreparationRequired, match="participating"):
        prepare_source_index(store.content_store, max_rows=0)
    with pytest.raises(ScalarIndexPreparationRequired, match="participating"):
        bucket_records(store.content_store, "forge:issues", row_budget=0)
    assert store.content_store.db.list_tables().tables == []


def test_source_index_initial_budget_and_incompatible_definition_refuse(corpus) -> None:
    from lancedb.index import BTree

    _config, store, chunks = corpus
    content = store.content_store
    before = store.projection_snapshot()
    with pytest.raises(ScalarIndexPreparationRequired, match="budget"):
        prepare_source_index(content, max_rows=len(chunks) - 1)
    assert store.projection_snapshot() == before
    domain = store._source_domain
    with domain.write(content.db):
        domain.effect("fixture-incompatible-index")
        content._get_or_create_table().create_index(
            "chunk_id", config=BTree(), name="source_idx",
        )
    before = store.projection_snapshot()
    with pytest.raises(ScalarIndexPreparationRequired, match="Incompatible"):
        prepare_source_index(content, max_rows=len(chunks))
    with pytest.raises(ScalarIndexPreparationRequired, match="Incompatible"):
        bucket_records(content, "forge:issues", row_budget=4)
    assert store.projection_snapshot() == before


@pytest.mark.parametrize("failure", ["incomplete", "duplicate", "foreign", "malformed", "backend"])
def test_bad_bucket_input_propagates_and_closes_reader(corpus, monkeypatch, failure: str) -> None:
    import pyarrow as pa

    from agent_index.store.content_snapshot import ContentSnapshotUnavailable

    _config, store, chunks = corpus
    content = store.content_store
    prepare_source_index(content, max_rows=len(chunks))
    table = content._get_or_create_table()
    rows = table.search().where(bucket_predicate("forge:issues")).limit(5).to_list()
    if failure == "incomplete":
        rows.pop()
    elif failure == "duplicate":
        rows[1] = rows[0]
    elif failure == "foreign":
        rows[0]["source"] = "forge:pulls:repo-a"
    elif failure == "malformed":
        rows[0]["metadata"] = "[]"
    closed = []

    class Reader:
        def __iter__(self):
            yield pa.RecordBatch.from_pylist(rows[:1])
            if failure == "backend":
                raise OSError("fixture bucket reader failure")
            yield pa.RecordBatch.from_pylist(rows[1:])

        def close(self) -> None:
            closed.append(True)

    class Query:
        def where(self, predicate: str):
            assert predicate == bucket_predicate("forge:issues")
            return self

        def limit(self, count: int):
            assert count == 5
            return self

        def to_batches(self, *, batch_size: int) -> Reader:
            assert batch_size == 200
            return Reader()

    before = store.projection_snapshot()
    monkeypatch.setattr(type(table), "search", lambda *args: Query())
    error = (
        OSError if failure == "backend" else
        ContentSnapshotUnavailable if failure == "malformed" else RuntimeError
    )
    with pytest.raises(error):
        bucket_records(content, "forge:issues", row_budget=4)
    assert closed == [True]
    assert store.projection_snapshot() == before


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_actual_bucket_and_model_reads_stay_slice_scoped_at_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int,
) -> None:
    import lancedb
    from lancedb.query import LanceEmptyQueryBuilder

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path, model_profiles={
        "a": ModelProfile("a", "fixture-a", dim=2, table_name="vectors_a"),
        "b": ModelProfile("b", "fixture-b", dim=2, table_name="vectors_b"),
    })
    store = initialize_source_store(config)
    chunks = [chunk("unrelated:items", f"{i}.md") for i in range(size)]
    selected = [chunk("forge:issues:repo-a"), chunk("forge:issues:repo-b")]
    store.upsert_content(chunks + selected)
    store.upsert_vectors("a", chunks + selected, np.ones((size + 2, 2), np.float32))
    prepare(store, size + 2, ("a",))
    db = lancedb.connect(config.lance_dir)
    cold = db.open_table(config.content_table)
    vectors = db.open_table("vectors_a")
    ids = ", ".join(f"'{row.chunk_id}'" for row in selected)
    for _ in range(2):
        queries = [
            cold.search().where(bucket_predicate("forge:issues")).limit(3),
            vectors.search().where(f"chunk_id IN ({ids})").limit(3).select(["chunk_id", "vector"]),
        ]
        for query in queries:
            plan = query.analyze_plan()
            assert "ScalarIndexQuery" in plan, plan
            scanned = [int(value) for value in re.findall(r"\brows_scanned=(\d+)", plan)]
            assert sum(scanned) == 2, plan
            assert sum(int(value) for value in re.findall(r"\biops=(\d+)", plan)) < 64, plan
    batches = LanceEmptyQueryBuilder.to_batches
    reads = {"canonical": 0, "vectors": 0}
    get_vectors = store.get_vector_table("a").get_vectors

    def scoped_vectors(ids: list[str], **kwargs):
        assert set(ids) <= {row.chunk_id for row in selected} and kwargs == {"bounded": True}
        reads["vectors"] += len(ids)
        return get_vectors(ids, **kwargs)

    def scoped_batches(query, **kwargs):
        if query._where == bucket_predicate("forge:issues"):
            reads["canonical"] += 1
        else:
            assert query._where.startswith("chunk_id IN (")
        return batches(query, **kwargs)

    monkeypatch.setattr(LanceEmptyQueryBuilder, "to_batches", scoped_batches)
    monkeypatch.setattr(store.get_vector_table("a"), "get_vectors", scoped_vectors)
    monkeypatch.setattr(store.get_vector_table("b"), "get_vectors", forbidden)
    monkeypatch.setattr(store._content, "distinct_items", forbidden)
    monkeypatch.setattr(store._content, "get_chunks_for_file", forbidden)
    before = store.projection_snapshot()
    reps = list(store.iter_bucket_item_representations(
        "forge:issues", model_ids=("a",), row_budget=2,
    ))
    assert {rep.source for rep in reps} == {row.source for row in selected}
    assert reads == {"canonical": 1, "vectors": 2}
    assert store.projection_snapshot() == before
