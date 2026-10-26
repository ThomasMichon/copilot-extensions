"""A retry finishes acquired work before accepting a newer upstream cursor."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index.indexing import engine
from agent_index.indexing.manifest import CrawlManifest, CrawlStats
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recover_source
from agent_index.indexing.state import IndexState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SourceDelta


def item(path: str, content: str) -> FileEntry:
    return FileEntry(path, content, "markdown", "forge:repo")


def test_restart_replays_pinned_delta_before_newer_net_delta_can_omit_reverted_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    state_file = tmp_path / "state.json"
    config = SimpleNamespace(model_profiles={}, stream_batch_size=2, state_file=state_file)
    state = IndexState()
    upstream = "old"
    calls: list[str | None] = []
    contents: dict[str, str] = {}
    fail_second = False

    def discover(last: str | None, **kwargs: object) -> SourceDelta:
        calls.append(last)
        if upstream == "old":
            return SourceDelta(upserts=[item("first.md", "original")], commit="old")
        if upstream == "middle":
            return SourceDelta(
                upserts=[item("first.md", "temporary"), item("second.md", "second")],
                commit="middle",
            )
        # first.md is unchanged between old and latest, so a fresh net delta
        # from old would omit it despite the partial middle writes.
        entries = [item("second.md", "latest")]
        if last == "middle":
            entries.append(item("first.md", "original"))
        return SourceDelta(upserts=entries, commit="latest")

    connector = SimpleNamespace(source_name="forge:repo", discover_delta=discover)
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: connector)
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    chunker = SimpleNamespace(
        chunk=lambda content, path, **k: [SimpleNamespace(path=path, content=content)] * 2,
    )
    monkeypatch.setattr("agent_index.chunking.get_chunker", lambda path: (chunker, "text"))

    def embed(batch: list[SimpleNamespace], *args: object, **kwargs: object) -> int:
        if fail_second and batch[0].path == "second.md":
            raise RuntimeError("fixture interruption")
        for chunk in batch:
            contents[chunk.path] = chunk.content
        return len(batch)

    monkeypatch.setattr(engine, "_embed_and_store_batch", embed)
    store = SimpleNamespace(delete_stale_by_file=lambda *a, **k: 0)

    def run() -> None:
        engine._index_source(
            "alias", config=config, state=state, path_index=paths,
            multi_store=store, multi_clients={}, full=False,
        )

    run()
    upstream, fail_second = "middle", True
    with pytest.raises(RuntimeError, match="interruption"):
        run()
    assert contents["first.md"] == "temporary"
    assert IndexState.load(state_file).sources["alias"].last_commit == "old"
    # A fresh process sees the durable acquired target, not mutable provider input.
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState.load(state_file)
    calls.clear()
    upstream, fail_second = "latest", False
    run()
    assert calls == ["middle"]
    assert contents == {"first.md": "original", "second.md": "latest"}
    assert state.sources["alias"].last_commit == "latest"
    assert state.total_chunks == 4
    assert PendingCrawls(paths).load("alias", "alias") is None


def test_journal_survives_state_save_failure_before_fresh_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = CrawlManifest(
        source="alias", accounting_family="forge:repo", upserts=[item("one.md", "old")],
        commit="middle", stats=CrawlStats(files_changed=1),
    )
    journal.save(manifest, scope="scope", full=False)
    order: list[str] = []
    state = IndexState()
    state_file = tmp_path / "state.json"

    def apply(*, manifest: CrawlManifest, full: bool) -> tuple[int, int, int]:
        state.mark_source_indexed("alias", manifest.commit, 2)
        order.append("apply")
        return 2, 0, 1

    def fail_save(path: Path) -> None:
        raise OSError("fixture cursor write failure")

    with monkeypatch.context() as patch:
        patch.setattr(state, "save", fail_save)
        with pytest.raises(OSError, match="cursor write"):
            recover_source(
                "alias", scope="scope", state=state, state_file=state_file,
                paths=paths, full=False, acquire=lambda: pytest.fail("acquired before cursor save"),
                apply=apply,
            )
    assert journal.load("alias", "scope") == (manifest, False)

    def acquire() -> CrawlManifest:
        assert IndexState.load(state_file).sources["alias"].last_commit == "middle"
        assert journal.load("alias", "scope") is None
        order.append("acquire")
        return CrawlManifest(source="alias", commit="latest")

    recover_source(
        "alias", scope="scope", state=state, state_file=state_file,
        paths=paths, full=False, acquire=acquire, apply=apply,
    )
    assert order == ["apply", "apply", "acquire", "apply"]
    assert IndexState.load(state_file).sources["alias"].last_commit == "latest"


def test_journal_scope_or_corruption_refuses_before_effects(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    manifest = CrawlManifest(source="alias", accounting_family="forge:repo", upserts=[item("a", "a")])
    journal.save(manifest, scope="old-account", full=True)
    with pytest.raises(ValueError, match="configuration"):
        journal.load("alias", "new-account")
    with sqlite3.connect(tmp_path / "paths.db") as conn:
        conn.execute("UPDATE pending_crawls SET payload = '{}'")
    with pytest.raises(ValueError, match="envelope"):
        journal.load("alias", "old-account")


def test_failed_atomic_state_publication_preserves_prior_cursor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_index.indexing import state as state_module

    path = tmp_path / "state.json"
    state = IndexState()
    state.mark_source_indexed("alias", "old", 2)
    state.save(path)
    before = path.read_bytes()
    state.mark_source_indexed("alias", "new", 3)

    def fail_replace(*args: object) -> None:
        raise OSError("fixture atomic replacement failure")

    monkeypatch.setattr(state_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement"):
        state.save(path)
    assert path.read_bytes() == before
    assert IndexState.load(path).sources["alias"].last_commit == "old"
    assert not list(tmp_path.glob(".state.json.*.tmp"))
