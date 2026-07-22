"""Tests for anchor_hygiene -- dirty/stash state and behind-origin staleness."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_worktrees import anchor_hygiene


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=path)
    _git("config", "user.email", "t@t", cwd=path)
    _git("config", "user.name", "t", cwd=path)


def _make_origin_and_clone(tmp: Path) -> Path:
    """Create a bare origin with one commit and return a clone tracking it."""
    origin = tmp / "origin"
    origin.mkdir()
    _git("init", "--bare", "-b", "main", cwd=origin)

    seed = tmp / "seed"
    _init_repo(seed)
    (seed / "a.txt").write_text("1")
    _git("add", "-A", cwd=seed)
    _git("commit", "-m", "c1", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    _git("push", "-u", "origin", "main", cwd=seed)

    work = tmp / "work"
    _git("clone", str(origin), str(work), cwd=tmp)
    _git("config", "user.email", "t@t", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    return work


def _advance_origin(tmp: Path, n: int) -> None:
    """Push *n* new commits to origin/main via a throwaway clone."""
    other = tmp / "other"
    _git("clone", str(tmp / "origin"), str(other), cwd=tmp)
    _git("config", "user.email", "t@t", cwd=other)
    _git("config", "user.name", "t", cwd=other)
    for i in range(n):
        (other / f"b{i}.txt").write_text(str(i))
        _git("add", "-A", cwd=other)
        _git("commit", "-m", f"x{i}", cwd=other)
    _git("push", "origin", "main", cwd=other)


def test_clean_current_repo_not_behind(tmp_path: Path) -> None:
    work = _make_origin_and_clone(tmp_path)
    report = anchor_hygiene.check_anchor(str(work), fetch=True)
    assert report.is_clean
    assert not report.is_behind
    assert report.behind_count == 0
    assert report.branch == "main"
    assert report.tracking == "origin/main"


def test_behind_count_requires_fetch(tmp_path: Path) -> None:
    work = _make_origin_and_clone(tmp_path)
    _advance_origin(tmp_path, 3)

    # Without fetch the local refs are stale -> behind reads 0.
    stale = anchor_hygiene.check_anchor(str(work), fetch=False)
    assert stale.behind_count == 0

    # With fetch the count is accurate; a clean-but-behind anchor is the case
    # that silently stales picker/status config.
    fresh = anchor_hygiene.check_anchor(str(work), fetch=True)
    assert fresh.behind_count == 3
    assert fresh.is_behind
    assert fresh.is_clean  # behind is independent of local cleanliness


def test_report_json_shape(tmp_path: Path) -> None:
    work = _make_origin_and_clone(tmp_path)
    _advance_origin(tmp_path, 1)
    report = anchor_hygiene.check_anchor(str(work), fetch=True)
    data = anchor_hygiene.report_as_json(report)
    for key in ("behind_count", "is_behind", "branch", "tracking"):
        assert key in data
    assert data["behind_count"] == 1
    assert data["is_behind"] is True


def test_no_upstream_is_graceful(tmp_path: Path) -> None:
    # A repo with no upstream (no remote tracking) must not error.
    solo = tmp_path / "solo"
    _init_repo(solo)
    (solo / "a.txt").write_text("1")
    _git("add", "-A", cwd=solo)
    _git("commit", "-m", "c1", cwd=solo)
    report = anchor_hygiene.check_anchor(str(solo), fetch=True)
    assert report.behind_count == 0
    assert not report.is_behind
    assert report.tracking == ""
