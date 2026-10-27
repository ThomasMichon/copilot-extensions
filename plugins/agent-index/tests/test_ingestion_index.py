"""Ordinary publication owns point/file indices, not a fixture or a reader."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.ingestion_index import prepare_ingestion_indices
from agent_index.store.scalar_index import ScalarIndexPreparationRequired, inspect_index
from agent_index.store.source_layout import LAYOUT, initialize_source_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    return initialize_source_store(IndexConfig(
        data_dir=tmp_path, model_profiles={
            model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
            for model in ("a", "b")
        },
    ))


def test_empty_preparation_and_finite_tail_preserve_logical_generation(store) -> None:
    prepare_ingestion_indices(store, max_rows=0)
    chunk = Chunk("Useful content.", "a.md", "text", "markdown", 1, 1, "sample:repo")
    store.upsert_content([chunk])
    for model in store.model_ids:
        store.upsert_vectors(model, [chunk], np.ones((1, 2), dtype=np.float32))
    before = store.projection_snapshot()
    prepare_ingestion_indices(store, max_rows=1)
    assert store.projection_snapshot() == before
    for owner, columns in [
        (store.content_store, ("chunk_id", "file_path")),
        *((store.get_vector_table(model), ("chunk_id",)) for model in store.model_ids),
    ]:
        for column in columns:
            state, _ = inspect_index(owner._get_or_create_table(), column=column)
            assert state.prepared and state.physical_unindexed_rows == 0


def test_unprepared_nonempty_store_refuses_before_changing_any_index(store) -> None:
    chunk = Chunk("Useful content.", "a.md", "text", "markdown", 1, 1, "sample:repo")
    store.upsert_content([chunk])
    before = store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="budget"):
        prepare_ingestion_indices(store, max_rows=0)
    assert store._source_domain.receipt() == before
    assert not store.content_store._get_or_create_table().list_indices()


def test_segment_refusal_preflights_every_table_without_writes(store, monkeypatch) -> None:
    from agent_index.store import ingestion_index

    prepare_ingestion_indices(store, max_rows=0)
    before = store._source_domain.receipt()
    original = ingestion_index.inspect_index

    def at_limit(table, *, column):
        state, dataset = original(table, column=column)
        if table.name == store.get_vector_table("b")._table_name:
            state = replace(state, segments=ingestion_index.MAX_SEGMENTS)
        return state, dataset

    monkeypatch.setattr(ingestion_index, "inspect_index", at_limit)
    with pytest.raises(ScalarIndexPreparationRequired, match="consolidation"):
        prepare_ingestion_indices(store, max_rows=0, reserve_segment=True)
    assert store._source_domain.receipt() == before


def test_repeated_tails_are_covered_until_explicit_consolidation_is_due(store) -> None:
    from agent_index.store.scalar_index import MAX_SEGMENTS

    prepare_ingestion_indices(store, max_rows=0)
    content = store.content_store
    states = []
    for iteration in range(MAX_SEGMENTS + 1):
        state, dataset = inspect_index(content._get_or_create_table(), column="chunk_id")
        if state.segments == MAX_SEGMENTS:
            break
        retained = {
            segment.uuid for index in dataset.describe_indices() if index.name == "chunk_id_idx"
            for segment in index.segments if segment.fragment_ids
        }
        prepare_ingestion_indices(store, max_rows=0, reserve_segment=True)
        chunk = Chunk(
            f"Revision {iteration}.", "a.md", "text", "markdown", 1, 1, "sample:repo",
        )
        with store._source_domain.write(content.db):
            store.upsert_content([chunk])
            for model in store.model_ids:
                store.upsert_vectors(model, [chunk], np.ones((1, 2), dtype=np.float32))
            prepare_ingestion_indices(store, max_rows=1)
        after, dataset = inspect_index(content._get_or_create_table(), column="chunk_id")
        segments = {
            segment.uuid for index in dataset.describe_indices() if index.name == "chunk_id_idx"
            for segment in index.segments
        }
        assert retained <= segments  # An empty bootstrap segment may disappear.
        assert after.segments <= state.segments + 1
        assert after.physical_unindexed_rows == 0
        states.append(after)
    assert states and states[-1].segments == MAX_SEGMENTS
    before = store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="consolidation"):
        prepare_ingestion_indices(store, max_rows=0, reserve_segment=True)
    assert store._source_domain.receipt() == before


@pytest.mark.parametrize("budget", [-1, True, 1.5])
def test_invalid_preparation_budget(store, budget) -> None:
    with pytest.raises(ValueError, match="nonnegative integer"):
        prepare_ingestion_indices(store, max_rows=budget)
