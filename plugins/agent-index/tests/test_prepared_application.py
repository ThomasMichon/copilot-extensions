"""Staging is replayable work, not permission to publish progress or repair a store."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing import engine
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.manifest import CrawlManifest
from agent_index.indexing.pending_crawl import PendingCrawlConflict, PendingCrawls, recovery_scope
from agent_index.indexing.runner import IndexingCancelled
from agent_index.indexing.state import IndexState
from agent_index.indexing.work_counts import measure_reindex
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, SourceLayoutError, initialize_source_store


@pytest.fixture(params=[False, True], ids=["legacy", "source-aware"])
def application(tmp_path, monkeypatch, request):
    selected = request.param
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    profiles = {
        model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
        for model in ("a", "b")
    }
    config = IndexConfig(
        data_dir=tmp_path, model_profiles=profiles, stream_batch_size=1, cluster_enabled=False,
    )
    store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
    if not selected:
        for profile in profiles.values():
            store.register_model(profile)
    paths = PathIndex(tmp_path / "paths.db")
    state = IndexState()
    files = [
        FileEntry(f"{name}.md", f"Useful {name} text.", "markdown", "sample:repo")
        for name in ("first", "second")
    ]
    fixture = SimpleNamespace(
        config=config, store=store, paths=paths, state=state, files=files,
        delta=SourceDelta(upserts=files, commit="pinned"), calls=[], acquired=[],
        hook=None, selected=selected,
        progress=None, resume_since=None,
    )

    class Connector:
        source_name = "sample:repo"
        recovery_identity = ("fixture", "account")

        def discover_delta(self, last_commit, **kwargs):
            fixture.acquired.append(last_commit)
            return fixture.delta

    fixture.connector = Connector()
    fixture.scope = recovery_scope("sample:repo", fixture.connector)

    class Client:
        def __init__(self, model):
            self.model = model

        def embed_texts(self, texts):
            assert store._source_domain is None or not store._source_domain.writing
            fixture.calls.append((self.model, texts))
            if fixture.hook:
                fixture.hook(self.model, texts)
            return np.array([[len(text), 1] for text in texts], dtype=np.float32)

    fixture.clients = {model: Client(model) for model in profiles}

    @measure_reindex
    def run():
        result = engine._index_source(
            "sample:repo", config=fixture.config, state=fixture.state, path_index=fixture.paths,
            multi_store=fixture.store, multi_clients=fixture.clients, full=False,
            connector=fixture.connector,
            progress_cb=fixture.progress, resume_since=fixture.resume_since,
        )
        return {"result": result}

    fixture.run = run
    return fixture


def fail_second(model, texts):
    if model == "a" and "second" in texts[0]:
        raise RuntimeError("fixture interrupted preparation")


def assert_unpublished(fixture):
    assert fixture.paths.get_all_entries() == {}
    assert sum(fixture.store.source_counts().values()) == 0
    assert not fixture.config.state_file.exists()
    assert fixture.state.sources == {}
    if fixture.selected:
        assert fixture.store._source_domain.receipt()["state"] == "clean"


def test_restart_reuses_prepared_pairs_before_acquiring_a_new_delta(application):
    f = application
    f.hook = fail_second
    with pytest.raises(RuntimeError, match="interrupted preparation"):
        f.run()
    assert_unpublished(f)
    assert len(f.calls) == 3  # Both models for file one, then the failing pair.
    assert PendingCrawls(f.paths).load("sample:repo", f.scope)[0].commit == "pinned"
    f.paths = PathIndex(f.paths._db_path)
    f.state = IndexState.load(f.config.state_file)
    f.hook = None
    f.delta = SourceDelta(commit="newer")
    f.calls.clear()
    f.acquired.clear()
    result = f.run()
    assert f.acquired == ["pinned"]
    assert [model for model, _texts in f.calls] == ["a", "b"]
    assert all("second" in texts[0] for _model, texts in f.calls)
    assert result["work_counts"]["content_rows_upserted"] == 2
    assert result["work_counts"]["checkpoint_rows_written"] == 2
    assert IndexState.load(f.config.state_file).sources["sample:repo"].last_commit == "newer"
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is None
    with closing(f.paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prepared_crawl_rows").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["save", "clear"])
def test_cursor_and_journal_failures_replay_committed_files_without_engine_calls(
    application, monkeypatch, failure,
):
    f = application
    if failure == "save":
        target, name = f.state, "save"
    else:
        target, name = PendingCrawls, "clear"

    def fail(*args, **kwargs):
        raise OSError("fixture publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(target, name, fail)
        with pytest.raises(OSError, match="publication failure"):
            f.run()
    assert sum(f.store.source_counts().values()) == 2
    assert len(f.paths.get_all_entries()) == 2
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is not None
    if f.selected:
        assert f.store._source_domain.receipt()["state"] == "clean"

    def no_engine(*args):
        pytest.fail("committed file checkpoint was not reused")

    f.hook = no_engine
    f.calls.clear()
    f.delta = SourceDelta(commit="newer")
    result = f.run()
    assert f.calls == []
    assert result["work_counts"]["content_rows_upserted"] == 0
    assert result["work_counts"]["files_reused"] == 2
    assert IndexState.load(f.config.state_file).sources["sample:repo"].last_commit == "newer"


def test_checkpoint_change_during_preparation_refuses_before_store_entry(application):
    f = application

    def concurrent_checkpoint(model, texts):
        if model == "b" and "second" in texts[0]:
            f.paths.mark_indexed("sample:repo", "first.md", content_hash="peer-checkpoint")

    f.hook = concurrent_checkpoint
    with pytest.raises(PendingCrawlConflict, match="checkpoints changed"):
        f.run()
    assert sum(f.store.source_counts().values()) == 0
    assert f.paths.get_entry("sample:repo", "first.md")[0] == "peer-checkpoint"
    assert not f.config.state_file.exists()
    if f.selected:
        assert f.store._source_domain.receipt()["state"] == "clean"


def test_partial_vector_write_retains_journal_and_never_advertises_clean_native_store(
    application, monkeypatch,
):
    f = application
    write = f.store.upsert_vectors
    calls = 0

    def partial(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = write(*args, **kwargs)
        if calls == 3:
            raise RuntimeError("fixture partial store effect")
        return result

    monkeypatch.setattr(f.store, "upsert_vectors", partial)
    with pytest.raises(RuntimeError, match="partial store"):
        f.run()
    assert len(f.calls) == 4  # Every engine call finished before the first store effect.
    assert f.paths.get_entry("sample:repo", "first.md")[0] is not None
    assert f.paths.get_entry("sample:repo", "second.md") is None
    assert not f.config.state_file.exists()
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is not None
    if f.selected:
        assert f.store._source_domain.receipt()["state"] == "dirty"
        with pytest.raises(SourceLayoutError):
            f.store.projection_snapshot()
    else:
        monkeypatch.setattr(f.store, "upsert_vectors", write)
        f.calls.clear()
        f.delta = SourceDelta(commit="newer")
        f.run()
        assert f.calls == []  # Cached exact inputs also survive partial legacy writes.
        assert sum(f.store.source_counts().values()) == 2


def test_model_revision_change_cannot_reuse_staged_vectors(application):
    f = application
    f.hook = fail_second
    with pytest.raises(RuntimeError, match="interrupted preparation"):
        f.run()
    f.config = replace(f.config, model_profiles={
        model: replace(profile, revision="changed")
        for model, profile in f.config.model_profiles.items()
    })
    f.hook = None
    f.calls.clear()
    f.delta = SourceDelta(commit="newer")
    result = f.run()
    assert len(f.calls) == 4
    assert result["work_counts"]["models"]["a"]["chunks_embedded"] == 2


def test_corrupt_cached_vector_refuses_without_live_store_writes(application):
    f = application
    f.hook = fail_second
    with pytest.raises(RuntimeError, match="interrupted preparation"):
        f.run()
    with closing(f.paths._connect()) as conn, conn:
        conn.execute(
            "UPDATE prepared_crawl_rows SET payload = ? WHERE kind = 'vector'", (b"corrupt",),
        )
    f.hook = None
    f.calls.clear()
    with pytest.raises(ValueError, match="Corrupt prepared crawl data"):
        f.run()
    assert f.calls == []
    assert_unpublished(f)


def test_preparation_failure_preserves_old_content_and_reusable_file_checkpoints(application):
    f = application
    f.run()
    checkpoints = f.paths.get_all_entries()
    prior = f.store.projection_snapshot()
    f.delta = SourceDelta(
        upserts=[replace(entry, content=f"Changed {entry.content}") for entry in f.files],
        commit="changed",
    )
    f.hook = fail_second
    with pytest.raises(RuntimeError, match="interrupted preparation"):
        f.run()
    assert f.paths.get_all_entries() == checkpoints
    assert f.store.projection_snapshot() == prior
    assert IndexState.load(f.config.state_file).sources["sample:repo"].last_commit == "pinned"


def test_parent_scope_change_during_embedding_refuses_before_store_effects(application):
    f = application

    def replace_parent(model, texts):
        journal = PendingCrawls(f.paths)
        expected = journal.read_payload("sample:repo", f.scope)
        journal.compare_and_save(
            "sample:repo", scope=f.scope, payload=expected + " ", expected=expected,
            family="sample:repo",
        )

    f.hook = replace_parent
    with pytest.raises(PendingCrawlConflict, match="no longer matches"):
        f.run()
    assert_unpublished(f)


@pytest.mark.parametrize("when", ["first", "last"])
def test_cancellation_during_prepared_models_never_invalidates_a_file(application, when):
    f = application
    cancelled = False

    def cancel_after_first_model(model, texts):
        nonlocal cancelled
        if when == "first" or (model == "b" and "second" in texts[0]):
            cancelled = True

    def check():
        if cancelled:
            raise IndexingCancelled("fixture cancellation")

    f.progress = SimpleNamespace(phase=lambda *a, **k: None, check_cancelled=check)
    f.hook = cancel_after_first_model
    with pytest.raises(IndexingCancelled, match="cancellation"):
        f.run()
    assert len(f.calls) == (1 if when == "first" else 4)
    assert_unpublished(f)


@pytest.mark.parametrize("resume", [False, True])
def test_pending_full_application_preserves_force_and_retry_window_semantics(application, resume):
    f = application
    f.run()
    f.calls.clear()
    journal = PendingCrawls(f.paths)
    manifest = CrawlManifest(
        source="sample:repo", accounting_family="sample:repo",
        upserts=f.files, commit="full",
    )
    journal.save(manifest, scope=f.scope, full=True)
    f.delta = SourceDelta(commit="newer")
    f.resume_since = 0.0 if resume else None
    result = f.run()
    assert len(f.calls) == (0 if resume else 4)
    assert result["work_counts"]["checkpoint_rows_written"] == (0 if resume else 2)
    assert f.state.sources["sample:repo"].last_commit == "newer"


def test_empty_file_still_completes_cleanup_and_checkpoint_without_an_engine(application):
    f = application
    f.delta = SourceDelta(
        upserts=[FileEntry("empty.md", "", "markdown", "sample:repo")], commit="empty",
    )
    result = f.run()
    assert f.calls == []
    assert result["work_counts"]["chunks_generated"] == 0
    assert result["work_counts"]["checkpoint_rows_written"] == 1
    assert f.paths.get_entry("sample:repo", "empty.md")[0] is not None
    assert f.state.sources["sample:repo"].chunk_count == 0


def test_changed_file_stale_cleanup_and_tombstone_share_one_publication(application):
    f = application
    f.run()
    before = (
        f.store._source_domain.receipt()["logical_projection"]["generation"] if f.selected else None
    )
    f.delta = SourceDelta(
        upserts=[replace(f.files[0], content="Changed first file.")],
        deletions=[DeletedFile("sample:repo", "second.md")], commit="changed-and-deleted",
    )
    result = f.run()
    counts = result["work_counts"]
    assert counts["stale_chunk_ids_deleted"] == counts["file_chunk_ids_deleted"] == 1
    assert f.paths.get_entry("sample:repo", "second.md") is None
    assert f.state.sources["sample:repo"].chunk_count == 1
    assert sum(f.store.source_counts().values()) == 1
    if f.selected:
        summary = counts["source_mutations"]["sample:repo"][0]
        assert summary["write_commits"] == summary["logical_commits"] == 1
        assert summary["generation"] == before + 1
        assert summary["tables"] == ["chunks", "vectors_a", "vectors_b"]


def test_failed_clean_store_receipt_cannot_publish_source_state(application, monkeypatch):
    f = application
    if not f.selected:
        pytest.skip("legacy layout has no participating clean receipt")
    from agent_index.store.ingestion_index import prepare_ingestion_indices

    prepare_ingestion_indices(f.store, max_rows=0)
    domain = f.store._source_domain
    save = domain.save

    def fail_clean(value):
        if value["state"] == "clean":
            raise OSError("fixture clean receipt failure")
        save(value)

    monkeypatch.setattr(domain, "save", fail_clean)
    with pytest.raises(OSError, match="clean receipt failure"):
        f.run()
    assert len(f.calls) == 4
    assert domain.receipt()["state"] == "dirty"
    assert not f.config.state_file.exists()
    assert f.state.sources == {}
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is not None
    with pytest.raises(SourceLayoutError):
        f.store.projection_snapshot()


def test_ordinary_selected_publication_covers_all_point_and_file_indices(application):
    f = application
    if not f.selected:
        pytest.skip("legacy layout does not opt into source-owned scalar upkeep")
    from agent_index.store.scalar_index import inspect_index

    f.run()
    before = f.store._source_domain.receipt()["logical_projection"]["generation"]
    f.delta = SourceDelta(upserts=[replace(f.files[0], content="Changed first file.")], commit="next")
    f.run()
    assert f.store._source_domain.receipt()["logical_projection"]["generation"] == before + 1
    for owner, columns in [
        (f.store.content_store, ("chunk_id", "file_path")),
        *((f.store.get_vector_table(model), ("chunk_id",)) for model in f.store.model_ids),
    ]:
        for column in columns:
            state, _ = inspect_index(owner._get_or_create_table(), column=column)
            assert state.prepared and state.physical_unindexed_rows == 0


def test_ordinary_nonempty_unprepared_store_requires_explicit_preparation(application):
    f = application
    if not f.selected:
        pytest.skip("legacy layout does not opt into source-owned scalar upkeep")
    from agent_index.chunking.base import Chunk
    from agent_index.store.scalar_index import ScalarIndexPreparationRequired

    f.store.upsert_content([
        Chunk("Existing data.", "existing.md", "text", "markdown", 1, 1, "other:repo"),
    ])
    before = f.store._source_domain.receipt()
    with pytest.raises(ScalarIndexPreparationRequired, match="budget"):
        f.run()
    assert not f.calls
    assert f.store._source_domain.receipt() == before
    assert f.state.sources == {}
    assert f.paths.get_all_entries() == {}
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is not None


def test_ordinary_segment_limit_refuses_before_mutation(application, monkeypatch):
    f = application
    if not f.selected:
        pytest.skip("legacy layout does not opt into source-owned scalar upkeep")
    from agent_index.store import ingestion_index
    from agent_index.store.scalar_index import ScalarIndexPreparationRequired, inspect_index

    f.run()
    domain = f.store._source_domain
    before = domain.receipt()
    prior_paths = f.paths.get_all_entries()
    state, _ = inspect_index(f.store.content_store._get_or_create_table(), column="chunk_id")
    monkeypatch.setattr(ingestion_index, "MAX_SEGMENTS", state.segments)
    f.delta = SourceDelta(upserts=[replace(f.files[0], content="Changed first file.")], commit="next")
    with pytest.raises(ScalarIndexPreparationRequired, match="consolidation"):
        f.run()
    assert domain.receipt() == before
    assert f.paths.get_all_entries() == prior_paths
    assert f.state.sources["sample:repo"].last_commit == "pinned"
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is not None


def test_failed_tail_upkeep_cannot_publish_source_progress(application, monkeypatch):
    f = application
    if not f.selected:
        pytest.skip("legacy layout does not opt into source-owned scalar upkeep")
    from agent_index.store import ingestion_index

    prepare = ingestion_index.prepare_ingestion_indices

    def fail_tail(store, *, max_rows, reserve_segment=False):
        if max_rows:
            raise RuntimeError("fixture index upkeep failed")
        prepare(store, max_rows=max_rows, reserve_segment=reserve_segment)

    monkeypatch.setattr(ingestion_index, "prepare_ingestion_indices", fail_tail)
    with pytest.raises(RuntimeError, match="upkeep failed"):
        f.run()
    assert len(f.calls) == 4
    assert f.store._source_domain.receipt()["state"] == "dirty"
    assert f.store._source_domain.receipt()["uncertain_effect"]
    assert not f.config.state_file.exists()
    assert f.state.sources == {}
    assert PendingCrawls(f.paths).load("sample:repo", f.scope) is not None
    with pytest.raises(SourceLayoutError):
        initialize_source_store(f.config)


@pytest.mark.parametrize("shape", ["short", "dimension", "nan"])
def test_invalid_embedding_response_fails_before_any_content_or_checkpoint(
    application, shape,
):
    f = application
    response = {
        "short": np.empty((0, 2), dtype=np.float32),
        "dimension": np.zeros((1, 3), dtype=np.float32),
        "nan": np.array([[float("nan"), 1]], dtype=np.float32),
    }[shape]
    f.clients["a"] = SimpleNamespace(embed_texts=lambda _texts: response)
    with pytest.raises(ValueError, match="incompatible shape or nonfinite"):
        f.run()
    assert_unpublished(f)
