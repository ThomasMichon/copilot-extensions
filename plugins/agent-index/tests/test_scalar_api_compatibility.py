"""Scalar preparation retains the older public API without retrying a write."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.chunk_id_index import prepare_chunk_id_index
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.filterwarnings("ignore:create_scalar_index is deprecated")
def test_preparation_works_without_the_general_index_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2, table_name="vectors_a")},
    )
    create_store = cast("Callable[[IndexConfig], MultiModelStore]", initialize_source_store)
    store = create_store(config)
    chunks = [
        Chunk(f"text {i}", f"{i}.md", "text", "markdown", 1, 1, "sample:repo")
        for i in range(5)
    ]
    store.upsert("a", chunks, np.array([[i + 1, 1] for i in range(5)], dtype=np.float32))

    def unsupported(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("scalar preparation used the vector-only API")

    monkeypatch.setattr(import_module("lancedb.table").LanceTable, "create_index", unsupported)
    for owner in (store.content_store, store.get_vector_table("a")):
        state = prepare_chunk_id_index(owner, max_rows=len(chunks))
        assert state.prepared and state.physical_unindexed_rows == 0
    assert set(store.content_store.get_by_ids([chunks[0].chunk_id], bounded=True)) == {
        chunks[0].chunk_id,
    }
    vectors = store.get_vector_table("a").get_vectors([chunks[0].chunk_id], bounded=True)
    np.testing.assert_array_equal(vectors[chunks[0].chunk_id], [1, 1])
    assert store._source_domain is not None
    assert store._source_domain.receipt()["state"] == "clean"
