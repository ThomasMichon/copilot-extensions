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


# ---------------------------------------------------------------------------
# classify_managed_worktree (system/bridge leak GC eligibility)
# ---------------------------------------------------------------------------

def _managed(**over):
    """A managed-worktree fact set that is eligible for reap by default."""
    base = dict(
        worktree_id="wt-1", kind="bridge", follow_up=False,
        status="finalized", git_state="completed", has_live_mux=False,
        attached=False, has_live_session=False, idle_secs=7200.0,
    )
    base.update(over)
    return gc.classify_managed_worktree(**base)


def test_managed_final_idle_dead_is_removed():
    assert _managed().action == "remove"
    assert _managed().reason == "final"


def test_managed_unused_is_removed():
    v = _managed(status="active", git_state="unused")
    assert v.action == "remove"
    assert v.reason == "unused"


def test_managed_gone_dir_counts_as_final():
    v = _managed(status="active", git_state="gone")
    assert v.action == "remove" and v.reason == "final"


def test_managed_non_managed_kind_skipped():
    assert _managed(kind="session").reason == "not-managed"


def test_managed_follow_up_is_spared():
    assert _managed(follow_up=True).reason == "follow-up"


def test_managed_attached_is_spared():
    assert _managed(attached=True).reason == "attached"


def test_managed_live_mux_is_spared():
    assert _managed(has_live_mux=True).reason == "live-mux"


def test_managed_live_session_is_spared():
    assert _managed(has_live_session=True).reason == "live-session"


def test_managed_dirty_or_wip_is_spared():
    # A managed worktree still doing real work (dirty/wip) is never final/unused.
    assert _managed(status="active", git_state="dirty").reason == "not-final-or-unused"
    assert _managed(status="active", git_state="wip").reason == "not-final-or-unused"


def test_managed_fresh_within_grace_is_spared():
    assert _managed(idle_secs=60.0).action == "skip"
    assert _managed(idle_secs=60.0).reason == "idle-grace"


def test_managed_unknown_activity_is_spared():
    # Never risk reaping something we can't prove is idle.
    assert _managed(idle_secs=None).reason == "activity-unknown"
