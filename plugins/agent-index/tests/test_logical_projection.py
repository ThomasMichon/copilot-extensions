"""Logical generations share the existing source writer's commit and failure gates."""

from __future__ import annotations

import copy

import numpy as np
import pytest
from lancedb.index import FTS

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.logical_projection import commit_projection, current_projection
from agent_index.store.projection_snapshot import cluster_signature
from agent_index.store.source_layout import (
    LAYOUT, SourceLayoutError, initialize_source_store, validate_source_store,
)


def item(path="one.md", text="sample text"):
    return Chunk(text, path, "text", "markdown", 1, 1, "git:sample")


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "data",
        model_profiles={
            name: ModelProfile(name, f"fixture-{name}", dim=2) for name in ("small", "other")
        },
        cluster_enabled=True,
    )
    store = initialize_source_store(config)
    chunks = [item(), item("two.md")]
    for model in store.model_ids:
        store.upsert(model, chunks, np.array([[1, 0], [1, 0]], np.float32))
    return config, store


def test_nested_writes_publish_one_generation_only_at_clean_commit(corpus):
    _config, store = corpus
    domain = store._source_domain
    before = domain.receipt()["logical_projection"]
    with domain.write(store.content_store.db):
        store.upsert("small", [item("new.md")], np.array([[0, 1]], np.float32))
        store.upsert_vectors("other", [item("new.md")], np.array([[0, 1]], np.float32))
        during = domain.receipt()
        assert during["state"] == "dirty"
        assert during["logical_projection"] == before
    after = domain.receipt()["logical_projection"]
    assert after["epoch"] == before["epoch"]
    assert after["generation"] == before["generation"] + 1
    assert all(after["tables"][name] == generation + 1 for name, generation in before["tables"].items())


def test_identical_content_is_no_logical_or_physical_mutation(corpus):
    _config, store = corpus
    domain = store._source_domain
    before = domain.receipt()
    assert store.upsert_content([item(), item("two.md")], skip_identical=True) == 0
    assert domain.receipt() == before


def test_vector_only_write_advances_only_its_model_table(corpus):
    config, store = corpus
    domain = store._source_domain
    before = domain.receipt()["logical_projection"]
    signature = cluster_signature(store, config)
    store.upsert_vectors("other", [item()], np.array([[0, 1]], np.float32))
    after = domain.receipt()["logical_projection"]
    model_table = config.model_profiles["other"].table_name
    assert after["generation"] == before["generation"] + 1
    assert after["tables"] == {
        name: generation + (name == model_table) for name, generation in before["tables"].items()
    }
    assert cluster_signature(store, config) != signature


def test_tombstone_advances_content_and_affected_model_generations(corpus):
    config, store = corpus
    before = store._source_domain.receipt()["logical_projection"]
    signature = cluster_signature(store, config)
    assert store.delete_by_file("git:sample", "one.md") == [item().chunk_id]
    after = store._source_domain.receipt()["logical_projection"]
    assert after["generation"] == before["generation"] + 1
    assert all(after["tables"][name] == generation + 1 for name, generation in before["tables"].items())
    assert cluster_signature(store, config) != signature
    assert engine._refresh_clusters(store, config)["clusters"] == 0


def test_scalar_preparation_preserves_logical_cluster_coverage_after_restart(corpus, monkeypatch):
    config, store = corpus
    assert engine._refresh_clusters(store, config)["clusters"] == 2
    before = store._source_domain.receipt()["logical_projection"]
    signature = cluster_signature(store, config)
    for owner in (store.content_store, *store._vector_tables.values()):
        assert prepare_chunk_id_index(owner, max_rows=10).prepared
    after = store._source_domain.receipt()["logical_projection"]
    assert after["witness"] != before["witness"]
    assert after["tables"] == before["tables"]
    assert after["generation"] == before["generation"]
    assert cluster_signature(store, config) == signature
    fresh = initialize_source_store(config)

    def forbidden(*args, **kwargs):
        raise AssertionError("physical-only maintenance invalidated logical cluster coverage")

    monkeypatch.setattr(fresh, "iter_item_representations", forbidden)
    assert engine._refresh_clusters(fresh, config)["maintenance_skipped"] == 1


@pytest.mark.parametrize("kind", ["content", "vector"])
def test_compaction_annotation_preserves_key_without_running_retention(corpus, monkeypatch, kind):
    config, store = corpus
    owner = store.content_store if kind == "content" else store.get_vector_table("small")
    table = owner._get_or_create_table()
    before = store._source_domain.receipt()["logical_projection"]
    signature = cluster_signature(store, config)
    calls = []

    def physical_rewrite(handle, **kwargs):
        calls.append(handle.name)
        handle.update(values={"indexed_at": 1.0})

    monkeypatch.setattr(type(table), "optimize", physical_rewrite)
    owner.compact()
    assert calls == [table.name]
    after = store._source_domain.receipt()["logical_projection"]
    assert after["witness"] != before["witness"]
    assert after["generation"] == before["generation"]
    assert after["tables"] == before["tables"]
    assert cluster_signature(store, config) == signature


def test_unknown_effect_overrides_physical_only_exemption(corpus):
    _config, store = corpus
    domain = store._source_domain
    table = store.content_store._get_or_create_table()
    before = domain.receipt()["logical_projection"]
    with domain.write(store.content_store.db):
        domain.effect("fixture-index", physical_only=table.name)
        table.create_index("content", config=FTS(), replace=True)
        domain.effect("unknown-writer")
    after = domain.receipt()["logical_projection"]
    assert after["generation"] == before["generation"] + 1
    assert after["tables"][table.name] == before["tables"][table.name] + 1


def test_writer_without_effect_does_not_inherit_physical_only_exemption(corpus):
    _config, store = corpus
    domain = store._source_domain
    table = store.content_store._get_or_create_table()
    before = domain.receipt()["logical_projection"]
    with domain.write(store.content_store.db):
        table.update(values={"indexed_at": 1.0})
    after = domain.receipt()["logical_projection"]
    assert after["tables"][table.name] == before["tables"][table.name] + 1


def test_older_writer_witness_drift_invalidates_then_starts_new_epoch(corpus):
    config, store = corpus
    domain = store._source_domain
    before = domain.receipt()
    signature = cluster_signature(store, config)
    table = store.content_store._get_or_create_table()
    # A previous binary can update its physical receipt while retaining unknown fields.
    with domain.exclusion():
        table.update(values={"indexed_at": 1.0})
        value = domain.receipt()
        value["versions"][table.name] = table.version
        domain.save(value)
    assert cluster_signature(store, config) != signature
    assert store.projection_snapshot()["logical_epoch"] is None
    store.upsert_content([item()], skip_identical=True)
    after = domain.receipt()["logical_projection"]
    assert after["epoch"] != before["logical_projection"]["epoch"]
    assert cluster_signature(store, config) != signature
    assert store.projection_snapshot()["logical_epoch"] == after["epoch"]


def test_failed_effect_does_not_publish_generation_or_relax_recovery(corpus):
    _config, store = corpus
    domain = store._source_domain
    before = domain.receipt()["logical_projection"]
    with pytest.raises(RuntimeError, match="fixture interruption"):
        with domain.write(store.content_store.db):
            store.upsert_content([item("new.md")])
            raise RuntimeError("fixture interruption")
    receipt = domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
    assert receipt["logical_projection"] == before
    with pytest.raises(SourceLayoutError):
        store.projection_snapshot()
    with pytest.raises(SourceLayoutError, match="uncertain effect"):
        validate_source_store(store)


@pytest.mark.parametrize("damage", ["missing", "malformed", "identity"])
def test_unproven_logical_metadata_uses_physical_key(corpus, damage):
    config, store = corpus
    domain = store._source_domain
    before = cluster_signature(store, config)
    value = domain.receipt()
    if damage == "missing":
        value.pop("logical_projection")
    elif damage == "malformed":
        value["logical_projection"]["tables"] = []
    else:
        witness = value["logical_projection"]["witness"][config.content_table]
        witness["storage"][1] += 1
    domain.save(value)
    assert store.projection_snapshot()["logical_epoch"] is None
    assert cluster_signature(store, config) != before
    assert initialize_source_store(config).projection_snapshot() == store.projection_snapshot()


def test_logical_metadata_budget_and_new_table_are_conservative():
    before = {"content": {"version": 1, "storage": [1, 2, 3]}}
    receipt = {}
    commit_projection(receipt, {}, before, physical_only={"content"})
    assert receipt["logical_projection"]["tables"] == {"content": 1}
    old = copy.deepcopy(receipt["logical_projection"])
    after = {"content": {"version": 2, "storage": [1, 2, 4]}}
    commit_projection(receipt, before, after, physical_only={"content"})
    assert receipt["logical_projection"]["tables"] == old["tables"]
    assert current_projection(receipt, after) is not None
    assert current_projection(receipt, before) is None
    receipt["fixture_padding"] = "x" * 65536
    commit_projection(receipt, after, after, physical_only=set())
    assert "logical_projection" not in receipt


@pytest.mark.parametrize("after", [
    {"version": 2, "storage": [1, 99, 4]},
    {"version": 1, "storage": [1, 2, 4]},
])
def test_physical_only_annotation_cannot_hide_replacement_or_rollback(after):
    before = {"content": {"version": 2, "storage": [1, 2, 3]}}
    receipt = {}
    commit_projection(receipt, {}, before, physical_only=set())
    previous = copy.deepcopy(receipt["logical_projection"])
    commit_projection(receipt, before, {"content": after}, physical_only={"content"})
    assert receipt["logical_projection"]["tables"]["content"] == previous["tables"]["content"] + 1


def test_table_removal_is_a_logical_change():
    before = {"content": {"version": 2, "storage": [1, 2, 3]}}
    receipt = {}
    commit_projection(receipt, {}, before, physical_only=set())
    generation = receipt["logical_projection"]["generation"]
    commit_projection(receipt, before, {}, physical_only={"content"})
    assert receipt["logical_projection"]["generation"] == generation + 1
    assert receipt["logical_projection"]["tables"] == {}
