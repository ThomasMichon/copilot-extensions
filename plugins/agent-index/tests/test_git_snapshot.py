"""Real Git snapshots resume immutable file and history coverage."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from test_git_delta import commit, connector, forbidden, git, repo as _repo

from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls, recovery_scope
from agent_index.indexing.snapshot_crawl import acquire_snapshot
from agent_index.sources import git_snapshot
from agent_index.sources.git_repo import GitRepoConnector

repo = _repo


def pages(source):
    discover = source.discover_snapshot_page
    assert discover is not None
    snapshot = cursor = None
    while True:
        page = discover(snapshot, cursor)
        yield page
        if page.cursor is None:
            break
        snapshot, cursor = page.snapshot, page.cursor


def test_snapshot_matches_legacy_and_never_uses_whole_corpus_helpers(repo, monkeypatch):
    (repo / "nested").mkdir()
    (repo / "nested" / "new.py").write_text("print('nested')\n", encoding="utf-8")
    (repo / "binary.md").write_bytes(b"binary\0content")
    target = commit(repo)
    source = connector(repo)
    expected = {(entry.source, entry.path): entry for entry in source.discover()}
    commands = []
    read_text, read_bytes = source._git, source._git_bytes

    def measured_text(args):
        commands.append(args)
        return read_text(args)

    def measured_bytes(args):
        commands.append(args)
        return read_bytes(args)

    monkeypatch.setattr(source, "_git", measured_text)
    monkeypatch.setattr(source, "_git_bytes", measured_bytes)
    for name in ("discover", "_tracked_paths", "list_paths", "_commit_shas"):
        monkeypatch.setattr(source, name, forbidden)
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 2)
    result = list(pages(source))
    assert all(page.commit == target and len(page.upserts) <= 2 for page in result)
    assert len({page.snapshot for page in result}) == 1
    entries = [entry for page in result for entry in page.upserts]
    assert len(entries) == len(expected)
    assert {(entry.source, entry.path): entry for entry in entries} == expected
    assert not any("ls-tree" in args or "show" in args for args in commands)
    history = [args for args in commands if "log" in args]
    assert history
    assert all(
        ("--max-count=2" in args or "--max-count=3" in args)
        and "--topo-order" in args and target in args
        and any(arg.startswith("--skip=") for arg in args)
        for args in history
    )


def test_resume_uses_pinned_objects_after_branch_moves_and_reopens_journal(
    repo, tmp_path, monkeypatch,
):
    (repo / "nested").mkdir()
    (repo / "nested" / "new.md").write_text("Pinned content.\n", encoding="utf-8")
    target = commit(repo)
    source = connector(repo)
    expected = {(entry.source, entry.path): entry for entry in source.discover()}
    family = source.source_name
    scope = recovery_scope("alias", source)
    db = tmp_path / "paths.db"
    paths = PathIndex(db)
    old = [(family, "gone.md"), (family + ":commits", "old-history")]
    paths.mark_indexed_batch([(stream, path, "old", 1) for stream, path in old])
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 2)
    calls = []

    def run():
        fresh = connector(repo)
        paths = PathIndex(db)
        discover = fresh.discover_snapshot_page
        assert discover is not None

        def measured(snapshot, cursor, **kwargs):
            calls.append((snapshot, cursor))
            return discover(snapshot, cursor, **kwargs)

        return acquire_snapshot(
            "alias", family=family, stored_family=family, scope=scope,
            last_commit="base", journal=PendingCrawls(paths), discover=measured, unit_budget=1,
        )

    with pytest.raises(RuntimeError, match="unit budget"):
        run()
    saved = PendingCrawls(paths).read_payload("alias", scope)
    assert saved is not None and json.loads(saved)["version"] == 4
    (repo / "keep.md").write_text("Moving branch must not change this snapshot.\n", encoding="utf-8")
    (repo / "nested" / "new.md").unlink()
    moved = commit(repo)
    assert moved != target
    for _ in range(50):
        try:
            result = run()
            break
        except RuntimeError as exc:
            assert "unit budget" in str(exc)
    else:
        pytest.fail("Git full snapshot failed to finish its bounded units")
    assert result.commit == target
    assert {(entry.source, entry.path): entry for entry in result.upserts} == expected
    assert {(entry.source, entry.path) for entry in result.deletions} == set(old)
    assert calls[0] == (None, None)
    assert all(snapshot == git_snapshot._PREFIX + target for snapshot, _ in calls[1:])
    assert len({cursor for _, cursor in calls}) == len(calls)
    assert paths.count() == 2
    assert PendingCrawls(paths).load("alias", scope) is not None


def test_excluded_files_still_consume_page_budget(repo, monkeypatch):
    for index in range(300):
        (repo / f"binary-{index:03d}.png").write_bytes(b"not indexed")
    commit(repo)
    source = connector(repo)
    discover = source.discover_snapshot_page
    assert discover is not None
    read = source._git_bytes
    blobs = []

    def measured(args):
        if "blob" in args:
            blobs.append(args)
        return read(args)

    monkeypatch.setattr(source, "_git_bytes", measured)
    page = discover(None, None)
    assert page.upserts == []
    assert page.cursor is not None
    assert blobs == []
    assert len(list(pages(source))) == 4


@pytest.mark.parametrize("bad", [
    "", "[]", '{"history":true}', '{"history":-1}', '{"files":[]}',
    '{"history":true,"previous":null}', '{"history":-1,"previous":null}',
    '{"history":1,"previous":null}', '{"history":0,"previous":"invalid"}',
    '{"files":[["",0],["../escape",0]]}', '{"files":[["",0],["a/b",0]]}',
    '{"files":[["",0],["child",-1]]}', '{"files":[["",false]]}',
    '{"files":[["other",0]]}', '{"files":[["",0]],"history":0}',
    '{"files":[["",0],["bad\\u0000path",0]]}',
])
def test_invalid_cursor_refuses_before_git(bad, monkeypatch):
    monkeypatch.setattr(git_snapshot, "_stable_graph", forbidden)
    source = object.__new__(GitRepoConnector)
    with pytest.raises(ValueError):
        git_snapshot.discover_git_snapshot(source, git_snapshot._PREFIX + "a" * 40, bad)


def test_missing_snapshot_commit_does_not_replace_pending_progress(repo, tmp_path, monkeypatch):
    source = connector(repo)
    discover = source.discover_snapshot_page
    assert discover is not None
    paths = PathIndex(tmp_path / "paths.db")
    journal = PendingCrawls(paths)
    scope = recovery_scope("alias", source)
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 1)
    kwargs = dict(
        family=source.source_name, stored_family=None, scope=scope, last_commit="base",
        journal=journal, discover=discover, unit_budget=1,
    )
    with pytest.raises(RuntimeError, match="unit budget"):
        acquire_snapshot("alias", **kwargs)
    before = journal.read_payload("alias", scope)
    read = source._git

    def missing(args):
        if args[:2] == ["cat-file", "-t"]:
            raise subprocess.CalledProcessError(128, ["git", *args], stderr="missing object")
        return read(args)

    monkeypatch.setattr(source, "_git", missing)
    with pytest.raises(subprocess.CalledProcessError):
        acquire_snapshot("alias", **kwargs)
    assert journal.read_payload("alias", scope) == before
    assert journal.load("alias", scope) is None


def test_tree_limit_refuses_before_loading_object(repo, monkeypatch):
    source = connector(repo)
    target = source.current_commit()
    read = source._git

    def oversized(args):
        if args[:2] == ["cat-file", "-s"]:
            return str(git_snapshot._MAX_TREE_BYTES + 1)
        return read(args)

    monkeypatch.setattr(source, "_git", oversized)
    monkeypatch.setattr(source, "_git_bytes", forbidden)
    with pytest.raises(ValueError, match="tree limit"):
        git_snapshot.discover_git_snapshot(source, git_snapshot._PREFIX + target, None)


def test_working_tree_keeps_legacy_and_shallow_or_grafted_views_refuse(
    repo, tmp_path,
):
    assert GitRepoConnector(repo_path=repo, fetch=False).discover_snapshot_page is None
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "--depth=1", "--no-local", repo.as_uri(), str(shallow))
    source = connector(shallow)
    discover = source.discover_snapshot_page
    assert discover is not None
    with pytest.raises(ValueError, match="shallow"):
        discover(None, None)
    graft = repo / ".git" / "info" / "grafts"
    graft.write_text(git(repo, "rev-parse", "HEAD") + "\n", encoding="utf-8")
    discover = connector(repo).discover_snapshot_page
    assert discover is not None
    with pytest.raises(ValueError, match="grafts"):
        discover(None, None)


def test_replace_refs_do_not_change_snapshot_content_or_history(repo):
    target = git(repo, "rev-parse", "HEAD")
    original = connector(repo).discover()
    (repo / "keep.md").write_text("Replacement content.\n", encoding="utf-8")
    replacement = commit(repo)
    git(repo, "replace", target, replacement)
    source = GitRepoConnector(repo_path=repo, ref=target, fetch=False)
    entries = [entry for page in pages(source) for entry in page.upserts]
    assert {(entry.source, entry.path): entry for entry in entries} == {
        (entry.source, entry.path): entry for entry in original
    }


def test_annotated_tag_pins_the_peeled_commit(repo):
    target = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "sample-tag", "-m", "Annotated fixture tag")
    assert git(repo, "rev-parse", "sample-tag") != target
    source = GitRepoConnector(repo_path=repo, ref="sample-tag", fetch=False)
    result = list(pages(source))
    assert all(page.commit == target for page in result)
    assert all(
        entry.metadata["commit"] == target
        for page in result for entry in page.upserts if entry.language != "commit"
    )
    assert source.current_commit() == target
    delta = source.discover_delta(target)
    assert delta.commit == target and delta.upserts == [] and delta.deletions == []


def test_history_pages_cover_merges_exactly_once(repo, monkeypatch):
    git(repo, "checkout", "-b", "side")
    (repo / "side.md").write_text("Side branch.\n", encoding="utf-8")
    commit(repo)
    git(repo, "checkout", "main")
    for index in range(3):
        git(repo, "commit", "--allow-empty", "-m", f"Main branch {index}")
    git(repo, "merge", "--no-ff", "side", "-m", "Merge fixture history")
    source = connector(repo)
    expected = {entry.path for entry in source.discover() if entry.language == "commit"}
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 2)
    actual = [
        entry.path for page in pages(source) for entry in page.upserts if entry.language == "commit"
    ]
    assert len(actual) == len(set(actual)) == len(expected) == 6
    assert set(actual) == expected


@pytest.mark.skipif(os.name == "nt", reason="Win32 forbids control-character filenames")
def test_snapshot_preserves_nul_framed_unusual_paths(repo):
    directory = repo / "line\nand\ttab"
    directory.mkdir()
    (directory / "colon:name.md").write_text("Unusual path.\n", encoding="utf-8")
    commit(repo)
    expected = {(entry.source, entry.path): entry for entry in connector(repo).discover()}
    actual = [entry for page in pages(connector(repo)) for entry in page.upserts]
    assert {(entry.source, entry.path): entry for entry in actual} == expected


def test_snapshot_decodes_sha256_tree_object_ids(tmp_path):
    path = tmp_path / "sha256"
    path.mkdir()
    git(path, "init", "-b", "main", "--object-format=sha256")
    git(path, "config", "user.name", "Example")
    git(path, "config", "user.email", "dev@example.test")
    (path / "readme.md").write_text("SHA-256 tree.\n", encoding="utf-8")
    target = commit(path)
    assert len(target) == 64
    result = list(pages(connector(path)))
    assert all(page.commit == target for page in result)
    entries = [entry for page in result for entry in page.upserts]
    assert {entry.path for entry in entries} == {"readme.md", f"commits/{target}.txt"}


def test_restored_offset_inside_mode_like_filename_cannot_complete_acquisition(
    repo, tmp_path, monkeypatch,
):
    real_path = "prefix100644 victim.md"
    (repo / real_path).write_text("Must not be replaced by an invented identity.\n", encoding="utf-8")
    target = commit(repo)
    source = connector(repo)
    discover = source.discover_snapshot_page
    assert discover is not None
    paths = PathIndex(tmp_path / "paths.db")
    family = source.source_name
    paths.mark_indexed_batch([(family, real_path, "old", 1)])
    journal = PendingCrawls(paths)
    scope = recovery_scope("alias", source)
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 1)
    kwargs = dict(
        family=family, stored_family=None, scope=scope, last_commit="base",
        journal=journal, discover=discover, unit_budget=1,
    )
    with pytest.raises(RuntimeError, match="unit budget"):
        acquire_snapshot("alias", **kwargs)
    before = journal.read_payload("alias", scope)
    tree = source._git_bytes(["cat-file", "tree", target + ":"])
    bad_offset = tree.index(b"100644 victim.md")
    data = json.loads(before)
    data["cursor"] = json.dumps({"files": [["", bad_offset]]})
    corrupted = json.dumps(data)
    journal.compare_and_save("alias", scope=scope, payload=corrupted, expected=before, family=family)
    with pytest.raises(ValueError, match="tree-record boundary"):
        acquire_snapshot("alias", **kwargs)
    assert journal.read_payload("alias", scope) == corrupted
    assert journal.load("alias", scope) is None
    assert paths.count() == 1


def test_deep_git_tree_cursor_grows_linearly_and_finishes(repo):
    source = connector(repo)

    def input_git(args, content):
        return subprocess.run(
            source._git_command(args), cwd=repo, input=content, capture_output=True,
            check=True, **no_window_kwargs(),
        ).stdout.decode().strip()

    blob = git(repo, "rev-parse", "HEAD:keep.md")
    tree = input_git(["mktree", "-z"], f"100644 blob {blob}\tleaf.md\0".encode())
    names = [f"segment{index:04d}" for index in range(104)]
    for name in reversed(names):
        tree = input_git(["mktree", "-z"], f"040000 tree {tree}\t{name}\0".encode())
    target = input_git(["commit-tree", tree], b"Deep synthetic snapshot\n")
    result = list(pages(GitRepoConnector(repo_path=repo, ref=target, fetch=False)))
    assert max(len(page.cursor.encode()) for page in result if page.cursor) < 7000
    entries = [entry for page in result for entry in page.upserts]
    assert {entry.path for entry in entries} == {
        "/".join([*names, "leaf.md"]), f"commits/{target}.txt",
    }


def test_pre_policy_checkpoint_requires_explicit_reconciliation_before_acquisition(
    repo, tmp_path, monkeypatch,
):
    from agent_index.indexing.crawl import collect_source_changes
    from agent_index.indexing.state import IndexState, SourceState

    source = connector(repo)
    target = source.current_commit()
    assert source.recovery_identity[-1] == "git-objects:canonical-v1"
    previous = SimpleNamespace(
        source_name=source.source_name, recovery_identity=source.recovery_identity[:-1],
    )
    old_scope = recovery_scope("alias", previous)
    state = IndexState(sources={"alias": SourceState(
        last_commit=target, acquisition_scope=old_scope,
    )})
    paths = PathIndex(tmp_path / "paths.db")
    monkeypatch.setattr(source, "discover_delta", forbidden)
    with pytest.raises(ValueError, match="explicit full reindex required"):
        collect_source_changes(
            "alias", state=state, path_index=paths, full=False,
            source_aware=False, connector=source,
        )
    assert state.sources["alias"].last_commit == target
    assert state.sources["alias"].acquisition_scope == old_scope
    assert paths.count() == 0
    assert PendingCrawls(paths).sources() == []


def test_restored_parent_frame_must_point_immediately_after_its_active_child(
    repo, tmp_path, monkeypatch,
):
    for name in ("a", "b"):
        (repo / name).mkdir()
        (repo / name / "leaf.md").write_text(name + "\n", encoding="utf-8")
    target = commit(repo)
    source = connector(repo)
    discover = source.discover_snapshot_page
    assert discover is not None
    paths = PathIndex(tmp_path / "paths.db")
    family = source.source_name
    paths.mark_indexed_batch([(family, "b/leaf.md", "old", 1)])
    journal = PendingCrawls(paths)
    scope = recovery_scope("alias", source)
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 1)
    kwargs = dict(
        family=family, stored_family=None, scope=scope, last_commit="base",
        journal=journal, discover=discover, unit_budget=1,
    )
    with pytest.raises(RuntimeError, match="unit budget"):
        acquire_snapshot("alias", **kwargs)
    before = journal.read_payload("alias", scope)
    tree = source._git_bytes(["cat-file", "tree", target + ":"])
    after_b = tree.index(b" b\0") + 3 + len(target) // 2
    data = json.loads(before)
    data["cursor"] = json.dumps({"files": [["", after_b], ["a", 0]]})
    corrupted = json.dumps(data)
    journal.compare_and_save("alias", scope=scope, payload=corrupted, expected=before, family=family)
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 128)
    with pytest.raises(ValueError, match="parent continuation"):
        acquire_snapshot("alias", **kwargs)
    assert journal.read_payload("alias", scope) == corrupted
    assert journal.load("alias", scope) is None
    assert paths.count() == 1


@pytest.mark.parametrize("offset,wrong_predecessor", [(100, False), (1, True)])
def test_invalid_history_ordinal_cannot_certify_terminal_coverage(
    repo, tmp_path, monkeypatch, offset, wrong_predecessor,
):
    source = connector(repo)
    target = source.current_commit()
    discover = source.discover_snapshot_page
    assert discover is not None
    paths = PathIndex(tmp_path / "paths.db")
    family = source.source_name
    paths.mark_indexed_batch([(family + ":commits", f"commits/{target}.txt", "old", 1)])
    journal = PendingCrawls(paths)
    scope = recovery_scope("alias", source)
    monkeypatch.setattr(git_snapshot, "_PAGE_SIZE", 1)
    kwargs = dict(
        family=family, stored_family=None, scope=scope, last_commit="base",
        journal=journal, discover=discover, unit_budget=1,
    )
    with pytest.raises(RuntimeError, match="unit budget"):
        acquire_snapshot("alias", **kwargs)
    before = journal.read_payload("alias", scope)
    data = json.loads(before)
    data["cursor"] = json.dumps({
        "history": offset, "previous": "a" * 40 if wrong_predecessor else target,
    })
    corrupted = json.dumps(data)
    journal.compare_and_save("alias", scope=scope, payload=corrupted, expected=before, family=family)
    with pytest.raises(ValueError, match="pinned predecessor"):
        acquire_snapshot("alias", **kwargs)
    assert journal.read_payload("alias", scope) == corrupted
    assert journal.load("alias", scope) is None
    assert paths.count() == 1
