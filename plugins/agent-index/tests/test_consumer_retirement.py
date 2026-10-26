"""Consumer inventories do not silently retire pushed content or crawl cursors."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig
from agent_index.indexing import engine
from agent_index.indexing.embedding_runtime import EmbeddingRuntime
from agent_index.indexing.gc import SourceRetirementPolicy, gc_stale_sources
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources import register_connector
from agent_index.sources.delta import SourceDelta
from agent_index.store.multi_model_store import MultiModelStore


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("unselected engine or maintenance work")


class EmptyConnector:
    source_name = "sample:feed"
    recovery_identity = ("https://example.test/feed", "account-1")

    def discover(self, **kwargs: object) -> list:
        return []

    def discover_delta(self, previous: str | None, **kwargs: object) -> SourceDelta:
        return SourceDelta(commit="current")

    def current_commit(self) -> str:
        return "current"


def chunk(source: str) -> Chunk:
    return Chunk("retained text", "item.md", "text", "text", 1, 1, source=source)


@pytest.mark.parametrize("custom, full, collect_stale", [
    (False, False, True), (True, False, True),
    (True, True, False), (True, True, True),
])
def test_actual_engine_retirement_policy_preserves_external_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    custom: bool, full: bool, collect_stale: bool,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    monkeypatch.setenv("AGENT_INDEX_REINDEX_GC", "1")
    from agent_index import sources

    monkeypatch.setattr(sources, "_CONNECTORS", dict(sources._CONNECTORS))
    register_connector("sample", lambda **kwargs: EmptyConnector())
    config = IndexConfig(data_dir=tmp_path, model_profiles={}, cluster_enabled=False)
    config.ensure_dirs()
    store = MultiModelStore(config.lance_dir)
    pushed = chunk("pushed:independent")
    stale = chunk("sample:obsolete")
    store.upsert_content([pushed, stale])
    monkeypatch.setattr(MultiModelStore, "compact", lambda _self: {})
    policy = SourceRetirementPolicy(
        configured_snapshot=False,
        stale_generations=collect_stale,
        is_live_source=lambda source: source != stale.source,
    ) if custom else None

    result = engine.run_reindex(
        full=full, config=config,
        source_specs=[engine.SourceSpec("sample:feed", type="sample")],
        embedding_runtime=EmbeddingRuntime(forbidden, forbidden, forbidden),
        retirement_policy=policy,
    )
    assert "sources_failed" not in result
    records = store.content_store.get_by_ids([pushed.chunk_id, stale.chunk_id])
    assert (pushed.chunk_id in records) is custom
    assert (stale.chunk_id in records) is (custom and not (full and collect_stale))


def test_custom_stale_classifier_retains_crawl_alias_and_shared_pending_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(data_dir=tmp_path)
    config.ensure_dirs()
    store = MultiModelStore(config.lance_dir)
    paths = PathIndex(tmp_path / "paths.db")
    stale = chunk("sample:feed")
    store.upsert_content([stale])
    paths.mark_indexed(stale.source, stale.file_path)
    state = IndexState(sources={
        "sample:feed": SourceState(last_commit="keep-cursor", chunk_count=1),
    })
    policy = SourceRetirementPolicy(
        configured_snapshot=False, is_live_source=lambda source: False,
        live_crawl_keys=frozenset({"sample:feed"}),
    )
    journal = PendingCrawls(paths)
    journal.save(CrawlManifest(source="other"), scope="fixture", full=False)
    with pytest.raises(PendingCrawlConflict, match="before source garbage collection"):
        gc_stale_sources(store, paths, state, policy=policy)
    assert store.content_store.get_by_ids([stale.chunk_id])
    assert paths.get_entry(stale.source, stale.file_path) is not None
    journal.clear("other")

    preview = gc_stale_sources(store, paths, state, policy=policy, dry_run=True)
    assert preview["purged"] == {"sample:feed": 1}
    assert store.content_store.get_by_ids([stale.chunk_id])
    summary = gc_stale_sources(store, paths, state, policy=policy)
    assert summary["chunks_deleted"] == 1
    assert not store.content_store.get_by_ids([stale.chunk_id])
    assert paths.get_entry(stale.source, stale.file_path) is None
    assert state.sources["sample:feed"].last_commit == "keep-cursor"


def test_failed_full_run_keeps_last_completed_reconciliation_and_skips_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    config = IndexConfig(data_dir=tmp_path, model_profiles={}, cluster_enabled=False)
    config.ensure_dirs()
    state = IndexState(last_full_reindex=123.0)
    state.save(config.state_file)
    compact = Mock(side_effect=forbidden)
    stale_gc = Mock(side_effect=forbidden)
    monkeypatch.setattr(MultiModelStore, "compact", compact)
    monkeypatch.setattr("agent_index.indexing.gc.gc_stale_sources", stale_gc)

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("source acquisition failed")

    monkeypatch.setattr(engine, "_index_source", fail)
    result = engine.run_reindex(
        full=True, source="sample:feed", config=config,
        source_specs=[engine.SourceSpec("sample:feed", type="sample")],
        embedding_runtime=EmbeddingRuntime(forbidden, forbidden, forbidden),
        retirement_policy=replace(SourceRetirementPolicy(), configured_snapshot=False),
    )
    assert result["sources_failed"][0]["source"] == "sample:feed"
    assert IndexState.load(config.state_file).last_full_reindex == 123.0
    compact.assert_not_called()
    stale_gc.assert_not_called()


def test_disabled_direct_stale_collection_does_not_scan_or_mutate() -> None:
    result = gc_stale_sources(
        None, None, None,
        policy=SourceRetirementPolicy(stale_generations=False),
    )
    assert result == {"purged": {}, "kept": [], "chunks_deleted": 0, "dry_run": False}


def test_operator_keep_override_precedes_custom_stale_classifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    monkeypatch.setenv("AGENT_INDEX_GC_KEEP_SOURCES", "pushed:protected")
    config = IndexConfig(data_dir=tmp_path)
    config.ensure_dirs()
    store = MultiModelStore(config.lance_dir)
    kept = chunk("pushed:protected")
    stale = chunk("sample:obsolete")
    store.upsert_content([kept, stale])
    classified: list[str] = []

    def classifier(source: str) -> bool:
        classified.append(source)
        return False

    summary = gc_stale_sources(
        store, PathIndex(tmp_path / "paths.db"), IndexState(),
        policy=SourceRetirementPolicy(is_live_source=classifier),
    )
    assert classified == [stale.source]
    assert summary["purged"] == {stale.source: 1}
    assert summary["kept"] == [kept.source]
    assert store.content_store.get_by_ids([kept.chunk_id])
    assert not store.content_store.get_by_ids([stale.chunk_id])
