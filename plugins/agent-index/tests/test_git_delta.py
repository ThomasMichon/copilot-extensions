"""Git ranges carry complete tombstones without enumerating unchanged state."""

from __future__ import annotations

import shutil
import subprocess
import os
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_index.indexing.crawl import collect_source_changes
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import recovery_scope
from agent_index.indexing.state import IndexState, SourceState
from agent_index.sources.git_delta import GitReconciliationRequired
from agent_index.sources.git_repo import GitRepoConnector


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [shutil.which("git") or "git", *args], cwd=repo,
        check=True, capture_output=True, text=True, encoding="utf-8",
        **no_window_kwargs(),
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "sample"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Example")
    git(path, "config", "user.email", "dev@example.test")
    for name in ("keep.md", "edit.md", "remove.md", "rename.md", "binary.md"):
        (path / name).write_text(name + "\n", encoding="utf-8")
    commit(path)
    return path


def commit(repo: Path) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "--allow-empty", "-m", "Fixture revision")
    return git(repo, "rev-parse", "HEAD")


def connector(repo: Path) -> GitRepoConnector:
    return GitRepoConnector(repo_path=repo, ref="refs/heads/main", fetch=False)


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("incremental Git attempted corpus-wide discovery")


@pytest.mark.parametrize("corpus_size", [100, 10_000, 100_000])
@pytest.mark.parametrize("changed", [False, True])
def test_git_delta_is_bounded_by_changes_not_stored_corpus(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus_size: int, changed: bool,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    if changed:
        (repo / "edit.md").write_text("new text\n", encoding="utf-8")
        target = commit(repo)
    else:
        target = base
    source = connector(repo)
    assert source.current_commit() == target
    commands: list[tuple[str, ...]] = []
    read_text, read_bytes = source._git, source._git_bytes

    def measured_text(args: list[str]) -> str:
        commands.append(tuple(args))
        return read_text(args)

    def measured_bytes(args: list[str]) -> bytes:
        commands.append(tuple(args))
        return read_bytes(args)

    monkeypatch.setattr(source, "_git", measured_text)
    monkeypatch.setattr(source, "_git_bytes", measured_bytes)
    for name in ("list_paths", "_tracked_paths", "_commit_shas", "discover", "discover_changed"):
        monkeypatch.setattr(source, name, forbidden)
    paths = PathIndex(tmp_path / "paths.db")
    paths.mark_indexed_batch([
        (source.source_name, f"old-{index}.md", "same", 1) for index in range(corpus_size)
    ])
    monkeypatch.setattr(paths, "get_paths_by_family", forbidden)
    state = IndexState(sources={source.source_name: SourceState(last_commit=base)})
    state.sources[source.source_name].acquisition_scope = recovery_scope(source.source_name, source)
    manifest = collect_source_changes(
        source.source_name, state=state, path_index=paths,
        full=False, source_aware=False, connector=source,
    )
    assert manifest.commit == target
    assert manifest.deletions == []
    assert manifest.stats.files_scanned == (2 if changed else 0)
    assert len(commands) == (5 if changed else 0)
    assert [item.path for item in manifest.upserts if item.language != "commit"] == (
        ["edit.md"] if changed else []
    )
    assert state.sources[source.source_name].last_commit == base


@pytest.mark.parametrize("destination", [
    "renamed.md",
    pytest.param(
        "tab\tand\nnewline.md",
        marks=pytest.mark.skipif(os.name == "nt", reason="Win32 forbids control-character filenames"),
    ),
])
def test_git_delta_renames_deletes_binary_transitions_and_unusual_paths(
    repo: Path, destination: str,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "remove.md").unlink()
    (repo / "rename.md").rename(repo / destination)
    (repo / "binary.md").write_bytes(b"new\0binary")
    (repo / "add.py").write_text("print('new')\n", encoding="utf-8")
    target = commit(repo)
    source = connector(repo)
    delta = source.discover_delta(base)
    assert delta.commit == target
    assert {item.path for item in delta.deletions} == {"remove.md", "rename.md", "binary.md"}
    files = {item.path: item for item in delta.upserts if item.language != "commit"}
    assert set(files) == {destination, "add.py"}
    assert files[destination].content == "rename.md\n"
    assert all(item.metadata["commit"] == target for item in files.values())
    commits = [item.path for item in delta.upserts if item.language == "commit"]
    assert commits == [f"commits/{target}.txt"]


@pytest.mark.parametrize("suffix", [b".png", b".md"])
def test_non_utf8_git_path_does_not_block_other_items_or_invent_a_stored_identity(
    repo: Path, suffix: bytes, caplog: pytest.LogCaptureFixture,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("normal edit\n", encoding="utf-8")
    middle = commit(repo)
    executable = shutil.which("git") or "git"

    def binary_git(args: list[str], data: bytes | None = None) -> bytes:
        return subprocess.run(
            [executable, *args], cwd=repo, input=data, capture_output=True, check=True,
            **no_window_kwargs(),
        ).stdout

    raw_tree = binary_git(["ls-tree", "-z", middle])
    blob = git(repo, "rev-parse", f"{middle}:keep.md").encode()
    raw_tree += b"100644 blob " + blob + b"\tasset-\xff" + suffix + b"\0"
    tree = binary_git(["mktree", "-z"], raw_tree).decode().strip()
    target = binary_git(
        ["commit-tree", tree, "-p", middle], b"Add unsupported filename\n",
    ).decode().strip()
    git(repo, "update-ref", "refs/heads/main", target)
    source = connector(repo)
    delta = source.discover_delta(base)
    assert delta.commit == target
    assert delta.deletions == []
    assert [item.path for item in delta.upserts if item.language != "commit"] == ["edit.md"]
    assert {item.path for item in delta.upserts if item.language == "commit"} == {
        f"commits/{middle}.txt", f"commits/{target}.txt",
    }
    assert "unsupported non-UTF-8 Git path" in caplog.text
    assert all("asset-" not in item.path for item in source.discover())
    assert all("asset-" not in path for paths in source.list_paths().values() for path in paths)


def test_git_commit_message_delimiters_are_content_not_record_boundaries(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    body = "Body with field \x1f and record \x1e separators.\n\nMore text."
    git(repo, "commit", "--allow-empty", "-m", "Delimited subject", "-m", body)
    target = git(repo, "rev-parse", "HEAD")
    source = connector(repo)
    delta = source.discover_delta(base)
    assert delta.commit == target
    assert len(delta.upserts) == 1
    assert delta.upserts[0].path == f"commits/{target}.txt"
    assert body in delta.upserts[0].content
    assert body in next(item for item in source.discover() if item.path == f"commits/{target}.txt").content


def test_git_delta_pins_files_history_and_cursor_even_when_ref_moves(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("pinned\n", encoding="utf-8")
    target = commit(repo)
    source = connector(repo)
    read = source._git_bytes
    newer: list[str] = []

    def move_ref(args: list[str]) -> bytes:
        result = read(args)
        if args[0] == "diff":
            (repo / "edit.md").write_text("too new\n", encoding="utf-8")
            newer.append(commit(repo))
        return result

    monkeypatch.setattr(source, "_git_bytes", move_ref)
    delta = source.discover_delta(base)
    assert newer and newer[0] != target
    assert delta.commit == target
    assert next(item for item in delta.upserts if item.path == "edit.md").content == "pinned\n"
    assert [item.path for item in delta.upserts if item.language == "commit"] == [
        f"commits/{target}.txt",
    ]


@pytest.mark.parametrize("base", [None, "invalid", "f" * 40])
def test_git_missing_or_expired_cursor_requests_explicit_repair(repo: Path, base: str | None) -> None:
    with pytest.raises(GitReconciliationRequired, match="explicit full reindex"):
        connector(repo).discover_delta(base)


def test_git_rewritten_history_requests_explicit_repair(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "--orphan", "replacement")
    (repo / "edit.md").write_text("replacement root\n", encoding="utf-8")
    target = commit(repo)
    source = GitRepoConnector(repo_path=repo, ref="refs/heads/replacement", fetch=False)
    assert source.current_commit() == target
    with pytest.raises(GitReconciliationRequired, match="history was rewritten"):
        source.discover_delta(base)


def test_git_delta_blob_failure_never_authorizes_a_checkpoint(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_text("changed\n", encoding="utf-8")
    commit(repo)
    source = connector(repo)
    read = source._git_bytes

    def fail_blob(args: list[str]) -> bytes:
        if args[0] == "cat-file":
            raise subprocess.CalledProcessError(128, ["git", *args])
        return read(args)

    monkeypatch.setattr(source, "_git_bytes", fail_blob)
    state = IndexState(sources={source.source_name: SourceState(last_commit=base)})
    state.sources[source.source_name].acquisition_scope = recovery_scope(source.source_name, source)
    with pytest.raises(subprocess.CalledProcessError):
        collect_source_changes(
            source.source_name, state=state, path_index=PathIndex(tmp_path / "paths.db"),
            full=False, source_aware=False, connector=source,
        )
    assert state.sources[source.source_name].last_commit == base


def test_git_delta_oversized_blob_is_retired_without_reading_content(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    (repo / "edit.md").write_bytes(b"x" * 1_000_001)
    commit(repo)
    source = connector(repo)
    read = source._git_bytes

    def reject_blob_read(args: list[str]) -> bytes:
        assert args[0] != "cat-file", "oversized content must not be loaded"
        return read(args)

    monkeypatch.setattr(source, "_git_bytes", reject_blob_read)
    delta = source.discover_delta(base)
    assert [item.path for item in delta.deletions] == ["edit.md"]


def test_git_delta_cancellation_happens_before_range_io(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo)
    source = connector(repo)
    source.current_commit()
    monkeypatch.setattr(source, "_git", forbidden)
    monkeypatch.setattr(source, "_git_bytes", forbidden)

    def cancel() -> None:
        raise InterruptedError("fixture cancellation")

    with pytest.raises(InterruptedError, match="cancellation"):
        source.discover_delta(base, cancel_check=cancel)


@pytest.mark.parametrize("raw", [b"truncated", b":100644 100644 invalid invalid M\0edit.md\0"])
def test_git_delta_rejects_incomplete_or_invalid_diff(
    repo: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes,
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo)
    source = connector(repo)
    monkeypatch.setattr(source, "_git_bytes", lambda args: raw)
    with pytest.raises(ValueError, match="Git delta"):
        source.discover_delta(base)


def test_worktree_keeps_legacy_reconciliation_and_full_bootstrap_remains_available(
    repo: Path, tmp_path: Path,
) -> None:
    source = GitRepoConnector(repo_path=repo, fetch=False)
    assert source.delta_available is False
    manifest = collect_source_changes(
        source.source_name, state=IndexState(), path_index=PathIndex(tmp_path / "legacy.db"),
        full=False, source_aware=False, connector=source,
    )
    assert manifest.upserts and manifest.commit
    pinned = connector(repo)
    assert pinned.delta_available
    manifest = collect_source_changes(
        pinned.source_name, state=IndexState(), path_index=PathIndex(tmp_path / "full.db"),
        full=True, source_aware=False, connector=pinned,
    )
    assert manifest.upserts and manifest.commit
