"""Explicit native consolidation never borrows an ordinary ingestion budget."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.ingestion_index import (
    consolidate_ingestion_indices,
    prepare_ingestion_indices,
)
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.scalar_index import (
    ScalarIndexPreparationRequired,
    consolidate_index,
    inspect_index,
)
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MultiModelStore:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    return initialize_source_store(IndexConfig(
        data_dir=tmp_path, model_profiles={
            model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
            for model in ("a", "b")
        },
    ))


def append(store: MultiModelStore, iteration: int, *, prepare: bool = True) -> Chunk:
    chunk = Chunk(
        f"Useful revision {iteration}.", f"{iteration}.md", "text", "markdown",
        1, 1, "sample:repo",
    )
    with store._source_domain.write(store.content_store.db):
        store.upsert_content([chunk])
        for model in store.model_ids:
            store.upsert_vectors(model, [chunk], np.array([[iteration, 1]], dtype=np.float32))
        if prepare:
            prepare_ingestion_indices(store, max_rows=1)
    return chunk


@pytest.mark.parametrize("current_fts", [False, True])
def test_whole_store_consolidation_preserves_rows_versions_generation_and_fts(
    store: MultiModelStore, monkeypatch: pytest.MonkeyPatch, current_fts: bool,
) -> None:
    import lance
    from lance.dataset import DatasetOptimizer

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("explicit index consolidation compacted data or pruned versions")

    monkeypatch.setattr(DatasetOptimizer, "compact_files", forbidden)
    monkeypatch.setattr(lance.LanceDataset, "cleanup_old_versions", forbidden)
    chunks = [append(store, i) for i in range(3)]
    # Include an unindexed tail: consolidation must cover it as well as merge.
    chunks.append(append(store, 3, prepare=False))
    if current_fts:
        assert store.content_store.ensure_fts_index()
    before = store.projection_snapshot()
    owners = [store.content_store, *(store.get_vector_table(m) for m in store.model_ids)]
    pinned = {owner._table_name: owner._get_or_create_table().version for owner in owners}
    fragments = {
        owner._table_name: owner._get_or_create_table().to_lance().get_fragments()
        for owner in owners
    }
    consolidate_ingestion_indices(store, max_rows=4)
    assert store.projection_snapshot() == before
    if current_fts:
        assert store._source_domain.require_fts(store.content_store)
        assert store.content_store.fts_search("Useful", limit=10)
    else:
        with pytest.raises(SourceLayoutError, match="FTS is stale"):
            store._source_domain.require_fts(store.content_store)
    for owner in owners:
        table = owner._get_or_create_table()
        for column in (("chunk_id", "file_path") if owner is store.content_store else ("chunk_id",)):
            state, _ = inspect_index(table, column=column)
            assert state.segments == 1 and state.physical_unindexed_rows == 0
        assert [f.metadata for f in table.to_lance().get_fragments()] == [
            f.metadata for f in fragments[owner._table_name]
        ]
        old = owner._db.open_table(owner._table_name)
        old.checkout(pinned[owner._table_name])
        assert old.count_rows() == 4
    records = store.content_store.get_by_ids([c.chunk_id for c in chunks], bounded=True)
    assert set(records) == {c.chunk_id for c in chunks}
    for model in store.model_ids:
        actual = store.get_vector_table(model).get_vectors([chunks[-1].chunk_id], strict=True)
        np.testing.assert_array_equal(actual[chunks[-1].chunk_id], [3, 1])
    receipt = store._source_domain.receipt()
    consolidate_ingestion_indices(store, max_rows=0)
    assert store._source_domain.receipt() == receipt


def test_deleted_physical_rows_still_consume_consolidation_budget(store: MultiModelStore) -> None:
    chunks = [
        Chunk(f"Useful {i}.", f"{i}.md", "text", "markdown", 1, 1, "sample:repo")
        for i in range(2)
    ]
    store.upsert_content(chunks)
    for model in store.model_ids:
        store.upsert_vectors(model, chunks, np.ones((2, 2), dtype=np.float32))
    prepare_ingestion_indices(store, max_rows=2)
    chunks.append(append(store, 2))
    store.delete_by_file("sample:repo", chunks[0].file_path)
    table = store.content_store._get_or_create_table()
    assert table.count_rows() == 2
    assert sum(f.metadata.physical_rows for f in table.to_lance().get_fragments()) == 3
    before = store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="needs 3 physical rows"):
        consolidate_ingestion_indices(store, max_rows=2)
    assert store._source_domain.receipt() == before
    consolidate_ingestion_indices(store, max_rows=3)
    assert set(store.content_store.get_by_ids([c.chunk_id for c in chunks], bounded=True)) == {
        chunks[1].chunk_id, chunks[2].chunk_id,
    }


def test_late_model_budget_refuses_before_any_index_changes(store: MultiModelStore) -> None:
    chunks = [
        Chunk(f"Useful {i}.", f"{i}.md", "text", "markdown", 1, 1, "sample:repo")
        for i in range(3)
    ]
    for chunk in chunks:
        store.upsert_content([chunk])
        store.upsert_vectors("a", [chunk], np.ones((1, 2), dtype=np.float32))
    store.upsert_vectors("b", chunks, np.ones((3, 2), dtype=np.float32))
    prepare_ingestion_indices(store, max_rows=3)
    append(store, 3)
    store.delete_by_file("sample:repo", chunks[0].file_path)
    assert sum(
        f.metadata.physical_rows for f in store.content_store._get_or_create_table().to_lance().get_fragments()
    ) == 3
    before = store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="needs 4 physical rows"):
        consolidate_ingestion_indices(store, max_rows=3)
    assert store._source_domain.receipt() == before


@pytest.mark.parametrize("failure", ["native", "coverage", "segments"])
def test_uncertain_effect_or_failed_postcondition_retains_dirty_owner(
    store: MultiModelStore, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from dataclasses import replace

    from lance.dataset import DatasetOptimizer
    from agent_index.store import scalar_index

    for i in range(2):
        append(store, i)
    if failure == "native":
        def fail(*args: object, **kwargs: object) -> None:
            raise RuntimeError("fixture index merge failed")
        monkeypatch.setattr(DatasetOptimizer, "optimize_indices", fail)
    else:
        original = scalar_index.inspect_index
        calls = 0

        def invalid_after(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            state, dataset = original(*args, **kwargs)
            if calls == 3:
                state = replace(state, **{
                    "physical_unindexed_rows": 1,
                } if failure == "coverage" else {"segments": 2})
            return state, dataset

        monkeypatch.setattr(scalar_index, "inspect_index", invalid_after)
    with pytest.raises(RuntimeError, match="merge failed|fully covered"):
        consolidate_index(store.content_store, column="chunk_id", max_rows=2)
    assert store._source_domain.receipt()["state"] == "dirty"
    assert store._source_domain.receipt()["uncertain_effect"]
    with pytest.raises(SourceLayoutError):
        store.projection_snapshot()


def test_missing_index_requires_explicit_preparation(store: MultiModelStore) -> None:
    before = store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="existing prepared index"):
        consolidate_ingestion_indices(store, max_rows=0)
    assert store._source_domain.receipt() == before


def test_writer_rechecks_budget_before_index_effect(
    store: MultiModelStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_index.store import scalar_index

    for i in range(2):
        append(store, i)
    before = store._source_domain.receipt()
    original = scalar_index.consolidation_rows
    calls = 0

    def raced(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ScalarIndexPreparationRequired("fixture intervening budget change")
        return original(*args, **kwargs)

    monkeypatch.setattr(scalar_index, "consolidation_rows", raced)
    with pytest.raises(ScalarIndexPreparationRequired, match="intervening budget"):
        consolidate_index(store.content_store, column="chunk_id", max_rows=2)
    assert calls == 2
    assert store._source_domain.receipt() == before


@pytest.mark.parametrize("budget", [-1, True, 1.5])
def test_invalid_consolidation_budget(store: MultiModelStore, budget: Any) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        consolidate_ingestion_indices(store, max_rows=budget)
    with pytest.raises(ValueError, match="nonnegative integer"):
        consolidate_index(store.content_store, column="chunk_id", max_rows=budget)


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_consolidation_qualifies_whole_index_cost_not_delta_cost(
    store: MultiModelStore, monkeypatch: pytest.MonkeyPatch, size: int,
) -> None:
    from lance.dataset import DatasetOptimizer

    content = store.content_store
    content_chunks = [
        Chunk(f"Useful item {i}.", f"{i}.md", "text", "markdown", 1, 1, "sample:repo")
        for i in range(size)
    ]
    store.upsert_content(content_chunks)
    prepare_ingestion_indices(store, max_rows=size)
    added = Chunk("New item.", "new.md", "text", "markdown", 1, 1, "sample:repo")
    store.upsert_content([added])
    prepare_ingestion_indices(store, max_rows=1)
    state, _ = inspect_index(content._get_or_create_table(), column="chunk_id")
    assert state.segments == 2
    original = DatasetOptimizer.optimize_indices
    work = []

    def measured(optimizer: DatasetOptimizer, **kwargs: Any) -> None:
        assert kwargs == {
            "index_names": ["chunk_id_idx"], "num_indices_to_merge": 2, "retrain": False,
        }
        optimizer._dataset.io_stats_incremental()
        original(optimizer, **kwargs)
        work.append(optimizer._dataset.io_stats_incremental())

    monkeypatch.setattr(DatasetOptimizer, "optimize_indices", measured)
    receipt = store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match=f"needs {size + 1} physical rows"):
        consolidate_index(content, column="chunk_id", max_rows=1)
    assert work == [] and store._source_domain.receipt() == receipt
    result = consolidate_index(content, column="chunk_id", max_rows=size + 1)
    assert result.segments == 1 and result.physical_unindexed_rows == 0
    assert len(work) == 1 and work[0].read_bytes > 0
    # This is explicitly corpus-sized maintenance, not the ordinary delta bound.
    assert work[0].read_bytes < (size + 1) * 1024 + 262_144
    print(f"consolidation physical_rows={size + 1} native_io={work[0]}")
    assert content._get_or_create_table().count_rows() == size + 1
    assert set(content.get_by_ids([
        content_chunks[0].chunk_id, content_chunks[-1].chunk_id, added.chunk_id,
    ], bounded=True)) == {
        content_chunks[0].chunk_id, content_chunks[-1].chunk_id, added.chunk_id,
    }
