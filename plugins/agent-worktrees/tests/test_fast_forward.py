"""Tests for git_ops.fast_forward_worktree and can_fast_forward.

These exercise the FF-only update path against real temporary git repos so
the safety guards (clean / ahead / diverged / dirty) are verified end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_worktrees import git_ops
from agent_worktrees.git_ops import (
    FastForwardResult,
    WorktreeState,
    WorktreeStateInfo,
    can_fast_forward,
    fast_forward_worktree,
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"add {name}", cwd=repo)


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """Create an origin (bare) plus a clone that tracks origin/master.

    Returns (origin_workdir, clone). ``origin_workdir`` is a normal checkout
    used to push new commits to origin so the clone falls behind.
    """
    origin_bare = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "master", str(origin_bare), cwd=tmp_path)

    seed = tmp_path / "seed"
    _git("clone", str(origin_bare), str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=seed)
    _git("config", "user.name", "Test", cwd=seed)
    _commit(seed, "base.txt", "v1")
    _git("push", "origin", "master", cwd=seed)

    clone = tmp_path / "clone"
    _git("clone", str(origin_bare), str(clone), cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=clone)
    _git("config", "user.name", "Test", cwd=clone)

    return seed, clone


def _advance_origin(seed: Path, name: str = "next.txt") -> None:
    """Add a commit on origin/master via the seed checkout."""
    _commit(seed, name, "more")
    _git("push", "origin", "master", cwd=seed)


class TestFastForwardWorktree:
    def test_clean_behind_is_fast_forwarded(self, repos: tuple[Path, Path]):
        seed, clone = repos
        _advance_origin(seed)

        result = fast_forward_worktree(
            clone, remote="origin", default_branch="master", do_fetch=True,
        )
        assert isinstance(result, FastForwardResult)
        assert result.updated is True
        assert result.reason == "updated"
        assert result.behind == 1
        # The new file from origin is now present in the worktree.
        assert (clone / "next.txt").exists()

    def test_up_to_date_is_noop(self, repos: tuple[Path, Path]):
        _seed, clone = repos
        result = fast_forward_worktree(
            clone, remote="origin", default_branch="master", do_fetch=True,
        )
        assert result.updated is False
        assert result.reason == "up-to-date"

    def test_ahead_is_skipped(self, repos: tuple[Path, Path]):
        _seed, clone = repos
        _commit(clone, "local.txt", "local work")  # local commit, not pushed

        result = fast_forward_worktree(
            clone, remote="origin", default_branch="master", do_fetch=True,
        )
        assert result.updated is False
        assert result.reason == "ahead"
        assert result.ahead == 1

    def test_diverged_is_skipped(self, repos: tuple[Path, Path]):
        seed, clone = repos
        _advance_origin(seed)               # origin gains a commit
        _commit(clone, "local.txt", "local")  # clone gains a different commit

        result = fast_forward_worktree(
            clone, remote="origin", default_branch="master", do_fetch=True,
        )
        assert result.updated is False
        assert result.reason == "diverged"
        assert result.ahead == 1
        assert result.behind == 1

    def test_dirty_is_skipped(self, repos: tuple[Path, Path]):
        seed, clone = repos
        _advance_origin(seed)
        (clone / "base.txt").write_text("uncommitted change")  # dirty tree

        result = fast_forward_worktree(
            clone, remote="origin", default_branch="master", do_fetch=True,
        )
        assert result.updated is False
        assert result.reason == "dirty"

    def test_missing_worktree_is_gone(self, tmp_path: Path):
        result = fast_forward_worktree(
            tmp_path / "does-not-exist",
            remote="origin", default_branch="master", do_fetch=False,
        )
        assert result.updated is False
        assert result.reason == "gone"

    def test_no_fetch_still_fast_forwards_known_ref(self, repos: tuple[Path, Path]):
        """With do_fetch=False, FF uses the already-fetched origin ref."""
        seed, clone = repos
        _advance_origin(seed)
        git_ops.fetch("origin", cwd=clone)  # explicit fetch refreshes origin/master

        result = fast_forward_worktree(
            clone, remote="origin", default_branch="master", do_fetch=False,
        )
        assert result.updated is True
        assert result.behind == 1


class TestCanFastForward:
    def test_clean_behind_eligible(self):
        info = WorktreeStateInfo(state=WorktreeState.UNUSED, ahead=0, behind=3, dirty=0)
        assert can_fast_forward(info) is True

    def test_aligned_not_eligible(self):
        info = WorktreeStateInfo(state=WorktreeState.UNUSED, ahead=0, behind=0, dirty=0)
        assert can_fast_forward(info) is False

    def test_ahead_not_eligible(self):
        info = WorktreeStateInfo(state=WorktreeState.WIP, ahead=2, behind=1, dirty=0)
        assert can_fast_forward(info) is False

    def test_dirty_not_eligible(self):
        info = WorktreeStateInfo(state=WorktreeState.DIRTY, ahead=0, behind=2, dirty=4)
        assert can_fast_forward(info) is False
