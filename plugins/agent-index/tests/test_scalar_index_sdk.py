"""Qualify index-only SDK maintenance against the supported native store."""

from __future__ import annotations

import re
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store


@pytest.mark.parametrize("selected", [False, True])
def test_index_only_sdk_preserves_native_content_vectors_and_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool,
) -> None:
    import lance
    from lance.dataset import DatasetOptimizer
    from lancedb.index import BTree

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    config = IndexConfig(
        data_dir=tmp_path,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2, table_name="vectors_a")},
    )
    store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
    store.register_model(config.model_profiles["a"])
    domain = store._source_domain
    def write() -> AbstractContextManager[None]:
        return domain.write(store.content_store.db) if domain is not None else nullcontext()

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("index qualification invoked compaction or version cleanup")

    monkeypatch.setattr(DatasetOptimizer, "compact_files", forbidden)
    monkeypatch.setattr(lance.LanceDataset, "cleanup_old_versions", forbidden)
    initial = [
        Chunk(f"initial-{i}", f"{i}.md", "text", "markdown", 1, 1, "sample:repo")
        for i in range(100)
    ]
    vectors = np.array([[i + 1, 1] for i in range(100)], dtype=np.float32)
    store.upsert("a", initial, vectors)
    owners = [store.content_store, store.get_vector_table("a")]
    pinned = []
    for owner in owners:
        table = owner._get_or_create_table()
        pinned.append((owner._table_name, table.version))
        with write():
            if domain is not None:
                domain.effect("scalar-index:" + owner._table_name)
            table.create_index("chunk_id", config=BTree(), name="chunk_id_idx")
    added = Chunk("added", "added.md", "text", "markdown", 1, 1, "sample:repo")
    store.upsert("a", [added], np.array([[101, 1]], dtype=np.float32))

    for owner in owners:
        with write():
            table = owner._get_or_create_table()
            table.checkout_latest()
            sdk = table.to_lance()
            before = table.index_stats("chunk_id_idx")
            assert before.num_indexed_rows == 100 and before.num_unindexed_rows == 1
            if domain is not None:
                domain.effect("scalar-index:" + owner._table_name)
            sdk.optimize.optimize_indices(index_names=["chunk_id_idx"], num_indices_to_merge=0)
            table.checkout_latest()
            after = table.index_stats("chunk_id_idx")
            assert after.num_unindexed_rows == 0
            assert after.num_indexed_rows == 101
            assert table.count_rows() == 101

    records = store.content_store.get_by_ids([initial[0].chunk_id, added.chunk_id])
    assert records[initial[0].chunk_id].content == initial[0].content
    assert records[added.chunk_id].content == added.content
    actual = store.get_vector_table("a").get_vectors([added.chunk_id], strict=True)
    np.testing.assert_array_equal(actual[added.chunk_id], [101, 1])
    for name, version in pinned:
        table = store.content_store.db.open_table(name)
        table.checkout(version)
        assert table.count_rows() == 100


@pytest.mark.parametrize("size", [100, 10_000, 100_000])
def test_index_only_sdk_scales_with_new_tail_not_base_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int,
) -> None:
    import lancedb
    from lancedb.index import BTree

    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    db = lancedb.connect(tmp_path / "native")
    table = db.create_table("points", pa.table({
        "chunk_id": [f"item-{i:08}" for i in range(size)],
        "content": [f"synthetic text {i}" for i in range(size)],
    }))
    table.create_index("chunk_id", config=BTree(), name="chunk_id_idx")
    original_version = table.version
    table.add([{"chunk_id": "new-point", "content": "new text"}])
    dataset = table.to_lance()
    dataset.io_stats_incremental()
    dataset.optimize.optimize_indices(
        index_names=["chunk_id_idx"], num_indices_to_merge=0, retrain=False,
    )
    work = dataset.io_stats_incremental()
    assert work.read_bytes < 262_144, (size, work)
    assert work.read_iops < 32, (size, work)
    table.checkout_latest()
    assert table.index_stats("chunk_id_idx").num_unindexed_rows == 0
    assert table.index_stats("chunk_id_idx").num_indices == 2

    predicate = f"chunk_id IN ('item-{size - 1:08}', 'new-point')"
    query = table.search().where(predicate).select(["chunk_id", "content"]).limit(2)
    for _ in range(2):
        plan = query.analyze_plan()
        assert "ScalarIndexQuery" in plan
        scanned = [int(value) for value in re.findall(r"\brows_scanned=(\d+)", plan)]
        assert scanned and sum(scanned) == 2, plan
    old = db.open_table("points")
    old.checkout(original_version)
    assert old.count_rows() == size
