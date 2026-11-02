"""Bucket keys reuse only witnessed transitions of their existing source owner."""

from __future__ import annotations

import copy
import json
from dataclasses import replace

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.bucket_projection import (
    MAX_BUCKETS, bucket_generation, commit_buckets, mutation_buckets,
)
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.logical_projection import current_projection
from agent_index.store.projection_snapshot import cluster_signature
from agent_index.store.source_bucket_index import prepare_source_index
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store


def chunk(source="forge:issues:repo-a", text="sample", path="one.md"):
    return Chunk(text, path, "text", "markdown", 1, 1, source)


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path, model_profiles={
        name: ModelProfile(name, "fixture-" + name, dim=2, table_name="vectors_" + name)
        for name in ("a", "b")
    })
    store = initialize_source_store(config)
    chunks = [chunk(), chunk("forge:pulls:repo-a"), chunk("other:issues:repo-b")]
    for model in store.model_ids:
        store.upsert(model, chunks, np.array([[1, 0]] * len(chunks), np.float32))
    return config, store, chunks


def keys(store):
    projection = store._source_domain.receipt()["logical_projection"]
    return {
        (table, bucket): bucket_generation(projection, table, bucket)
        for table in projection["tables"]
        for bucket in ("forge:issues", "forge:pulls", "other:issues", "new:bucket")
    }


@pytest.mark.parametrize("operation", ["content", "vector", "both", "delete", "metadata"])
def test_native_mutation_changes_only_affected_bucket_and_model(corpus, operation):
    _config, store, chunks = corpus
    before = keys(store)
    domain = store._source_domain
    previous = domain.receipt()["logical_projection"]
    if operation == "content":
        store.upsert_content([replace(chunks[0], content="updated")])
        changed = {"chunks"}
    elif operation == "vector":
        store.upsert_vectors("b", chunks[:1], np.array([[0, 1]], np.float32))
        changed = {"vectors_b"}
    elif operation == "both":
        store.upsert("a", [replace(chunks[0], content="updated")], np.array([[0, 1]], np.float32))
        changed = {"chunks", "vectors_a"}
    elif operation == "metadata":
        store.upsert_content([replace(chunks[0], language="text")])
        changed = {"chunks", "vectors_a", "vectors_b"}
    else:
        assert store.delete_by_file(chunks[0].source, chunks[0].file_path) == [chunks[0].chunk_id]
        changed = {"chunks", "vectors_a", "vectors_b"}
    after = keys(store)
    assert {key for key in before if before[key] != after[key]} == {
        (table, "forge:issues") for table in changed
    }
    projection = domain.receipt()["logical_projection"]
    assert projection["epoch"] == previous["epoch"]
    assert projection["generation"] == previous["generation"] + 1


def test_nested_writes_union_buckets_once_and_restart_preserves_tombstone(corpus):
    config, store, chunks = corpus
    domain = store._source_domain
    previous = domain.receipt()["logical_projection"]
    before = keys(store)
    with domain.write(store.content_store.db):
        store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
        store.upsert_vectors("a", chunks[1:2], np.array([[0, 1]], np.float32))
        store.delete_by_file(chunks[2].source, chunks[2].file_path)
        assert domain.receipt()["logical_projection"] == previous
    after = domain.receipt()["logical_projection"]
    assert after["generation"] == previous["generation"] + 1
    assert after["bucket_generations"]["tables"]["vectors_a"]["buckets"] == {
        "forge:issues": after["generation"], "forge:pulls": after["generation"],
        "other:issues": after["generation"],
    }
    fresh = initialize_source_store(config)
    assert keys(fresh) == keys(store) != before
    assert fresh.content_store.get_by_ids([chunks[2].chunk_id]) == {}


def test_noop_and_physical_maintenance_keep_bucket_keys(corpus):
    _config, store, chunks = corpus
    before = keys(store)
    assert store.upsert_content(chunks, skip_identical=True) == 0
    prepare_source_index(store.content_store, max_rows=3)
    for vector in store._vector_tables.values():
        prepare_chunk_id_index(vector, max_rows=3)
    assert keys(store) == before


@pytest.mark.parametrize("position", ["before", "after", "between"])
def test_raw_mutation_cannot_hide_behind_bucket_annotation(corpus, position):
    _config, store, chunks = corpus
    domain = store._source_domain
    table = store.get_vector_table("a")._get_or_create_table()
    before = keys(store)

    def raw():
        table.update(where=f"chunk_id = '{chunks[1].chunk_id}'", values={"vector": [0.0, 1.0]})

    with domain.write(store.content_store.db):
        if position == "before":
            raw()
        store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
        if position != "before":
            raw()
        if position == "between":
            store.upsert_vectors("a", chunks[:1], np.array([[1, 0]], np.float32))
    after = keys(store)
    assert {key for key in before if before[key] != after[key]} == {
        ("vectors_a", bucket) for bucket in ("forge:issues", "forge:pulls", "other:issues", "new:bucket")
    }


def test_contiguous_mixed_maintenance_and_mutations_keep_unrelated_keys(corpus):
    _config, store, chunks = corpus
    domain = store._source_domain
    before = keys(store)
    with domain.write(store.content_store.db):
        prepare_chunk_id_index(store.get_vector_table("a"), max_rows=3)
        store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
        prepare_chunk_id_index(store.get_vector_table("a"), max_rows=3)
        prepare_source_index(store.content_store, max_rows=3)
    after = keys(store)
    assert {key for key in before if before[key] != after[key]} == {("vectors_a", "forge:issues")}


@pytest.mark.parametrize("position", ["before", "after"])
def test_noop_bucket_scope_does_not_revoke_physical_maintenance_exemption(corpus, position):
    config, store, _chunks = corpus
    domain = store._source_domain
    before = keys(store)
    signature = cluster_signature(store, config)
    generation = domain.receipt()["logical_projection"]["generation"]

    def noop():
        with domain.bucket_mutation("vectors_a", "fixture-noop", ["forge:issues"]):
            pass

    with domain.write(store.content_store.db):
        if position == "before":
            noop()
        prepare_chunk_id_index(store.get_vector_table("a"), max_rows=3)
        if position == "after":
            noop()
    assert keys(store) == before
    assert domain.receipt()["logical_projection"]["generation"] == generation
    assert cluster_signature(store, config) == signature


@pytest.mark.parametrize("kind", ["effect", "id-delete", "stale-delete"])
def test_unknown_or_unqualified_writers_force_table_fallback(corpus, kind):
    _config, store, chunks = corpus
    domain = store._source_domain
    before = keys(store)
    with domain.write(store.content_store.db):
        store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
        if kind == "effect":
            domain.effect("unknown-writer")
        elif kind == "id-delete":
            store.get_vector_table("a").delete_by_chunk_ids([chunks[1].chunk_id])
        else:
            store.get_vector_table("a").delete_stale(before=1.0)
    after = keys(store)
    assert {key for key in before if before[key] != after[key]} == {
        ("vectors_a", bucket) for bucket in ("forge:issues", "forge:pulls", "other:issues", "new:bucket")
    }


def test_failed_effect_never_publishes_bucket_coverage(corpus):
    _config, store, chunks = corpus
    domain = store._source_domain
    before = domain.receipt()["logical_projection"]
    with pytest.raises(RuntimeError, match="interrupted"):
        with domain.write(store.content_store.db):
            store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
            raise RuntimeError("interrupted")
    assert domain.receipt()["logical_projection"] == before
    assert domain.receipt()["state"] == "dirty"
    with pytest.raises(SourceLayoutError):
        store.projection_snapshot()


def test_creation_and_replacement_use_coarse_baseline(corpus):
    _config, store, chunks = corpus
    domain = store._source_domain
    before = keys(store)
    vector = store.get_vector_table("a")
    with domain.write(store.content_store.db):
        domain.effect("drop-model")
        store.content_store.db.drop_table(vector.table_name)
        vector._table = None
        store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
    after = keys(store)
    assert all(before[key] != after[key] for key in before if key[0] == "vectors_a")
    state = domain.receipt()["logical_projection"]["bucket_generations"]["tables"]["vectors_a"]
    assert state["buckets"] == {}


@pytest.mark.parametrize("source", ["", "a" * 257, "bad\nsource"])
def test_unqualified_source_retains_write_compatibility_and_coarse_fallback(corpus, source):
    _config, store, _chunks = corpus
    before = keys(store)
    store.upsert_content([chunk(source)])
    after = keys(store)
    assert all(before[key] != after[key] for key in before if key[0] == "chunks")
    assert len(store.content_store.get_by_ids([chunk(source).chunk_id])) == 1


def test_empty_delete_and_read_do_not_invent_bucket_changes(corpus):
    _config, store, _chunks = corpus
    before = keys(store)
    assert store.delete_by_file("forge:issues:none", "absent.md") == []
    assert keys(store) == before
    domain = store._source_domain
    with pytest.raises(SourceLayoutError, match="writer ownership"):
        with domain.bucket_mutation("chunks", "fixture", ["forge:issues"]):
            pytest.fail("unowned mutation was entered")


def test_transaction_bucket_budget_is_bounded_and_cannot_be_restored_by_later_scope(corpus, caplog):
    _config, store, chunks = corpus
    domain = store._source_domain
    before = keys(store)
    with domain.write(store.content_store.db):
        table = store.get_vector_table("a")._get_or_create_table()
        for start in (0, MAX_BUCKETS):
            with domain.bucket_mutation(
                table.name, "fixture-scoped", [f"source:{i}" for i in range(start, start + MAX_BUCKETS)],
            ):
                table.update(values={"indexed_at": float(start)})
        assert domain._local.bucket_changes[table.name] is None
        store.upsert_vectors("a", chunks[:1], np.array([[0, 1]], np.float32))
        assert domain._local.bucket_changes[table.name] is None
    assert all(keys(store)[key] != before[key] for key in before if key[0] == "vectors_a")
    assert "Transaction bucket budget exceeded" in caplog.text


def test_unannotated_changes_to_other_tables_are_not_covered_by_scoped_context(corpus):
    _config, store, _chunks = corpus
    domain = store._source_domain
    before = keys(store)
    table = store.get_vector_table("b")._get_or_create_table()
    with domain.write(store.content_store.db):
        with domain.bucket_mutation("vectors_a", "fixture-scoped", ["forge:issues"]):
            table.update(values={"vector": [0.0, 1.0]})
    after = keys(store)
    assert all(after[key] == before[key] for key in before if key[0] != "vectors_b")
    assert all(after[key] != before[key] for key in before if key[0] == "vectors_b")


def test_older_writer_witness_drift_cannot_reuse_bucket_keys(corpus):
    _config, store, _chunks = corpus
    domain = store._source_domain
    table = store.get_vector_table("a")._get_or_create_table()
    old = domain.receipt()
    with domain.exclusion():
        table.update(values={"vector": [0.0, 1.0]})
        value = domain.receipt()
        value["versions"][table.name] = table.version
        domain.save(value)
    assert store.projection_snapshot()["logical_epoch"] is None
    store.upsert_content([], skip_identical=True)
    assert domain.receipt()["logical_projection"]["epoch"] != old["logical_projection"]["epoch"]


def projection(generation=4):
    return {
        "version": 1, "epoch": "fixture", "generation": generation,
        "tables": {"chunks": generation}, "witness": {"chunks": {"version": generation}},
    }


def test_legacy_or_corrupt_optional_metadata_never_licenses_reuse(caplog):
    before = projection()
    expected = bucket_generation(before, "chunks", "forge:issues")
    for invalid in (
        1, {"version": True}, {"version": 1, "tables": {}},
        {"version": 1, "tables": {"chunks": {"baseline": True, "buckets": {}}}},
        {"version": 1, "tables": {"chunks": {"baseline": 3, "buckets": {"forge:issues": 5}}}},
        {"version": 1, "tables": {"chunks": {"baseline": 3, "buckets": {"a:b:c": 4}}}},
    ):
        value = {**before, "bucket_generations": invalid}
        assert bucket_generation(value, "chunks", "forge:issues") == expected
    assert "Invalid bucket generation metadata" in caplog.text
    after = projection(5)
    commit_buckets(before, after, {"chunks": {"forge:issues"}})
    assert bucket_generation(after, "chunks", "forge:pulls") == bucket_generation(
        before, "chunks", "forge:pulls",
    )
    assert bucket_generation(after, "chunks", "forge:issues") != expected
    assert current_projection({"logical_projection": after}, after["witness"]) is after


def test_bucket_budget_overflow_drops_only_optional_slice_metadata(caplog):
    before = projection()
    after = projection(5)
    buckets = {f"source:{number}" for number in range(MAX_BUCKETS + 1)}
    assert mutation_buckets(buckets) is None
    commit_buckets(before, after, {"chunks": buckets})
    assert "bucket_generations" not in after
    assert "budget exceeded" in caplog.text
    assert bucket_generation(before, "chunks", "source:0") != bucket_generation(
        after, "chunks", "source:0",
    )


def test_receipt_overflow_evicts_bucket_keys_before_table_keys(corpus, caplog):
    _config, store, _chunks = corpus
    domain = store._source_domain
    value = domain.receipt()
    before = copy.deepcopy(value["logical_projection"])
    value["padding"] = ""
    size = len(json.dumps(value, sort_keys=True).encode())
    value["padding"] = "x" * (65537 - size)
    domain.save(value)
    after = domain.receipt()["logical_projection"]
    assert after == {key: item for key, item in before.items() if key != "bucket_generations"}
    assert "retaining table coverage" in caplog.text
    assert (domain.path / ".source-layout.json").stat().st_size <= 65536
