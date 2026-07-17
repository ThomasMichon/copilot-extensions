"""Unit tests for the worktree garbage-collector's orphan-directory sweep."""
from __future__ import annotations

import types
from pathlib import Path

import agent_worktrees.gc as gc


def _repo(root: Path, anchor: Path | None = None):
    anchor = anchor or (root.parent / "anchor")
    return types.SimpleNamespace(anchor=str(anchor), worktree_root=str(root))


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_find_orphans_excludes_registered_and_tracked(tmp_path):
    root = _mkdir(tmp_path / "roots")
    _mkdir(root / "orphan")
    registered = _mkdir(root / "registered")
    tracked = _mkdir(root / "tracked")
    repo = _repo(root)

    orphans = gc.find_orphans(
        repo,
        registered_paths=[registered],
        tracked_paths=[str(tracked)],
    )
    names = {p.name for p in orphans}
    assert names == {"orphan"}  # registered + tracked excluded


def test_classify_empty_dir_is_removable(tmp_path):
    d = _mkdir(tmp_path / "empty")
    v = gc.classify_orphan(d, min_settle_secs=0)
    assert v.action == "remove"


def test_classify_cache_only_dir_is_effectively_empty(tmp_path):
    d = _mkdir(tmp_path / "cacheonly")
    cache = _mkdir(d / ".pytest_cache" / "v")
    (cache / "lastfailed").write_text("{}")
    (d / "__pycache__").mkdir()
    v = gc.classify_orphan(d, min_settle_secs=0)
    assert v.action == "remove"  # only cache files -> effectively empty


def test_classify_dir_with_real_files_is_skipped(tmp_path):
    d = _mkdir(tmp_path / "hasfiles")
    (d / "tools").mkdir()
    (d / "tools" / "keep.py").write_text("print('x')")
    v = gc.classify_orphan(d, min_settle_secs=0)
    assert v.action == "skip"
    assert "non-empty" in v.reason


def test_classify_recent_dir_is_skipped(tmp_path):
    d = _mkdir(tmp_path / "recent")
    v = gc.classify_orphan(d, min_settle_secs=10_000)  # dir just created
    assert v.action == "skip"
    assert "recent" in v.reason


def test_sweep_removes_empty_skips_nonempty(tmp_path, monkeypatch):
    root = _mkdir(tmp_path / "roots")
    empty = _mkdir(root / "empty")
    nonempty = _mkdir(root / "nonempty")
    (nonempty / "data.txt").write_text("keep me")
    repo = _repo(root)

    monkeypatch.setattr(gc, "candidate_roots", lambda r: [root])
    import agent_worktrees.git_ops as git_ops
    monkeypatch.setattr(git_ops, "list_worktree_paths", lambda *, cwd: [])

    report = gc.sweep_orphans(repo, records=[], dry_run=False, min_settle_secs=0)
    removed = {Path(x["path"]).name for x in report["removed"]}
    skipped = {Path(x["path"]).name for x in report["skipped"]}
    assert removed == {"empty"}
    assert skipped == {"nonempty"}
    assert not empty.exists()       # actually removed from disk
    assert nonempty.exists()        # preserved


def test_sweep_dry_run_removes_nothing(tmp_path, monkeypatch):
    root = _mkdir(tmp_path / "roots")
    empty = _mkdir(root / "empty")
    repo = _repo(root)
    monkeypatch.setattr(gc, "candidate_roots", lambda r: [root])
    import agent_worktrees.git_ops as git_ops
    monkeypatch.setattr(git_ops, "list_worktree_paths", lambda *, cwd: [])

    report = gc.sweep_orphans(repo, records=[], dry_run=True, min_settle_secs=0)
    assert len(report["removed"]) == 1
    assert "would remove" in report["removed"][0]["reason"]
    assert empty.exists()           # dry run: still on disk


def test_sweep_is_idempotent(tmp_path, monkeypatch):
    root = _mkdir(tmp_path / "roots")
    _mkdir(root / "empty")
    repo = _repo(root)
    monkeypatch.setattr(gc, "candidate_roots", lambda r: [root])
    import agent_worktrees.git_ops as git_ops
    monkeypatch.setattr(git_ops, "list_worktree_paths", lambda *, cwd: [])

    first = gc.sweep_orphans(repo, records=[], dry_run=False, min_settle_secs=0)
    assert len(first["removed"]) == 1
    second = gc.sweep_orphans(repo, records=[], dry_run=False, min_settle_secs=0)
    assert second["scanned"] == 0
    assert second["removed"] == []


def test_locked_dir_is_skipped_not_crashed(tmp_path, monkeypatch):
    root = _mkdir(tmp_path / "roots")
    _mkdir(root / "locked")
    repo = _repo(root)
    monkeypatch.setattr(gc, "candidate_roots", lambda r: [root])
    import agent_worktrees.git_ops as git_ops
    monkeypatch.setattr(git_ops, "list_worktree_paths", lambda *, cwd: [])

    def _always_locked(d):
        raise PermissionError("held by another process")
    monkeypatch.setattr(gc.shutil, "rmtree", lambda *a, **k: _always_locked(a[0]))

    report = gc.sweep_orphans(repo, records=[], dry_run=False, min_settle_secs=0)
    assert report["removed"] == []
    assert len(report["skipped"]) == 1
    assert "locked" in report["skipped"][0]["reason"]
