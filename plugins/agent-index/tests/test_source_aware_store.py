"""Cold, isolated source-aware domain proofs. No legacy data or live services."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from agent_procutil import no_window_kwargs

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.search.engine import SearchEngine
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import (
    LAYOUT,
    MARKER,
    SourceLayoutError,
    initialize_source_store,
    validate_source_store,
)


@pytest.fixture
def cold(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    config = IndexConfig(
        data_dir=tmp_path / "isolated", model_profiles={
            "small": ModelProfile("small", "fixture-inference-only", dim=2),
        },
    )
    return config, initialize_source_store(config)


def chunk(source="git:alpha", text="needle alpha", language="markdown", path="a.md"):
    return Chunk(text, path, "text", language, 1, 1, source)


def test_cold_projection_restart_and_unchanged_initialization(cold):
    config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    receipt = (config.lance_dir / MARKER).read_bytes()
    again = initialize_source_store(config)
    assert (config.lance_dir / MARKER).read_bytes() == receipt
    row = again.get_vector_table("small")._get_or_create_table().search().limit(1).to_list()[0]
    assert row["source"] == item.source and row["language"] == item.language
    assert row["chunk_id"] == item.chunk_id and row["vector"] == [1.0, 0.0]


def test_legacy_refused_without_mutation_and_unsafe_downgrade(cold, tmp_path, monkeypatch):
    config, _store = cold
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT")
    with pytest.raises(SourceLayoutError):
        MultiModelStore(config.lance_dir)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    sentinel = legacy / "old-data"
    sentinel.write_bytes(b"preserve")
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    from agent_index.store.source_layout import initialize_source_layout

    with pytest.raises(SourceLayoutError):
        initialize_source_layout(legacy)
    assert list(legacy.iterdir()) == [sentinel] and sentinel.read_bytes() == b"preserve"


def test_kernel_fence_excludes_other_participating_process(cold):
    config, store = cold
    code = (
        "import sys; from agent_index.store.multi_model_store import MultiModelStore; "
        "from agent_index.store.source_layout import SourceLayoutError; "
        "s=MultiModelStore(sys.argv[1]); "
        "\ntry:\n with s._source_domain.read(s.content_store.db): pass"
        "\nexcept SourceLayoutError:\n raise SystemExit(23)"
    )
    with store._source_domain.exclusion():
        result = subprocess.run(  # noqa: S603 -- fixed fixture code, owned store path
            [sys.executable, "-I", "-c", code, str(config.lance_dir)], capture_output=True,
            text=True, timeout=30, check=False, **no_window_kwargs(),
        )
        assert result.returncode == 23, result.stderr


def test_interruption_latches_closed_then_explicit_validation(cold):
    _config, store = cold
    with pytest.raises(RuntimeError):
        with store._source_domain.write(store.content_store.db):
            raise RuntimeError("fixture interruption before effect")
    with pytest.raises(SourceLayoutError):
        with store._source_domain.read(store.content_store.db):
            pass
    assert validate_source_store(store)["state"] == "clean"
    with store._source_domain.read(store.content_store.db):
        pass


def test_missing_provenance_and_source_changes_fail_closed(cold):
    _config, store = cold
    with pytest.raises(SourceLayoutError):
        store.upsert_vectors("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    assert validate_source_store(store)["state"] == "clean"
    original = chunk()
    store.upsert("small", [original], np.array([[1, 0]], dtype=np.float32))
    changed = chunk(source="git:beta")
    object.__setattr__(changed, "chunk_id", original.chunk_id)
    with pytest.raises(SourceLayoutError):
        store.upsert_content([changed])
    validate_source_store(store)
    assert store.get_document("git:alpha", "a.md").source == "git:alpha"


def test_native_prefilter_selectors_deletes_and_no_read_mutation(cold):
    _config, store = cold
    items = [
        chunk(), chunk("git:alpha:commits", "needle commit", path="b.md"),
        chunk("git:alphabet", "needle forbidden prefix", path="c.md"),
        chunk("git:beta", "needle forbidden beta", path="d.md"),
    ]
    store.upsert("small", items, np.array([[0, 1], [0.2, 0.8], [1, 0], [1, 0]],
                                        dtype=np.float32))

    class Inference:
        def embed_query(self, query):
            return np.array([1, 0], dtype=np.float32)

    engine = SearchEngine({"small": Inference()}, store)
    before = store._source_domain.receipt()
    result = engine.search_authorized("needle", families=("git:alpha",), mode="vector")
    assert {r.source for r in result} == {"git:alpha", "git:alpha:commits"}
    assert engine.search_authorized("needle", families=(), mode="vector") == []
    assert engine.search_authorized(
        "needle", families=("git:alpha",), language="python", mode="vector",
    ) == []
    assert engine.search_authorized(
        "needle", families=("git:alpha",), repo="beta", mode="vector",
    ) == []
    assert store._source_domain.receipt() == before
    store.delete_by_file("git:alpha", "a.md")
    result = engine.search_authorized("needle", families=("git:alpha",), mode="vector")
    assert [r.source for r in result] == ["git:alpha:commits"]


def test_canonical_language_update_preserves_vector_and_updates_selector(cold):
    _config, store = cold
    before = chunk()
    store.upsert("small", [before], np.array([[1, 0]], dtype=np.float32))
    after = chunk(language="python")
    assert after.chunk_id == before.chunk_id
    store.upsert_content([after])
    row = store.get_vector_table("small")._get_or_create_table().search().limit(1).to_list()[0]
    assert row["source"] == before.source and row["language"] == "python"
    assert row["vector"] == [1.0, 0.0] and row["chunk_id"] == before.chunk_id
    from agent_index.store.source_search import source_predicate

    with pytest.raises(ValueError):
        source_predicate(("git:alpha",), repo="x" * 257)


def run_git(repo: Path, *args):
    git = shutil.which("git")
    assert git is not None
    result = subprocess.run(  # noqa: S603 -- fixture-only Git verbs and owned repository
        [git, "-C", str(repo), *args], capture_output=True, text=True,
        timeout=10, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.timeout(120)
@pytest.mark.parametrize("all_sources", [False, True], ids=["explicit", "all"])
def test_real_queued_git_ingestion_incremental_delete_and_restart(
    tmp_path, monkeypatch, all_sources,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "--quiet")
    run_git(repo, "config", "user.name", "Synthetic Fixture")
    run_git(repo, "config", "user.email", "fixture@example.invalid")
    file = repo / "guide.md"
    file.write_text("# Needle\n\nfirst independently ingested document\n", encoding="utf-8")
    run_git(repo, "add", "--", "guide.md")
    run_git(repo, "commit", "--quiet", "-m", "fixture initial")
    data = tmp_path / "data"
    inline = {"role": "host", "corpus": {"sources": [
        {"name": "git:fixture", "type": "git", "_repo_path": str(repo)},
    ]}}
    for key, value in {
        "AGENT_INDEX_STORE_LAYOUT": LAYOUT, "AGENT_INDEX_DATA_DIR": str(data),
        "AGENT_INDEX_HOME": str(tmp_path / "home"), "AGENT_INDEX_SOURCE_MODE": "explicit",
        "AGENT_INDEX_INDEX_ENSURE_ENGINES": "0", "AGENT_INDEX_INDEXER_NICE": "0",
        "AGENT_INDEX_CLUSTER_ENABLED": "0", "AGENT_INDEX_GIT_FETCH": "0",
        "AGENT_INDEX_CONFIG_DATA_B64": (
            base64.urlsafe_b64encode(json.dumps(inline).encode()).decode()
        ),
    }.items():
        monkeypatch.setenv(key, value)
    from agent_index.engine.client import EngineClient
    from agent_index.indexing.task_store import TaskStore
    from agent_index.indexing.worker import run_worker

    def inference(self, texts):
        vectors = np.zeros((len(texts), 768), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors

    class QueryInference:
        def embed_query(self, query):
            return inference(None, [query])[0]

    monkeypatch.setattr(EngineClient, "embed_texts", inference)  # Inference only.
    queue = TaskStore(data / "tasks.db")

    def queued(full):
        task = queue.enqueue(
            "all" if all_sources else "git:fixture", full=full, trigger_source="fixture",
        )
        claimed = queue.dequeue_next()
        assert claimed.id == task.id
        assert run_worker(task.id) == 0
        assert queue.get_task(task.id).status == "complete"

    # Empty standalone first-touch FTS preparation must not poison the queued writer.
    empty = initialize_source_store(IndexConfig())
    assert empty.ensure_fts_index() is False
    assert empty._source_domain.receipt()["state"] == "clean"
    empty.upsert(
        "code", [chunk("monorepo", "staleterm", path="stale.md")],
        inference(None, ["stale"]),
    )
    if all_sources:
        retired = [
            chunk("git:retired", "retiredterm", path="retired.md"),
            chunk("git:repository", "prefixterm", path="prefix.md"),
        ]
        empty.upsert("code", retired, inference(None, ["a", "b"]))
    queued(True)
    config = IndexConfig()
    store = initialize_source_store(config)
    initial = store.content_store.get_chunks_for_file("git:repo", "guide.md")
    assert initial
    assert "monorepo" not in store.distinct_sources()
    if all_sources:
        from agent_index.indexing.state import IndexState

        persisted = IndexState.load(config.state_file)
        assert persisted.stored_families["git:fixture"] == "git:repo"
        assert "git:repo:commits" in store.distinct_sources()
        assert "git:retired" not in store.distinct_sources()
        assert "git:repository" not in store.distinct_sources()
    assert store._source_domain.receipt()["fts_coverage"]["chunks"] == (
        store._source_domain.receipt()["versions"]["chunks"]
    )
    assert store.content_store.fts_search(
        "independently", source="git:repo", file_path_glob="guide.md",
    )
    assert SearchEngine({"code": QueryInference()}, store).search_authorized(
        "independently", families=("git:repo",), mode="hybrid",
    )
    file.write_text("# Needle\n\nreplacement autonomous ingestion\n", encoding="utf-8")
    run_git(repo, "add", "--", "guide.md")
    run_git(repo, "commit", "--quiet", "-m", "fixture update")
    if all_sources:
        store.upsert(
            "code", [chunk("git:retired", "incrementalretired", path="retired.md")],
            inference(None, ["x"]),
        )
    queued(False)
    restarted = initialize_source_store(config)
    current = restarted.content_store.get_chunks_for_file("git:repo", "guide.md")
    assert current and any("replacement" in r.content for r in current)
    assert not any("first independently" in r.content for r in current)
    assert restarted._source_domain.receipt()["fts_coverage"]["chunks"] == (
        restarted._source_domain.receipt()["versions"]["chunks"]
    )
    if all_sources:
        assert "git:retired" not in restarted.distinct_sources()
    assert restarted.content_store.fts_search(
        "replacement", source="git:repo", file_path_glob="guide.md",
    )
    assert restarted.content_store.fts_search(
        "independently", source="git:repo", file_path_glob="guide.md",
    ) == []
    hybrid = SearchEngine({"code": QueryInference()}, restarted).search_authorized(
        "replacement", families=("git:repo",), mode="hybrid",
    )
    assert any("replacement autonomous" in row.content for row in hybrid)
    assert all("first independently" not in row.content for row in hybrid)
    queued(False)
    assert {r.chunk_id for r in current} == {
        r.chunk_id for r in restarted.content_store.get_chunks_for_file("git:repo", "guide.md")
    }
    file.unlink()
    run_git(repo, "add", "-u")
    run_git(repo, "commit", "--quiet", "-m", "fixture delete")
    queued(False)
    assert initialize_source_store(config).content_store.get_chunks_for_file(
        "git:repo", "guide.md",
    ) == []
    assert initialize_source_store(config).content_store.fts_search(
        "replacement", source="git:repo", file_path_glob="guide.md",
    ) == []




def test_orphan_out_of_band_and_unknown_layout_stay_closed(cold):
    config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    table = store.get_vector_table("small")._get_or_create_table()
    table.update(where=f"chunk_id = '{item.chunk_id}'", values={"source": "git:wrong"})
    with pytest.raises(SourceLayoutError):
        with store._source_domain.read(store.content_store.db):
            pass
    with pytest.raises(SourceLayoutError):
        validate_source_store(store)
    marker = config.lance_dir / MARKER
    value = json.loads(marker.read_text(encoding="utf-8"))
    value["protocol"] = 99
    marker.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(SourceLayoutError):
        MultiModelStore(config.lance_dir)


def test_external_vector_loss_cannot_be_accepted_as_recovery(cold):
    _config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    before = store._source_domain.receipt()
    table = store.get_vector_table("small")._get_or_create_table()
    table.delete(f"chunk_id = '{item.chunk_id}'")  # Simulated unexplained external writer.
    assert table.count_rows() == 0
    assert store.content_store._get_or_create_table().count_rows() == 1
    with pytest.raises(SourceLayoutError, match="version drift"):
        validate_source_store(store)
    assert store._source_domain.receipt() == before
    with pytest.raises(SourceLayoutError):
        store.stats()
    with pytest.raises(SourceLayoutError):
        store.get_document("git:alpha", "a.md")


def test_configured_canonical_gc_retains_cached_and_defers_unknown(cold, tmp_path):
    from agent_index.indexing.gc import gc_configured_snapshot
    from agent_index.indexing.path_index import PathIndex
    from agent_index.indexing.state import IndexState

    _config, store = cold
    items = [chunk("git:cached", "cached"), chunk("git:cached:commits", "commit"),
             chunk("git:cachedlookalike", "retire"), chunk("git:removed", "retire too")]
    store.upsert("small", items, np.tile(np.array([[1, 0]], dtype=np.float32), (4, 1)))
    state = IndexState(stored_families={"git:configured-alias": "git:cached"})
    paths = PathIndex(tmp_path / "paths.db")
    deferred = gc_configured_snapshot(
        store, paths, state, configured_names=frozenset({"git:unknown"}), source_aware=True,
    )
    assert deferred["deferred"] == ["git:unknown"] and deferred["purged"] == {}
    result = gc_configured_snapshot(
        store, paths, state, configured_names=frozenset({"git:configured-alias"}),
        source_aware=True,
    )
    assert set(result["purged"]) == {"git:cachedlookalike", "git:removed"}
    assert store.distinct_sources() == {"git:cached", "git:cached:commits"}


@pytest.fixture
def queued_source(tmp_path, monkeypatch):
    from agent_index.engine.client import EngineClient
    from agent_index.indexing.task_store import TaskStore
    from agent_index.indexing.worker import run_worker

    def repository(name):
        repo = tmp_path / name
        repo.mkdir(parents=True)
        run_git(repo, "init", "--quiet")
        run_git(repo, "config", "user.name", "Synthetic Fixture")
        run_git(repo, "config", "user.email", "fixture@example.invalid")
        for file in ("one.md", "two.md"):
            (repo / file).write_text(f"# Needle\n\n{name} autonomous {file}\n", encoding="utf-8")
        run_git(repo, "add", "--", "one.md", "two.md")
        run_git(repo, "commit", "--quiet", "-m", "fixture")
        return repo

    def configure(repo, *, name="git:alias", extra=()):
        inline = {"role": "host", "corpus": {"sources": [
            {"name": alias, "type": "git", "_repo_path": str(path)}
            for alias, path in [(name, repo), *extra]
        ]}}
        monkeypatch.setenv("AGENT_INDEX_CONFIG_DATA_B64",
                           base64.urlsafe_b64encode(json.dumps(inline).encode()).decode())

    data = tmp_path / "data"
    for key, value in {
        "AGENT_INDEX_STORE_LAYOUT": LAYOUT, "AGENT_INDEX_DATA_DIR": str(data),
        "AGENT_INDEX_HOME": str(tmp_path / "home"), "AGENT_INDEX_SOURCE_MODE": "explicit",
        "AGENT_INDEX_INDEX_ENSURE_ENGINES": "0", "AGENT_INDEX_INDEXER_NICE": "0",
        "AGENT_INDEX_CLUSTER_ENABLED": "0", "AGENT_INDEX_GIT_FETCH": "0",
    }.items():
        monkeypatch.setenv(key, value)

    def inference(self, texts):
        vectors = np.zeros((len(texts), 768), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors

    monkeypatch.setattr(EngineClient, "embed_texts", inference)
    configure(repository("old"))
    queue = TaskStore(data / "tasks.db")

    def run(full=False, *, source="all"):
        task = queue.enqueue(source, full=full, trigger_source="fixture")
        assert queue.dequeue_next().id == task.id
        code = run_worker(task.id)
        return code, queue.get_task(task.id)

    return repository, configure, run, IndexConfig()


@pytest.mark.timeout(180)
def test_queued_source_identity_publishes_only_after_success(queued_source, monkeypatch):
    from agent_index import sources
    from agent_index.engine.client import EngineClient
    from agent_index.indexing.state import IndexState

    repository, configure, run, config = queued_source
    assert run(full=True)[1].status == "complete"
    configure(repository("new"))

    def fail(*args, **kwargs):
        raise RuntimeError("fixture source failure")

    for failure in ("current_commit", "discover_changed", "chunk", "embed", "reconcile"):
        with monkeypatch.context() as patch:
            if failure in ("current_commit", "discover_changed"):
                original = sources.get_connector

                def connector(*args, **kwargs):
                    result = original(*args, **kwargs)
                    patch.setattr(result, failure, fail)
                    return result

                patch.setattr(sources, "get_connector", connector)
            elif failure == "chunk":
                from agent_index import chunking

                class FailingChunker:
                    chunk = staticmethod(fail)

                patch.setattr(chunking, "get_chunker", lambda path: (FailingChunker(), "markdown"))
            elif failure == "embed":
                patch.setattr(EngineClient, "embed_texts", fail)
            else:
                patch.setattr(MultiModelStore, "delete_stale_by_file", fail)
            code, task = run()
            assert code == 0 and task.status == "partial", failure
        assert IndexState.load(config.state_file).stored_families == {"git:alias": "git:old"}
        store = initialize_source_store(config)
        assert "git:old" in store.distinct_sources()
        assert store.get_document("git:old", "one.md") is not None
    assert run()[1].status == "complete"
    assert IndexState.load(config.state_file).stored_families == {"git:alias": "git:new"}
    assert "git:old" not in store.distinct_sources()
    assert "git:new" in store.distinct_sources()
    # Empty/unchanged incremental success preserves the published canonical identity.
    assert run()[1].status == "complete"
    assert IndexState.load(config.state_file).stored_families == {"git:alias": "git:new"}


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    "target", ["cluster", "content-compact", "vector-compact", "configured-gc", "stale-gc"],
)
def test_queued_maintenance_effect_failure_never_completes(
    queued_source, monkeypatch, capsys, target,
):
    _repository, _configure, run, config = queued_source
    store = initialize_source_store(config)
    effects = []
    if target == "cluster":
        from agent_index.store.cluster_store import ClusterStore

        monkeypatch.setenv("AGENT_INDEX_CLUSTER_ENABLED", "1")
        original = ClusterStore._connect

        class Connection:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def commit(self):
                self.conn.commit()
                effects.append("commit")
                if len(effects) == 2:  # Schema commit succeeds, actual replacement commits then fails.
                    raise RuntimeError("fixture cluster committed then failed")

        monkeypatch.setattr(ClusterStore, "_connect", lambda self: Connection(original(self)))
    elif target.endswith("-gc"):
        # Fault-only retirement row, not ingestion evidence.
        vector = np.zeros((1, 768), dtype=np.float32)
        vector[:, 0] = 1
        store.upsert("code", [chunk("monorepo", "retirement fixture")], vector)
        table = store.content_store._get_or_create_table()
        original = type(table).delete

        def delete(table, predicate):
            result = original(table, predicate)
            if table.name == "chunks" and "monorepo" in predicate:
                effects.append(table.name)
                raise RuntimeError("fixture GC deletion effected then failed")
            return result

        monkeypatch.setattr(type(table), "delete", delete)
        if target == "stale-gc":
            from agent_index.indexing import gc

            monkeypatch.setattr(gc, "gc_configured_snapshot", lambda *a, **kw: {
                "purged": {}, "kept": [], "chunks_deleted": 0, "dry_run": False,
            })
    else:
        table = store.content_store._get_or_create_table()
        original = type(table).optimize
        expected = "chunks" if target == "content-compact" else "vectors_code"

        def optimize(table, *args, **kwargs):
            result = original(table, *args, **kwargs)
            if table.name == expected:
                receipt = store._source_domain.receipt()
                assert receipt["uncertain_effect"] is True
                assert receipt["operation"] == f"{target}:{expected}"
                if target == "content-compact":
                    assert "chunks" not in receipt.get("fts_coverage", {})
                table.update(values={"indexed_at": 123.0})
                effects.append(table.name)
                raise RuntimeError("fixture optimize effected then failed")
            return result

        monkeypatch.setattr(type(table), "optimize", optimize)
    code, task = run(full=True)
    assert effects and code == 1 and task.status == "failed"
    assert "\nIndexing complete:" not in capsys.readouterr().out
    receipt = store._source_domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
    assert receipt["operation"].startswith(
        "canonical-delete" if target.endswith("-gc") else target,
    )
    with pytest.raises(SourceLayoutError, match="uncertain effect"):
        validate_source_store(store)
    with pytest.raises(SourceLayoutError):
        store.stats()


def test_compact_success_empty_and_legacy_optional_failure(cold, tmp_path, monkeypatch):
    _config, store = cold
    store.compact()
    store.upsert("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    assert store.ensure_fts_index()
    store.compact()
    assert "chunks" not in store._source_domain.receipt().get("fts_coverage", {})
    assert store.ensure_fts_index()
    store.compact()
    assert store.ensure_fts_index()
    assert store._source_domain.receipt()["state"] == "clean"
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT")
    legacy = MultiModelStore(tmp_path / "legacy")
    table = legacy.content_store._get_or_create_table()

    def fail(*args, **kwargs):
        raise RuntimeError("fixture legacy optimize failure")

    monkeypatch.setattr(type(table), "optimize", fail)
    assert "content" in legacy.compact()


def test_selected_fts_failed_rebuild_is_not_old_index_availability(cold, monkeypatch):
    from agent_index.store import content_store as content_module

    _config, store = cold
    store.upsert("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    assert store.ensure_fts_index() is True
    content = store.content_store
    assert content.fts_available is True
    native_run = subprocess.run
    attempts = []

    def fail_real_child(argv, **kwargs):
        args = list(argv)
        assert "child_fts_owner" in args[4]
        attempts.append(args[-1])
        args[4] = (
            "import sys; from agent_index.store.source_layout import child_fts_owner;"
            "\nwith child_fts_owner(sys.argv[1],sys.argv[3]):"
            "\n raise RuntimeError('synthetic builder failure')"
        )
        return native_run(args, **kwargs)

    monkeypatch.setattr(content_module.subprocess, "run", fail_real_child)
    content.mark_fts_dirty()
    with pytest.raises(SourceLayoutError, match="FTS preparation failed"):
        content.ensure_fts_index(max_retries=1)
    assert attempts == ["0", "1"]  # Actual incremental and escalated child processes.
    assert content.fts_available is True and content.fts_dirty is True
    receipt = store._source_domain.receipt()
    assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
    assert "fts_pending" not in receipt  # Children settled; failed effects remain uncertain.
    with pytest.raises(SourceLayoutError):
        validate_source_store(store)
    with pytest.raises(SourceLayoutError):
        store.stats()


def test_empty_selected_fts_is_clean_noop_then_new_index_works(cold):
    config, store = cold
    before = store._source_domain.receipt()
    assert store.ensure_fts_index() is False
    covered = store._source_domain.receipt()
    assert covered["state"] == before["state"] == "clean"
    assert covered["versions"] == before["versions"]
    assert covered["fts_coverage"]["chunks"] == covered["versions"]["chunks"]
    assert store.ensure_fts_index() is False
    assert store._source_domain.receipt() == covered
    assert "fts_pending" not in before and "uncertain_effect" not in before
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    assert store.ensure_fts_index() is True
    assert store._source_domain.receipt()["state"] == "clean"
    assert initialize_source_store(config).get_document("git:alpha", "a.md") is not None


def test_required_small_scoped_query_and_inference_contract(cold):
    _config, store = cold
    items = [
        chunk("git:alpha", "needle alpha", path="a.md"),
        chunk("git:alpha:commits", "needle commit", path="b.md"),
        chunk("git:alphabet", "needle lookalike", path="c.md"),
        chunk("git:beta", "needle beta", path="d.md"),
    ]
    store.upsert("small", items, np.array([[1, 0], [0.8, 0.2], [1, 0], [1, 0]],
                                        dtype=np.float32))
    assert store.ensure_fts_index()
    calls = []

    class Inference:
        def embed_query(self, query):
            calls.append(query)
            return np.array([1, 0], dtype=np.float32)

    engine = SearchEngine({"small": Inference()}, store)
    for mode in ("vector", "fts", "hybrid"):
        before = len(calls)
        assert engine.search_authorized("needle", families=(), mode=mode) == []
        assert len(calls) == before
        hits = engine.search_authorized("needle", families=("git:alpha",), mode=mode)
        assert {hit.source for hit in hits} == {"git:alpha", "git:alpha:commits"}
        assert len(calls) - before == (0 if mode == "fts" else 1)


def test_stale_fts_and_hybrid_refuse_without_read_mutation(cold):
    _config, store = cold
    item = chunk(text="oldterm")
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    assert store.ensure_fts_index()
    store.upsert("small", [chunk(text="newterm")], np.array([[1, 0]], dtype=np.float32))
    receipt = store._source_domain.receipt()
    assert receipt["state"] == "clean" and "chunks" not in receipt.get("fts_coverage", {})
    from agent_index.store.source_search import search_source_store

    for vectors in ({}, {"small": np.array([1, 0], dtype=np.float32)}):
        with pytest.raises(SourceLayoutError, match="FTS is stale"):
            search_source_store(
                store, vectors, "newterm", families=("git:alpha",),
            )
    for operation in (
        lambda: store.fts_search("newterm"),
        lambda: store.search_all({"small": np.array([1, 0], dtype=np.float32)}, "newterm"),
        lambda: store.content_store.fts_search("newterm"),
    ):
        with pytest.raises(SourceLayoutError, match="FTS is stale"):
            operation()
    assert store._source_domain.receipt() == receipt
    assert store.ensure_fts_index()
    assert store._source_domain.receipt()["fts_coverage"]["chunks"] == (
        store._source_domain.receipt()["versions"]["chunks"]
    )


@pytest.mark.parametrize("blocked", ["writer", "dirty", "drift"])
def test_all_selected_public_reads_are_fenced(cold, blocked, monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from agent_index.server import build_app
    from agent_index.store.cluster_store import ClusterStore

    config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    clusters = ClusterStore(config.clusters_db)
    clusters.replace_all([])
    vector = np.array([1, 0], dtype=np.float32)

    class Inference:
        def embed_query(self, query):
            return vector

    engine = SearchEngine({"small": Inference()}, store)
    reads = [
        lambda: engine.search("needle"),
        lambda: store.search_all({"small": vector}, "needle"),
        lambda: store.hybrid_search("small", vector, "needle"),
        lambda: store.search("small", vector),
        lambda: store.find_similar(item.chunk_id),
        lambda: store.get_document("git:alpha", "a.md"),
        lambda: store.content_store.get_by_ids([item.chunk_id]),
        lambda: store.get_vector_table("small").search(vector),
        lambda: store.stats(),
        lambda: store.source_counts(),
        lambda: list(store.iter_item_representations()),
        lambda: clusters.list_clusters(),
        lambda: clusters.stats(),
    ]
    held, release = threading.Event(), threading.Event()
    thread = None
    if blocked == "writer":
        def writer():
            with store._source_domain.write(store.content_store.db):
                held.set()
                assert release.wait(15)
        thread = threading.Thread(target=writer)
        thread.start()
        assert held.wait(5)
    elif blocked == "dirty":
        with pytest.raises(RuntimeError):
            with store._source_domain.write(store.content_store.db):
                raise RuntimeError("fixture uncertain ownership")
    else:
        store.get_vector_table("small")._get_or_create_table().update(
            where=f"chunk_id = '{item.chunk_id}'", values={"language": "python"},
        )
    try:
        for read in reads:
            with pytest.raises(SourceLayoutError):
                read()
        from agent_index.search import engine as engine_module

        monkeypatch.setattr(engine_module, "create_search_engine", lambda: engine)
        response = TestClient(build_app()).get("/search", params={"q": "needle"})
        assert response.status_code == 200 and response.json()["available"] is False
        assert response.json()["hits"] == []
    finally:
        release.set()
        if thread is not None:
            thread.join(10)
            assert not thread.is_alive()


@pytest.mark.parametrize("fault", ["vector-add", "canonical-delete"])
def test_uncertain_membership_loss_cannot_be_validated_clean(cold, monkeypatch, fault):
    _config, store = cold
    item = chunk()
    store.upsert("small", [item], np.array([[1, 0]], dtype=np.float32))
    vt = store.get_vector_table("small")._get_or_create_table()
    canonical = store.content_store._get_or_create_table()

    def fail(*args, **kwargs):
        raise RuntimeError("fixture fault after destructive boundary")

    if fault == "vector-add":
        monkeypatch.setattr(vt, "add", fail)
        with pytest.raises(RuntimeError):
            store.upsert_vectors("small", [item], np.array([[0, 1]], dtype=np.float32))
    else:
        monkeypatch.setattr(canonical, "delete", fail)
        with pytest.raises(RuntimeError):
            store.delete_by_file("git:alpha", "a.md")
    vt.checkout_latest()
    canonical.checkout_latest()
    assert vt.count_rows() == 0 and canonical.count_rows() == 1
    assert store._source_domain.receipt()["uncertain_effect"] is True
    with pytest.raises(SourceLayoutError, match="uncertain effect"):
        validate_source_store(store)
    with pytest.raises(SourceLayoutError):
        store.get_document("git:alpha", "a.md")


@pytest.mark.parametrize("weights", [None, (0.5, 4.0)])
def test_full_scope_hybrid_ranking_matches_native_weights(cold, monkeypatch, weights):
    from agent_index.store import multi_model_store as native

    config, store = cold
    semantic = chunk(text="authoritative semantic answer", path="semantic.md")
    lexical = chunk(text="needle needle exact lexical answer", path="lexical.md")
    store.upsert("small", [semantic, lexical], np.array([[1, 0], [0, 1]], dtype=np.float32))
    assert store.ensure_fts_index()
    if weights is not None:
        monkeypatch.setenv("AGENT_INDEX_VECTOR_WEIGHT", str(weights[0]))
        monkeypatch.setenv("AGENT_INDEX_FTS_WEIGHT", str(weights[1]))
        import importlib

        importlib.reload(native)
        store = initialize_source_store(config)
        assert store.ensure_fts_index()
    try:
        vector = np.array([1, 0], dtype=np.float32)
        ordinary = store.search_all({"small": vector}, "needle", limit=10)
        from agent_index.store.source_search import search_source_store

        scoped = search_source_store(
            store, {"small": vector}, "needle", families=("git:alpha",), limit=10,
        )
        assert [(h.chunk_id, h.score) for h in scoped] == [
            (h.chunk_id, h.score) for h in ordinary
        ]
    finally:
        if weights is not None:
            monkeypatch.delenv("AGENT_INDEX_VECTOR_WEIGHT")
            monkeypatch.delenv("AGENT_INDEX_FTS_WEIGHT")
            importlib.reload(native)


@pytest.mark.timeout(90)
def test_fts_child_retains_actual_ownership_after_parent_death(cold):
    config, store = cold
    store.upsert("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    path = config.lance_dir
    parent = subprocess.Popen(  # noqa: S603 -- fixed owned process fixture
        [sys.executable, "-I", str(Path(__file__).with_name("_source_fts_process.py")),
         "parent", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )

    def wait_file(name):
        deadline = time.monotonic() + 25
        while not (path / name).exists() and time.monotonic() < deadline:
            if parent.poll() is not None and not (path / "builder-ready").exists():
                out, err = parent.communicate(timeout=2)
                pytest.fail(f"owned parent exited before builder readiness: {out} {err}")
            time.sleep(0.05)
        assert (path / name).exists(), name

    try:
        wait_file("builder-ready")
        parent.terminate()  # Only the specific fixture parent; builder stays runner-contained.
        parent.wait(timeout=5)
        for operation in (store.stats, lambda: validate_source_store(store)):
            with pytest.raises(SourceLayoutError):
                operation()
        (path / "release-builder").touch()
        wait_file("builder-finished")
        # Builder no longer writes, but still lives: actual kernel guard remains owned.
        for operation in (store.stats, lambda: validate_source_store(store)):
            with pytest.raises(SourceLayoutError):
                operation()
        (path / "exit-builder").touch()
        deadline = time.monotonic() + 8
        from agent_index.store.source_layout import FTS_LOCK, kernel_lock

        while True:
            try:
                guard = kernel_lock(path / FTS_LOCK)
                guard.close()
                break
            except SourceLayoutError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        # Parent-death uncertainty is retained, not silently reclassified as clean.
        with pytest.raises(SourceLayoutError, match="uncertain effect"):
            validate_source_store(store)
    finally:
        (path / "release-builder").touch()
        (path / "exit-builder").touch()
        if parent.poll() is None:
            parent.terminate()
        out, err = parent.communicate(timeout=5)
        assert "Traceback" not in out + err or parent.returncode != 0
