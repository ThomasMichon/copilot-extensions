"""Selected canonical family reconciliation and configured crawl-state retirement."""

from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pytest
from test_source_aware_store import chunk, cold as cold, queued_source as queued_source, run_git

from agent_index.indexing.gc import gc_configured_snapshot
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState, SourceState
from agent_index.store.source_layout import initialize_source_store


@pytest.mark.parametrize("family", ["git:alpha_one", "git:alpha%one"])
def test_path_family_lookup_is_literal_and_case_sensitive(tmp_path, family):
    paths = PathIndex(tmp_path / "paths.db")
    sources = {
        family, f"{family}:commits", f"{family}:commits:more",
        family.upper(), f"{family.upper()}:commits", f"{family}extra:commits",
        family.replace("_", "X").replace("%", "X") + ":commits",
    }
    for source in sources:
        paths.mark_indexed(source, "one.md")
    expected = {source: {"one.md"} for source in sources
                if source == family or source.startswith(f"{family}:")}
    assert paths.get_paths_by_family(family) == expected
    assert set(paths.get_paths_by_prefix(family)) > set(expected)  # Legacy API stays compatible.


@pytest.mark.timeout(180)
def test_queued_reconcile_changes_only_exact_canonical_family(queued_source):
    repository, configure, run, config = queued_source
    root = repository("alpha_one")
    others = [repository(name) for name in
              ("alphaXone", Path("case") / "ALPHA_ONE", "alpha_oneextra")]
    configure(root, name="git:root", extra=[
        (f"git:other{i}", repo) for i, repo in enumerate(others)
    ])
    assert run(full=True)[1].status == "complete"
    store = initialize_source_store(config)
    paths = PathIndex(config.data_dir / "path_index.db")
    retained = {}
    for repo in others:
        family = f"git:{repo.name}"
        assert f"{family}:commits" in paths.get_paths_by_family(family)
        retained[family] = {
            key: value for key, value in paths.get_all_entries().items()
            if key[0] == family or key[0].startswith(f"{family}:")
        }
    ids = [
        row.chunk_id for family in retained
        for source, files in paths.get_paths_by_family(family).items()
        for file in files for row in store.content_store.get_chunks_for_file(source, file)
    ]
    vectors = store.get_vector_table("code").get_vectors(ids)
    assert len(vectors) == len(ids)
    old_root = store.content_store.get_chunks_for_file("git:alpha_one", "one.md")
    (root / "one.md").unlink()
    (root / "two.md").write_text("# Needle\n\nchanged root only\n", encoding="utf-8")
    run_git(root, "add", "-u")
    run_git(root, "commit", "--quiet", "-m", "fixture root update")
    for full in (False, True):
        assert run(full=full, source="git:root")[1].status == "complete"
        assert store.content_store.get_chunks_for_file("git:alpha_one", "one.md") == []
        assert not store.get_vector_table("code").get_vectors([row.chunk_id for row in old_root])
        assert any("changed root only" in row.content for row in
                   store.content_store.get_chunks_for_file("git:alpha_one", "two.md"))
        for family, before in retained.items():
            assert {key: value for key, value in paths.get_all_entries().items()
                    if key[0] == family or key[0].startswith(f"{family}:")} == before
        after = store.get_vector_table("code").get_vectors(ids)
        assert after.keys() == vectors.keys()
        for identity, vector in vectors.items():
            np.testing.assert_array_equal(after[identity], vector)
    assert len(paths.get_paths_by_family("git:alpha_one")["git:alpha_one:commits"]) == 2


@pytest.mark.timeout(120)
def test_queued_alias_equal_old_family_retires_rows_not_active_cursor(queued_source):
    repository, configure, run, config = queued_source
    old = config.data_dir.parent / "old"
    configure(old, name="git:old")
    assert run(full=True)[1].status == "complete"
    configure(repository("new"), name="git:old")
    assert run()[1].status == "complete"
    store = initialize_source_store(config)
    state = IndexState.load(config.state_file)
    assert state.stored_families == {"git:old": "git:new"}
    assert set(state.sources) == {"git:old"} and state.sources["git:old"].last_commit
    assert state.sources["git:old"].chunk_count > 0
    assert store.distinct_sources() == {"git:new", "git:new:commits"}
    assert PathIndex(config.data_dir / "path_index.db").get_paths_by_family("git:old") == {}
    cursor = state.sources["git:old"]
    assert run()[1].status == "complete"
    assert IndexState.load(config.state_file).sources["git:old"].last_commit == cursor.last_commit
    assert store.get_document("git:new", "one.md") is not None


@pytest.mark.timeout(180)
def test_queued_removed_shared_alias_and_incremental_readd_restore_content(queued_source):
    repository, configure, run, config = queued_source
    repo, anchor = repository("repo"), repository("anchor")
    configure(repo, extra=[("git:mirror", repo), ("git:anchor", anchor)])
    assert run(full=True)[1].status == "complete"
    store = initialize_source_store(config)
    paths = PathIndex(config.data_dir / "path_index.db")
    rows = [row for source, files in paths.get_paths_by_family("git:repo").items()
            for file in files for row in store.content_store.get_chunks_for_file(source, file)]
    ids = [row.chunk_id for row in rows]
    cursor = IndexState.load(config.state_file).sources["git:mirror"].last_commit
    configure(anchor, name="git:anchor", extra=[("git:mirror", repo)])
    assert run()[1].status == "complete"
    state = IndexState.load(config.state_file)
    assert "git:alias" not in state.sources and "git:alias" not in state.stored_families
    assert state.sources["git:mirror"].last_commit == cursor
    assert len(store.get_vector_table("code").get_vectors(ids)) == len(ids)
    assert store.get_document("git:repo", "one.md") is not None
    configure(anchor, name="git:anchor")
    assert run()[1].status == "complete"
    state = IndexState.load(config.state_file)
    assert set(state.sources) == set(state.stored_families) == {"git:anchor"}
    assert paths.get_paths_by_family("git:repo") == {}
    assert store.get_document("git:repo", "one.md") is None
    assert not store.get_vector_table("code").get_vectors(ids)
    configure(repo, extra=[("git:anchor", anchor)])
    assert run()[1].status == "complete"  # No full reindex and the repository is unchanged.
    assert store.get_document("git:repo", "one.md") is not None
    assert len(store.get_vector_table("code").get_vectors(ids)) == len(ids)
    assert IndexState.load(config.state_file).sources["git:alias"].last_commit == cursor


@pytest.mark.timeout(180)
def test_retired_pending_alias_restores_without_losing_unchanged_files(queued_source, monkeypatch):
    from agent_index.indexing import engine
    from agent_index.indexing.pending_crawl import PendingCrawls

    repository, configure, run, config = queued_source
    repo, anchor = repository("repo"), repository("anchor")
    (repo / "unchanged.md").write_text("Retained through source retirement\n", encoding="utf-8")
    run_git(repo, "add", "unchanged.md")
    run_git(repo, "commit", "--quiet", "-m", "fixture unchanged file")
    configure(repo, extra=[("git:anchor", anchor)])
    assert run(full=True)[1].status == "complete"
    (repo / "one.md").write_text("Pending changed content\n", encoding="utf-8")
    run_git(repo, "add", "one.md")
    run_git(repo, "commit", "--quiet", "-m", "fixture acquired update")
    with monkeypatch.context() as patch:
        def interrupt(*args, **kwargs):
            raise RuntimeError("fixture acquisition interrupted before apply")

        patch.setattr(engine, "_apply_source_manifest", interrupt)
        assert run(source="git:alias")[1].status == "partial"
    paths = PathIndex(config.data_dir / "path_index.db")
    assert PendingCrawls(paths).sources() == ["git:alias"]
    configure(anchor, name="git:anchor")
    code, task = run(full=True)
    assert code == 0 and task.status == "partial"
    assert task.result_stats["source_gc_deferred"] == ["git:alias"]
    state = IndexState.load(config.state_file)
    assert "git:alias" in state.sources
    store = initialize_source_store(config)
    assert store.get_document("git:repo", "unchanged.md") is not None
    configure(repo, extra=[("git:anchor", anchor)])
    assert run()[1].status == "complete"
    assert PendingCrawls(paths).sources() == []
    assert store.get_document("git:repo", "unchanged.md") is not None
    assert any("Pending changed content" in row.content for row in
               store.content_store.get_chunks_for_file("git:repo", "one.md"))


@pytest.mark.parametrize(
    "failure", ["source-scan", "before-delete", "after-one-delete", "path-delete", "path-scan"],
)
def test_configured_gc_prunes_alias_state_only_after_success(cold, tmp_path, monkeypatch, failure):
    _config, store = cold
    items = [chunk("git:keep"), chunk("git:gone1"), chunk("git:gone2")]
    store.upsert("small", items, np.tile(np.array([[1, 0]], dtype=np.float32), (3, 1)))
    state = IndexState(
        sources={name: SourceState(last_commit="fixture", chunk_count=1)
                 for name in ("git:active", "git:retired1", "git:retired2")},
        stored_families={"git:active": "git:keep", "git:retired1": "git:gone1",
                         "git:retired2": "git:gone2"}, total_chunks=3,
    )
    before = copy.deepcopy(state)
    paths = PathIndex(tmp_path / "paths.db")
    for item in items:
        paths.mark_indexed(item.source, item.file_path)
    deferred = gc_configured_snapshot(
        store, paths, state, configured_names=frozenset({"git:unknown"}), source_aware=True,
    )
    assert deferred["deferred"] == ["git:unknown"] and state == before
    delete = store.delete_by_source_exact
    calls = []

    def failing_delete(source):
        if len(calls) == (1 if failure == "after-one-delete" else 0):
            raise RuntimeError("fixture configured GC failure")
        calls.append(source)
        return delete(source)

    with monkeypatch.context() as patch:
        if failure == "source-scan":
            def fail_scan(*args, **kwargs):
                raise RuntimeError("fixture source-count scan failure")

            patch.setattr(store.content_store._get_or_create_table(), "search", fail_scan)
        elif failure == "path-delete":
            def fail_path(source):
                raise RuntimeError("fixture path-index failure after data deletion")

            patch.setattr(paths, "delete_source", fail_path)
        elif failure == "path-scan":
            def fail_sources():
                raise RuntimeError("fixture path source scan failure")

            patch.setattr(paths, "list_sources", fail_sources)
        else:
            patch.setattr(store, "delete_by_source_exact", failing_delete)
        with pytest.raises(RuntimeError, match="fixture"):
            gc_configured_snapshot(
                store, paths, state, configured_names=frozenset({"git:active"}), source_aware=True,
            )
    assert state == before
    gc_configured_snapshot(
        store, paths, state, configured_names=frozenset({"git:active"}), source_aware=True,
    )
    assert set(state.sources) == set(state.stored_families) == {"git:active"}
    assert state.total_chunks == 1 and store.distinct_sources() == {"git:keep"}
    assert paths.list_sources() == ["git:keep"]
