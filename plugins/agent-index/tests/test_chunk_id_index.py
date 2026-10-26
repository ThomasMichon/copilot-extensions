"""Prepared point reads never hide full index builds or unindexed data scans."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.chunk_id_index import (
    INDEX_NAME,
    ChunkIdIndexPreparationRequired,
    prepare_chunk_id_index,
)
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store


def chunk(index: int) -> Chunk:
    return Chunk(f"text {index}", f"{index}.md", "text", "markdown", 1, 1, "sample:repo")


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2, table_name="vectors_a")},
    )
    store = initialize_source_store(config)
    chunks = [chunk(i) for i in range(5)]
    store.upsert("a", chunks, np.array([[i + 1, 1] for i in range(5)], dtype=np.float32))
    return store, chunks


def test_missing_index_requires_explicit_budget_without_poisoning_domain(corpus) -> None:
    store, chunks = corpus
    content = store.content_store
    before = store.projection_snapshot()
    with pytest.raises(ChunkIdIndexPreparationRequired, match="missing"):
        content.get_by_ids([chunks[0].chunk_id], bounded=True)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="budget"):
        prepare_chunk_id_index(content, max_rows=4)
    assert store.projection_snapshot() == before
    assert content.get_by_ids([chunks[0].chunk_id])[chunks[0].chunk_id].content == "text 0"
    assert store._source_domain.receipt()["state"] == "clean"


def test_preparation_is_idempotent_and_tail_is_explicit(corpus) -> None:
    store, chunks = corpus
    owners = [store.content_store, store.get_vector_table("a")]
    for owner in owners:
        state = prepare_chunk_id_index(owner, max_rows=5)
        assert state.prepared and state.physical_unindexed_rows == 0
        assert state.segments == 1
    before = store.projection_snapshot()
    for owner in owners:
        assert prepare_chunk_id_index(owner, max_rows=0).version == owner._table.version
    assert store.projection_snapshot() == before
    assert store.content_store.get_by_ids([chunks[0].chunk_id, "absent"], bounded=True).keys() == {
        chunks[0].chunk_id,
    }
    vectors = store.get_vector_table("a").get_vectors([chunks[0].chunk_id], bounded=True)
    np.testing.assert_array_equal(vectors[chunks[0].chunk_id], [1, 1])

    added = chunk(9)
    store.upsert("a", [added], np.array([[9, 1]], dtype=np.float32))
    before = store.projection_snapshot()
    with pytest.raises(ChunkIdIndexPreparationRequired, match="budget"):
        store.content_store.get_by_ids([added.chunk_id], bounded=True)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="budget"):
        store.get_vector_table("a").get_vectors([added.chunk_id], bounded=True)
    for owner in owners:
        with pytest.raises(ChunkIdIndexPreparationRequired, match="budget"):
            prepare_chunk_id_index(owner, max_rows=0)
    assert store.projection_snapshot() == before
    for owner in owners:
        state = prepare_chunk_id_index(owner, max_rows=1)
        assert state.segments == 2 and state.physical_unindexed_rows == 0
    assert store.content_store.get_by_ids([added.chunk_id], bounded=True)[added.chunk_id].content == "text 9"
    vectors = store.get_vector_table("a").get_vectors([added.chunk_id], bounded=True)
    np.testing.assert_array_equal(vectors[added.chunk_id], [9, 1])


def test_updated_and_deleted_rows_do_not_reappear(corpus) -> None:
    store, chunks = corpus
    content = store.content_store
    vector = store.get_vector_table("a")
    for owner in (content, vector):
        prepare_chunk_id_index(owner, max_rows=5)
    changed = replace(chunks[0], metadata={"fresh": True})
    store.upsert("a", [changed], np.array([[42, 1]], dtype=np.float32))
    for owner in (content, vector):
        prepare_chunk_id_index(owner, max_rows=1)
    assert content.get_by_ids([changed.chunk_id], bounded=True)[changed.chunk_id].metadata == {"fresh": True}
    np.testing.assert_array_equal(vector.get_vectors([changed.chunk_id], bounded=True)[changed.chunk_id], [42, 1])
    store.delete_by_file(changed.source, changed.file_path)
    assert content.get_by_ids([changed.chunk_id], bounded=True) == {}
    assert vector.get_vectors([changed.chunk_id], bounded=True) == {}


def test_index_only_preparation_preserves_source_fts_coverage(corpus) -> None:
    from lancedb.index import FTS

    store, chunks = corpus
    content = store.content_store
    domain = store._source_domain
    with domain.write(content.db):
        table = content._get_or_create_table()
        domain.effect("fts-builder")
        table.create_index("content", config=FTS())
        domain.cover_fts(content)
    assert content.fts_current() is True
    prepare_chunk_id_index(content, max_rows=5)
    assert content.fts_current() is True
    assert content.get_by_ids([chunks[0].chunk_id], bounded=True)
    assert domain.receipt()["fts_coverage"][content._table_name] == table.version


def test_no_legacy_writer_assertion_substitutes_for_participating_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="participating"):
        prepare_chunk_id_index(store.content_store, max_rows=100)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="participating"):
        store.content_store.get_by_ids(["not-there"], bounded=True)
    assert store.content_store.db.list_tables().tables == []


def test_incompatible_index_is_not_overwritten(corpus) -> None:
    from lancedb.index import BTree

    store, _chunks = corpus
    content = store.content_store
    with store._source_domain.write(content.db):
        table = content._get_or_create_table()
        store._source_domain.effect("scalar-index:" + content._table_name)
        table.create_index("source", config=BTree(), name=INDEX_NAME)
    before = store.projection_snapshot()
    with pytest.raises(ChunkIdIndexPreparationRequired, match="Incompatible"):
        prepare_chunk_id_index(content, max_rows=5)
    assert store.projection_snapshot() == before


def test_segment_budget_refuses_consolidation_and_keeps_domain_clean(
    corpus, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_index.store.chunk_id_index as indices

    store, _chunks = corpus
    content = store.content_store
    prepare_chunk_id_index(content, max_rows=5)
    store.upsert_content([chunk(9)])
    monkeypatch.setattr(indices, "MAX_SEGMENTS", 1)
    before = store.projection_snapshot()
    with pytest.raises(ChunkIdIndexPreparationRequired, match="consolidation"):
        prepare_chunk_id_index(content, max_rows=1)
    assert store.projection_snapshot() == before


@pytest.mark.parametrize("budget", [-1, 1.5, True])
def test_invalid_preparation_budget_is_rejected(corpus, budget: object) -> None:
    store, _chunks = corpus
    with pytest.raises(ValueError, match="nonnegative integer"):
        prepare_chunk_id_index(store.content_store, max_rows=budget)


def test_deleted_tail_is_budgeted_by_physical_not_live_rows(corpus) -> None:
    store, _chunks = corpus
    content = store.content_store
    prepare_chunk_id_index(content, max_rows=5)
    tail = [chunk(i) for i in range(10, 20)]
    store.upsert_content(tail)
    with store._source_domain.write(content.db):
        table = content._get_or_create_table()
        store._source_domain.effect("canonical-delete:" + content._table_name)
        table.delete(f"chunk_id != '{tail[0].chunk_id}' AND file_path LIKE '1%'")
    assert table.index_stats(INDEX_NAME).num_unindexed_rows < 10
    with pytest.raises(ChunkIdIndexPreparationRequired, match="10 physical rows"):
        prepare_chunk_id_index(content, max_rows=1)
    prepare_chunk_id_index(content, max_rows=10)
    records = content.get_by_ids([item.chunk_id for item in tail], bounded=True)
    assert set(records) == {tail[0].chunk_id}


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_actual_content_vector_point_reads_at_scale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int,
) -> None:
    import lancedb
    from lance.dataset import DatasetOptimizer

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2, table_name="vectors_a")},
    )
    store = initialize_source_store(config)
    chunks = [chunk(i) for i in range(size)]
    content = store.content_store
    vector = store.get_vector_table("a")
    store.upsert_content(chunks)
    prepare_chunk_id_index(content, max_rows=size)
    store.upsert_vectors("a", chunks, np.ones((size, 2), dtype=np.float32))
    prepare_chunk_id_index(vector, max_rows=size)

    def qualify_reads(ids: list[str], expected: set[str]) -> None:
        assert set(content.get_by_ids(ids, bounded=True)) == expected
        assert set(vector.get_vectors(ids, bounded=True)) == expected
        for owner in (content, vector):
            cold = lancedb.connect(config.lance_dir).open_table(owner._table_name)
            predicate = ", ".join(f"'{cid}'" for cid in ids)
            query = cold.search().where(f"chunk_id IN ({predicate})").limit(len(ids) + 1)
            if owner is vector:
                query = query.select(["chunk_id", "vector"])
            for _ in range(2):
                plan = query.analyze_plan()
                assert "ScalarIndexQuery" in plan, plan
                scanned = [int(value) for value in re.findall(r"\brows_scanned=(\d+)", plan)]
                assert sum(scanned) <= len(ids), plan
                assert sum(int(value) for value in re.findall(r"\biops=(\d+)", plan)) < 64, plan

    ids = [chunks[0].chunk_id, chunks[-1].chunk_id]
    qualify_reads(ids, set(ids))
    from agent_index.indexing.embedding_coverage import EmbeddingCoverage
    from agent_index.indexing.path_index import PathIndex

    coverage = EmbeddingCoverage(PathIndex(tmp_path / "coverage.db"))
    selected = [chunks[0], chunks[-1]]
    model = config.model_profiles["a"]
    coverage.record(vector, selected, model, np.ones((2, 2), dtype=np.float32), bounded=True)
    before = store.projection_snapshot()
    assert coverage.missing(vector, selected, model, bounded=True) == []
    assert store.projection_snapshot() == before
    optimize = DatasetOptimizer.optimize_indices
    measured = []

    def measured_optimize(self, **kwargs) -> None:
        self._dataset.io_stats_incremental()
        optimize(self, **kwargs)
        work = self._dataset.io_stats_incremental()
        assert work.read_bytes < 262_144 and work.read_iops < 32, (size, work)
        measured.append(work)

    monkeypatch.setattr(DatasetOptimizer, "optimize_indices", measured_optimize)
    added = chunk(size)
    store.upsert("a", [added], np.array([[7, 1]], dtype=np.float32))
    for owner in (content, vector):
        prepare_chunk_id_index(owner, max_rows=1)
    qualify_reads([ids[0], added.chunk_id], {ids[0], added.chunk_id})
    updated = replace(chunks[0], metadata={"fresh": True})
    store.upsert("a", [updated], np.array([[42, 1]], dtype=np.float32))
    for owner in (content, vector):
        prepare_chunk_id_index(owner, max_rows=1)
    qualify_reads([ids[0], added.chunk_id], {ids[0], added.chunk_id})
    assert content.get_by_ids([ids[0]], bounded=True)[ids[0]].metadata == {"fresh": True}
    np.testing.assert_array_equal(vector.get_vectors([ids[0]], bounded=True)[ids[0]], [42, 1])
    from agent_index.store.source_projection import delete_canonical

    with store._source_domain.write(content.db):
        delete_canonical(content, f"chunk_id = '{ids[0]}'")
    qualify_reads([ids[0], added.chunk_id], {added.chunk_id})
    assert len(measured) == 4


def test_empty_tables_can_be_prepared_without_a_data_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(data_dir=tmp_path)
    store = initialize_source_store(config)
    state = prepare_chunk_id_index(store.content_store, max_rows=0)
    assert state.prepared and state.physical_unindexed_rows == 0
    assert store.content_store.get_by_ids(["missing"], bounded=True) == {}


def test_duplicate_canonical_rows_are_not_truncated_to_a_success(corpus) -> None:
    store, chunks = corpus
    content = store.content_store
    with store._source_domain.write(content.db):
        table = content._get_or_create_table()
        duplicate = table.search().where(f"chunk_id = '{chunks[0].chunk_id}'").limit(1).to_list()
        store._source_domain.effect("content-upsert:" + content._table_name)
        table.add(duplicate)
    prepare_chunk_id_index(content, max_rows=6)
    with pytest.raises(RuntimeError, match="duplicate"):
        content.get_by_ids([chunks[0].chunk_id], bounded=True)


@pytest.mark.parametrize("prepared", [False, True])
def test_intervening_committed_append_refuses_without_dirtying_domain(
    corpus, monkeypatch: pytest.MonkeyPatch, prepared: bool,
) -> None:
    store, _chunks = corpus
    content = store.content_store
    domain = store._source_domain
    budget = 5
    if prepared:
        prepare_chunk_id_index(content, max_rows=5)
        store.upsert_content([chunk(10)])
        budget = 1
    write = domain.write
    appended = False

    @contextmanager
    def intervening_write(db) -> Iterator[None]:
        nonlocal appended
        if not appended:
            appended = True
            store.upsert_content([chunk(20)])
        with write(db):
            yield

    monkeypatch.setattr(domain, "write", intervening_write)
    with pytest.raises(ChunkIdIndexPreparationRequired, match="budget"):
        prepare_chunk_id_index(content, max_rows=budget)
    assert appended
    receipt = domain.receipt()
    assert receipt["state"] == "clean" and not receipt.get("uncertain_effect")
    assert receipt["versions"][content._table_name] == content._table.version
    assert content.get_by_ids([chunk(20).chunk_id])
    store.upsert_content([chunk(21)])
    assert content.get_by_ids([chunk(21).chunk_id])
    assert domain.receipt()["state"] == "clean"


@pytest.mark.parametrize("failure", [RuntimeError, ChunkIdIndexPreparationRequired])
def test_post_effect_failure_keeps_existing_uncertain_receipt(
    corpus, monkeypatch: pytest.MonkeyPatch, failure: type[RuntimeError],
) -> None:
    from lance.dataset import DatasetOptimizer

    store, _chunks = corpus
    content = store.content_store
    prepare_chunk_id_index(content, max_rows=5)
    store.upsert_content([chunk(10)])

    def fail_after_effect(*_args: object, **_kwargs: object) -> None:
        raise failure("fixture index write failure")

    monkeypatch.setattr(DatasetOptimizer, "optimize_indices", fail_after_effect)
    with pytest.raises(RuntimeError, match="fixture index write failure"):
        prepare_chunk_id_index(content, max_rows=1)
    receipt = store._source_domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
    assert receipt["operation"] == "scalar-index:" + content._table_name
