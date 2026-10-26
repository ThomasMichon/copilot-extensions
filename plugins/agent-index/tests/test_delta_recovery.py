"""Completed file checkpoints cover cleanup and replay-safe source totals."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.store.multi_model_store import MultiModelStore


def fail(*args: object, **kwargs: object) -> None:
    raise RuntimeError("fixture failure")


def setup_round(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState(sources={"alias": SourceState(last_commit="old")})
    connector = SimpleNamespace(
        source_name="forge:repo", recovery_identity=("fixture-target",),
        delta=SourceDelta(commit="next"),
    )
    connector.discover_delta = lambda *a, **k: connector.delta
    state.sources["alias"].acquisition_scope = recovery_scope("alias", connector)
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: connector)
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    chunker = SimpleNamespace(chunk=lambda *a, **k: [object(), object()])
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda path: (chunker, "text"))
    monkeypatch.setattr(engine, "_embed_and_store_batch", lambda batch, *a, **k: len(batch))
    store = SimpleNamespace(
        delete_stale_by_file=lambda *a, **k: 0, delete_by_file=lambda *a: [],
    )

    def run() -> tuple[int, int, int]:
        return engine._index_source(
            "alias", config=SimpleNamespace(
                model_profiles={}, stream_batch_size=2, state_file=tmp_path / "state.json",
            ),
            state=state, path_index=paths, multi_store=store, multi_clients={}, full=False,
        )

    return paths, state, connector, store, run


@pytest.mark.parametrize("target", ["store", "path"])
def test_tombstone_failure_retains_cursor_for_replay(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target: str,
) -> None:
    paths, state, connector, store, run = setup_round(monkeypatch, tmp_path)
    paths.mark_indexed("forge:repo", "gone.md", chunk_count=2)
    paths.mark_indexed("forge:repo", "keep.md", chunk_count=3)
    connector.delta = SourceDelta(deletions=[DeletedFile("forge:repo", "gone.md")], commit="next")
    with monkeypatch.context() as patch:
        patch.setattr(store if target == "store" else paths,
                      "delete_by_file" if target == "store" else "remove", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            run()
    assert state.sources["alias"].last_commit == "old"
    assert paths.get_entry("forge:repo", "gone.md") is not None
    run()
    assert state.sources["alias"].last_commit == "next"
    assert state.sources["alias"].chunk_count == state.total_chunks == 3
    run()
    assert state.total_chunks == 3


@pytest.mark.parametrize("interruption", ["cleanup", "later-batch"])
def test_retry_only_reuses_files_whose_cleanup_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, interruption: str,
) -> None:
    paths, state, connector, store, run = setup_round(monkeypatch, tmp_path)
    first = FileEntry(source="forge:repo", path="first.md", content="new", language="markdown")
    second = replace(first, path="second.md")
    connector.delta = SourceDelta(upserts=[first, second], commit="next")
    cleaned: list[str] = []

    def cleanup(source: str, path: str, **kwargs: object) -> int:
        cleaned.append(path)
        return 0

    monkeypatch.setattr(store, "delete_stale_by_file", cleanup)
    batches: list[int] = []

    def embed(batch: list[object], *args: object, **kwargs: object) -> int:
        batches.append(len(batch))
        if interruption == "later-batch" and len(batches) == 2:
            fail()
        return len(batch)

    with monkeypatch.context() as patch:
        patch.setattr(engine, "_embed_and_store_batch", embed)
        if interruption == "cleanup":
            patch.setattr(store, "delete_stale_by_file", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            run()
    assert state.sources["alias"].last_commit == "old"
    assert (paths.get_entry(first.source, first.path) is not None) == (
        interruption == "later-batch"
    )
    assert paths.get_entry(second.source, second.path) is None
    assert run()[0] == (2 if interruption == "later-batch" else 4)
    assert cleaned == ["first.md", "second.md"]
    assert state.sources["alias"].chunk_count == state.total_chunks == 4


def test_retained_replaced_deleted_and_replayed_file_totals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths, state, connector, store, run = setup_round(monkeypatch, tmp_path)
    first = FileEntry(source="forge:repo", path="first.md", content="old", language="markdown")
    second = replace(first, path="second.md")
    connector.delta = SourceDelta(upserts=[first, second], commit="initial")
    run()
    assert state.total_chunks == 4
    connector.delta = SourceDelta(upserts=[replace(first, content="changed"), second], commit="next")
    assert run()[0] == 2
    assert state.total_chunks == 4
    paths.mark_indexed("forge:repo:issues", "1", chunk_count=3)
    connector.delta = SourceDelta(deletions=[DeletedFile(first.source, first.path)], commit="delete")
    run()
    assert state.total_chunks == 5
    run()
    assert state.total_chunks == 5


def test_source_totals_upgrade_once_and_follow_all_path_mutations(tmp_path: Path) -> None:
    db = tmp_path / "paths.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE indexed_files (
            source TEXT, file_path TEXT, content_hash TEXT, chunk_count INTEGER,
            indexed_at REAL, PRIMARY KEY (source, file_path))""")
        conn.executemany("INSERT INTO indexed_files VALUES (?, ?, NULL, ?, 0)", [
            ("forge:a_one", "one", 2), ("forge:a_one:child", "one", 3),
            ("forge:aXone", "one", 100), ("FORGE:A_ONE", "one", 100),
        ])
    paths = PathIndex(db)
    assert paths.chunk_count_by_family("forge:a_one") == 5
    paths.mark_indexed("forge:a_one", "one", chunk_count=4)
    paths.mark_indexed_batch([("forge:a_one", "two", "hash", 7)])
    assert PathIndex(db).chunk_count_by_family("forge:a_one") == 14
    paths.remove_batch([("forge:a_one", "two")])
    paths.delete_source("forge:a_one:child")
    assert paths.chunk_count_by_family("forge:a_one") == 4
    paths.remove_by_source_prefix("forge:a_one")
    assert paths.chunk_count_by_family("forge:a_one") == 0


def test_interrupted_cleanup_retries_real_stale_content_and_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths, state, connector, _store, _run = setup_round(monkeypatch, tmp_path)
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path / "lance")
    profile = ModelProfile("fixture", "fixture", dim=2)
    store.register_model(profile)
    old = Chunk("old", "one.md", "text", "markdown", 1, 1, "forge:repo")
    store.upsert("fixture", [old], np.array([[1, 0]], dtype=np.float32))
    paths.mark_indexed(old.source, old.file_path, content_hash="old", chunk_count=1)
    new = replace(old, content="new")
    connector.delta = SourceDelta(
        upserts=[FileEntry("one.md", "new", "markdown", source="forge:repo")], commit="next",
    )
    chunker = SimpleNamespace(chunk=lambda *a, **k: [new])
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda path: (chunker, "text"))

    def embed(batch: list[Chunk], *args: object, **kwargs: object) -> int:
        return store.upsert("fixture", batch, np.array([[0, 1]], dtype=np.float32))

    monkeypatch.setattr(engine, "_embed_and_store_batch", embed)

    def run() -> None:
        engine._index_source(
            "alias", config=SimpleNamespace(
                model_profiles={"fixture": profile}, stream_batch_size=2,
                state_file=tmp_path / "state.json",
            ),
            state=state, path_index=paths, multi_store=store, multi_clients={}, full=False,
        )

    with monkeypatch.context() as patch:
        patch.setattr(store, "delete_stale_by_file", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            run()
    assert paths.get_content_hash(old.source, old.file_path) is None
    assert state.sources["alias"].last_commit == "old"
    assert set(store.content_store.get_chunk_ids_by_file(old.source, old.file_path)) == {
        old.chunk_id, new.chunk_id,
    }
    run()
    assert store.content_store.get_chunk_ids_by_file(old.source, old.file_path) == [new.chunk_id]
    assert store.get_vector_table("fixture").get_vectors([old.chunk_id]) == {}
    assert state.total_chunks == 1


def test_failed_forced_rewrite_revokes_old_compatible_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths, state, connector, store, run = setup_round(monkeypatch, tmp_path)
    item = FileEntry("one.md", "unchanged", "markdown", "forge:repo")
    connector.delta = SourceDelta(upserts=[item], commit="next")
    run()
    assert paths.get_content_hash(item.source, item.path)
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_embed_and_store_batch", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            engine._embed_and_store_files(
                [item], multi_store=store, multi_clients={}, model_profiles={},
                path_index=paths, reconcile_file=lambda *a: None, force=True,
            )
    assert paths.get_content_hash(item.source, item.path) is None
    assert run()[0] == 2
    assert state.total_chunks == 2


def test_recreated_file_after_failed_tombstone_is_not_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths, state, connector, _store, run = setup_round(monkeypatch, tmp_path)
    item = FileEntry("one.md", "unchanged", "markdown", "forge:repo")
    connector.delta = SourceDelta(upserts=[item], commit="original")
    run()
    connector.delta = SourceDelta(
        deletions=[DeletedFile(item.source, item.path)], commit="deleted",
    )
    with monkeypatch.context() as patch:
        patch.setattr(paths, "remove", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            run()
    assert state.sources["alias"].last_commit == "original"
    assert paths.get_entry(item.source, item.path) is not None
    assert paths.get_content_hash(item.source, item.path) is None
    connector.delta = SourceDelta(upserts=[item], commit="recreated")
    assert run()[0] == 2
    assert state.sources["alias"].last_commit == "recreated"
    assert state.total_chunks == 2


def test_chunking_failure_does_not_advance_delta_cursor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _paths, state, connector, _store, run = setup_round(monkeypatch, tmp_path)
    connector.delta = SourceDelta(
        upserts=[FileEntry("one.md", "new", "markdown", "forge:repo")], commit="next",
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            "agent_index.chunking.get_chunker", lambda path: (SimpleNamespace(chunk=fail), "text"),
        )
        with pytest.raises(RuntimeError, match="fixture"):
            run()
    assert state.sources["alias"].last_commit == "old"
    assert run()[0] == 2
    assert state.sources["alias"].last_commit == "next"


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("target", ["lookup", "content", "vector"])
def test_legacy_deletion_failures_propagate_and_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stale: bool, target: str,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path / "lance")
    store.register_model(ModelProfile("fixture", "fixture", dim=2))
    item = Chunk("old", "a'b.md", "text", "markdown", 1, 1, "forge:repo")
    store.upsert("fixture", [item], np.array([[1, 0]], dtype=np.float32))
    content = store.content_store._get_or_create_table()
    vector = store.get_vector_table("fixture")._get_or_create_table()

    def delete() -> object:
        if stale:
            return store.delete_stale_by_file(item.source, item.file_path, before=10**12)
        return store.delete_by_file(item.source, item.file_path)

    with monkeypatch.context() as patch:
        table = vector if target == "vector" else content
        patch.setattr(table, "search" if target == "lookup" else "delete", fail)
        with pytest.raises(RuntimeError, match="fixture"):
            delete()
    assert store.content_store.get_chunk_ids_by_file(item.source, item.file_path) == [item.chunk_id]
    delete()
    assert store.content_store.get_chunk_ids_by_file(item.source, item.file_path) == []
    assert store.get_vector_table("fixture").get_vectors([item.chunk_id]) == {}
