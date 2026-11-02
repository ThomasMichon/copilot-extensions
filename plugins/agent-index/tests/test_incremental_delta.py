"""Delta acquisition stays bounded and refuses unsafe provider results."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.fingerprints import file_fingerprint
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta


def entry(*, source: str = "forge:repo") -> FileEntry:
    return FileEntry(path="one.md", content="one", source=source, language="markdown")


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("ordinary delta attempted a whole-corpus operation")


@pytest.mark.parametrize("corpus_size", [100, 10_000, 100_000])
@pytest.mark.parametrize("changed", [False, True])
def test_delta_acquisition_does_not_enumerate_existing_corpus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, corpus_size: int, changed: bool,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([
        ("forge:repo", f"old-{i}.md", "old", 1) for i in range(corpus_size)
    ])
    monkeypatch.setattr(paths, "get_paths_by_prefix", forbidden)
    monkeypatch.setattr(paths, "get_paths_by_family", forbidden)
    calls: list[str | None] = []
    result = SourceDelta(
        upserts=[entry()] if changed else [],
        deletions=[DeletedFile("forge:repo", "gone.md")] if changed else [],
        commit="next",
    )

    def discover(last_commit: str | None, cancel_check=None) -> SourceDelta:
        calls.append(last_commit)
        return result

    connector = SimpleNamespace(
        source_name="forge:repo", discover_delta=discover,
        recovery_identity=("fixture-target",),
        current_commit=forbidden, list_paths=forbidden, discover_changed=forbidden,
    )
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: connector)
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    state = IndexState(sources={"configured": SourceState(last_commit="previous")})
    state.sources["configured"].acquisition_scope = recovery_scope("configured", connector)
    manifest = engine._crawl_source(
        "configured", config=None, state=state, path_index=paths, full=False,
    )
    assert calls == ["previous"]
    assert manifest.commit == "next"
    assert manifest.stats.files_scanned == (2 if changed else 0)
    assert len(manifest.upserts) == int(changed)
    assert len(manifest.deletions) == int(changed)
    assert state.sources["configured"].last_commit == "previous"
    steps = 0

    def count_step() -> int:
        nonlocal steps
        steps += 1
        return 0

    connect = paths._connect

    def measured_connection():
        conn = connect()
        conn.set_progress_handler(count_step, 1)
        return conn

    monkeypatch.setattr(paths, "_connect", measured_connection)
    assert paths.chunk_count_by_family("forge:repo") == corpus_size
    assert steps < 100  # Count work is independent of unchanged file count.


@pytest.mark.parametrize("result", [
    SourceDelta(complete=False),
    SourceDelta(version=2),
    SourceDelta(upserts=[entry(source="other")]),
    SourceDelta(deletions=[DeletedFile("other", "gone.md")]),
    SourceDelta(upserts=[entry(), entry()]),
    SourceDelta(upserts=[entry()], deletions=[DeletedFile("forge:repo", "one.md")]),
    {"upserts": []},
])
def test_unsafe_delta_fails_before_indexing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: object,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed("forge:repo", "retained.md", content_hash="retained", chunk_count=3)
    before = paths.get_entry("forge:repo", "retained.md")
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    connector = SimpleNamespace(
        source_name="forge:repo", discover_delta=lambda *a, **k: result,
        recovery_identity=("fixture-target",),
    )
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: connector)
    state = IndexState(sources={"configured": SourceState(last_commit="previous")})
    state.sources["configured"].acquisition_scope = recovery_scope("configured", connector)
    with pytest.raises(ValueError):
        engine._crawl_source(
            "configured", config=None, state=state, path_index=paths, full=False,
        )
    assert paths.get_entry("forge:repo", "retained.md") == before
    assert state.sources["configured"].last_commit == "previous"


def test_selected_checkpoint_lookup_is_exact_and_bounded(tmp_path: Path) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([
        (source, f"path-{i}.md", str(i), 1)
        for source in ("forge:a", "forge:a:child", "forge:b")
        for i in range(250)
    ])
    identities = [("forge:a", f"path-{i}.md") for i in range(205)]
    result = paths.get_entries(identities + [identities[0], ("absent", "none")])
    assert set(result) == set(identities)
    assert paths.get_entries([]) == {}


def test_compatible_upsert_skips_chunker_engine_and_stale_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    paths = PathIndex(tmp_path / "paths.db")
    item = entry()
    paths.mark_indexed(
        item.source, item.path, content_hash=file_fingerprint(item, {}), chunk_count=3,
    )
    monkeypatch.setattr(paths, "get_all_entries", forbidden)
    monkeypatch.setattr("agent_index.chunking.get_chunker", forbidden)
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    connector = SimpleNamespace(
        source_name="forge:repo",
        recovery_identity=("fixture-target",),
        discover_delta=lambda *a, **k: SourceDelta(upserts=[item], commit="next"),
    )
    monkeypatch.setattr("agent_index.sources.get_connector", lambda *a, **k: connector)
    state = IndexState(
        sources={"configured": SourceState(last_commit="previous", chunk_count=3)},
    )
    state.sources["configured"].acquisition_scope = recovery_scope("configured", connector)
    result = engine._index_source(
        "configured",
        config=SimpleNamespace(
            model_profiles={}, stream_batch_size=2, state_file=tmp_path / "state.json",
        ),
        state=state, path_index=paths,
        multi_store=SimpleNamespace(reconcile_file_chunks=forbidden),
        multi_clients={}, full=False, before_store=forbidden,
    )
    assert result == (0, 0, 1)
    assert state.sources["configured"].last_commit == "next"
    assert state.sources["configured"].chunk_count == 3


def test_reuse_fingerprint_includes_metadata_models_and_chunker_revision() -> None:
    item = entry()
    profile = ModelProfile(model_id="prose", model_name="model", content_types=frozenset({"text"}))
    original = file_fingerprint(item, {"prose": profile})
    assert file_fingerprint(item, {"prose": profile}) == original
    assert file_fingerprint(replace(item, metadata={"state": "closed"}), {"prose": profile}) != original
    assert file_fingerprint(item, {"prose": replace(profile, model_name="new")}) != original
    assert file_fingerprint(item, {"prose": replace(profile, revision="new")}) != original
    assert file_fingerprint(item, {"prose": replace(profile, max_seq_length=512)}) != original
    assert file_fingerprint(item, {"prose": profile}, chunker_revision="2") != original
    assert file_fingerprint(replace(item, source="forge:other"), {"prose": profile}) != original


@pytest.mark.parametrize("has_embedding_work", [False, True])
def test_run_prepares_engines_only_when_embedding_is_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, has_embedding_work: bool,
) -> None:
    profile = ModelProfile(model_id="prose", model_name="fixture")
    config = IndexConfig(
        data_dir=tmp_path, model_profiles={"prose": profile}, cluster_enabled=False,
    )
    calls: list[str] = []
    client = SimpleNamespace(close=lambda: calls.append("close"))
    store = SimpleNamespace(register_model=lambda _profile: None)
    monkeypatch.setenv("AGENT_INDEX_INDEX_ENSURE_ENGINES", "1")
    monkeypatch.setattr("agent_index.index_config.IndexConfig", lambda: config)
    monkeypatch.setattr("agent_index.engine.client.EngineClient", lambda **k: client)
    monkeypatch.setattr("agent_index.store.multi_model_store.MultiModelStore", lambda *a, **k: store)
    monkeypatch.setattr(
        "agent_index.engine.lifecycle.ensure_engine",
        lambda *a: calls.append("ensure") or False,
    )
    monkeypatch.setattr(engine, "selected_layout", lambda: None)
    monkeypatch.setattr(
        engine, "configured_source_specs", lambda: [engine.SourceSpec("git:fixture")],
    )
    monkeypatch.setattr(engine, "_connector_kwargs", lambda _spec: {})

    def index_source(*args: object, **kwargs: object) -> tuple[int, int, int]:
        if has_embedding_work:
            before_store = kwargs["before_store"]
            assert callable(before_store)
            before_store()
            before_store()
        return (0, 0, 0)

    monkeypatch.setattr(engine, "_index_source", index_source)
    result = engine.run_reindex(source="git:fixture")
    assert "sources_failed" not in result
    assert calls == (["ensure", "close"] if has_embedding_work else ["close"])
