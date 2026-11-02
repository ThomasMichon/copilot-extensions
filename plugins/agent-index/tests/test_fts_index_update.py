"""Native index-only FTS updates preserve canonical fragments and unrelated indices."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.store.fts_index_update import update_fts_index
from agent_index.store.scalar_index import prepare_index
from agent_index.store.source_layout import LAYOUT, initialize_source_store


@pytest.mark.parametrize("indices", [[], [
    SimpleNamespace(index_type="FTS", columns=["content"], name="one"),
    SimpleNamespace(index_type="FTS", columns=["content"], name="two"),
]])
def test_missing_or_ambiguous_fts_refuses_before_native_update(indices: list[object]) -> None:
    with pytest.raises(RuntimeError, match="exactly one content index"):
        update_fts_index(SimpleNamespace(list_indices=lambda: indices))


@pytest.mark.parametrize("stats", [None, SimpleNamespace(num_unindexed_rows=1)])
def test_unproven_native_update_cannot_report_success(stats: object) -> None:
    table = SimpleNamespace(
        list_indices=lambda: [SimpleNamespace(
            index_type="FTS", columns=["content"], name="content_idx",
        )],
        to_lance=lambda: SimpleNamespace(optimize=SimpleNamespace(
            optimize_indices=lambda **kwargs: None,
        )),
        checkout_latest=lambda: None,
        index_stats=lambda _name: stats,
    )
    with pytest.raises(RuntimeError, match="did not cover"):
        update_fts_index(table)


def test_native_fts_only_update_preserves_scalar_segments_and_fragments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lance.dataset import DatasetOptimizer
    import lance

    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path, cluster_enabled=False,
        model_profiles={"a": ModelProfile("a", "fixture", dim=2, table_name="vectors_a")},
    )
    store = initialize_source_store(config)
    chunks = [
        Chunk("originalword", f"{i}.md", "text", "markdown", 1, 1, "sample:repo")
        for i in range(100)
    ]
    store.upsert("a", chunks, np.ones((100, 2), dtype=np.float32))
    store.ensure_fts_index()
    content = store.content_store
    prepare_index(content, column="chunk_id", max_rows=100)
    table = content._get_or_create_table()
    before_version = table.version
    def scalar_segments() -> list[tuple[str, list[int]]]:
        return [
            (segment.uuid, segment.fragment_ids)
            for index in table.to_lance().describe_indices() if index.name == "chunk_id_idx"
            for segment in index.segments
        ]

    original_segments = scalar_segments()
    changed = replace(chunks[0], content="replacementword")
    store.upsert("a", [changed], np.array([[2, 1]], dtype=np.float32))
    store.reconcile_file_chunks("sample:repo", changed.file_path, {changed.chunk_id})
    before = table.to_lance()
    fragments = [fragment.fragment_id for fragment in before.get_fragments()]
    fts_name = next(index.name for index in table.list_indices() if index.index_type == "FTS")
    assert table.index_stats(fts_name).num_unindexed_rows == 1
    optimize = DatasetOptimizer.optimize_indices
    calls: list[dict[str, object]] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ordinary FTS invoked compaction, retention or rebuilding")

    def measured(self: DatasetOptimizer, **kwargs: object) -> None:
        calls.append(kwargs)
        optimize(self, **kwargs)

    monkeypatch.setattr(DatasetOptimizer, "optimize_indices", measured)
    monkeypatch.setattr(DatasetOptimizer, "compact_files", forbidden)
    monkeypatch.setattr(lance.LanceDataset, "cleanup_old_versions", forbidden)
    monkeypatch.setattr(type(table), "create_fts_index", forbidden)
    domain = store._source_domain
    with domain.write(content.db):
        with domain.physical_maintenance(content._table_name, "fts-builder"):
            update_fts_index(table)
        domain.cover_fts(content)
    assert len(calls) == 1
    assert calls[0]["num_indices_to_merge"] == 0
    assert calls[0]["retrain"] is False
    assert calls[0]["index_names"] == [
        index.name for index in table.list_indices() if index.index_type == "FTS"
    ]
    current = table.to_lance()
    assert [fragment.fragment_id for fragment in current.get_fragments()] == fragments
    assert scalar_segments() == original_segments
    assert table.index_stats("chunk_id_idx").num_unindexed_rows == 1
    assert table.index_stats(fts_name).num_unindexed_rows == 0
    assert [cid for cid, _score in content.fts_search("replacementword")] == [changed.chunk_id]
    assert chunks[0].chunk_id not in [
        cid for cid, _score in content.fts_search("originalword", limit=200)
    ]
    old = content.db.open_table(content._table_name)
    old.checkout(before_version)
    assert old.count_rows() == 100
