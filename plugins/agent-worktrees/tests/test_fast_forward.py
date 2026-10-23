"""Tests for git_ops.fast_forward_worktree and can_fast_forward.

These exercise the FF-only update path against real temporary git repos so
the safety guards (clean / ahead / diverged / dirty) are verified end to end.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import _published_seed_environment, _seed_directory

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


def _build_repos(tmp_path: Path) -> None:
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


@pytest.fixture(scope="session")
def _fast_forward_seed(tmp_path_factory) -> Path:
    with _seed_directory(tmp_path_factory, "fast-forward") as (root, build):
        if build:
            with _published_seed_environment(tmp_path_factory):
                _build_repos(root)
        return root


def _copy_repos(source: Path, root: Path) -> tuple[Path, Path]:
    shutil.copytree(source, root, dirs_exist_ok=True, copy_function=shutil.copyfile)
    seed, clone = root / "seed", root / "clone"
    for repo in (seed, clone):
        _git("remote", "set-url", "origin", str(root / "origin.git"), cwd=repo)
    return seed, clone


@pytest.fixture
def repos(tmp_path: Path, _fast_forward_seed: Path) -> tuple[Path, Path]:
    """Private origin, publisher and aligned clone; later mutations stay per case."""
    return _copy_repos(_fast_forward_seed, tmp_path)


def test_repo_copies_preserve_bytes_and_isolate_mutations(tmp_path, _fast_forward_seed):
    def snapshot(root):
        return {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        }

    source = _fast_forward_seed
    source_before = snapshot(source)
    first, second = tmp_path / "first", tmp_path / "second"
    seed, clone = _copy_repos(source, first)
    _copy_repos(source, second)
    second_before = snapshot(second)
    for root in (first, second):
        for relative, data in source_before.items():
            copied = root / relative
            if relative not in (
                Path("seed") / ".git" / "config", Path("clone") / ".git" / "config",
            ):
                assert copied.read_bytes() == data
            assert not copied.samefile(source / relative)
            assert not copied.samefile((second if root == first else first) / relative)
        for name in ("seed", "clone"):
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"], cwd=root / name,
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            assert remote == str(root / "origin.git")
        for git_dir in (root / "origin.git", root / "seed" / ".git", root / "clone" / ".git"):
            assert not (git_dir / "objects" / "info" / "alternates").exists()

    _advance_origin(seed, "publisher-only.txt")
    _git("fetch", "origin", cwd=clone)
    _commit(clone, "clone-only.txt", "private clone mutation")
    object_file = next((clone / ".git" / "objects").glob("??/*"))
    object_file.chmod(stat.S_IWRITE | stat.S_IREAD)
    object_file.unlink()
    assert snapshot(source) == source_before
    assert snapshot(second) == second_before


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


def test_fetch_failure_is_reported_not_swallowed(repos: tuple[Path, Path]):
    _seed, clone = repos
    _git("remote", "set-url", "origin", str(clone.parent / "missing-origin.git"), cwd=clone)

    result = git_ops.fast_forward_worktree(
        clone, remote="origin", default_branch="master", do_fetch=True,
    )

    assert result.reason == "up-to-date"
    assert result.fetch_error


def test_successful_fetch_has_no_fetch_error(repos: tuple[Path, Path]):
    seed, clone = repos
    _advance_origin(seed)

    result = git_ops.fast_forward_worktree(
        clone, remote="origin", default_branch="master", do_fetch=True,
    )

    assert result.updated is True
    assert result.fetch_error is None