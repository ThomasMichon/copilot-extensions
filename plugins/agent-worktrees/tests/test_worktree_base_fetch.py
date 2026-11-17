"""Configured-base preparation against fresh, local Git repositories."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_worktrees import git_ops, worktree_creation
from agent_worktrees.git_repository_environment import repository_identity_scope


def _git(*args: str, cwd: Path) -> str:
    result = git_ops.git(
        *args, cwd=cwd, isolated_repository=True, no_hooks=True, timeout=10,
    )
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git("add", name, cwd=repo)
    _git("commit", "-m", f"Add {name}", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def local_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path]]:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    with repository_identity_scope():
        origin = tmp_path / "origin.git"
        _git("init", "--bare", "-b", "trunk", str(origin), cwd=tmp_path)
        seed = tmp_path / "seed"
        _git("clone", str(origin), str(seed), cwd=tmp_path)
        _git("config", "user.email", "test@example.com", cwd=seed)
        _git("config", "user.name", "Test", cwd=seed)
        _git("config", "commit.gpgsign", "false", cwd=seed)
        _commit(seed, "base.txt")
        _git("branch", "side", cwd=seed)
        _git("push", "origin", "trunk", "side", cwd=seed)
        anchor = tmp_path / "anchor"
        _git("clone", str(origin), str(anchor), cwd=tmp_path)
        _git(
            "config", "--replace-all", "remote.origin.fetch",
            "+refs/heads/side:refs/remotes/origin/side", cwd=anchor,
        )
        yield seed, anchor


@pytest.mark.parametrize("missing_tracking_base", [False, True])
def test_preparation_refreshes_only_the_configured_base(
    local_repositories: tuple[Path, Path], missing_tracking_base: bool,
) -> None:
    seed, anchor = local_repositories
    old_base = _git("rev-parse", "origin/trunk", cwd=anchor)
    old_side = _git("rev-parse", "origin/side", cwd=anchor)
    if missing_tracking_base:
        _git("update-ref", "-d", "refs/remotes/origin/trunk", cwd=anchor)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    _git("checkout", "side", cwd=seed)
    new_side = _commit(seed, "new-side.txt")
    _git("push", "origin", "side", cwd=seed)
    assert new_base != old_base
    assert new_side != old_side

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk",
    )

    assert prepared.fetched is True
    assert prepared.fetch_error is None
    assert prepared.start_point == "origin/trunk"
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert _git("rev-parse", "origin/side", cwd=anchor) == old_side
    assert prepared.anchor.updated is True
    assert _git("rev-parse", "HEAD", cwd=anchor) == new_base


def test_preparation_caller_reports_literal_offline_failure_and_last_known_base(
    local_repositories: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    seed, anchor = local_repositories
    last_known_base = _git("rev-parse", "origin/trunk", cwd=anchor)
    new_base = _commit(seed, "unfetched.txt")
    _git("push", "origin", "trunk", cwd=seed)
    assert new_base != last_known_base
    _git(
        "remote", "set-url", "origin", str(anchor.parent / "missing.git"),
        cwd=anchor,
    )

    prepared = worktree_creation._prepare_worktree_source(
        anchor, remote="origin", default_branch="trunk",
        fast_forward_anchor=True, label="fixture",
    )

    assert prepared.fetched is False
    assert prepared.fetch_error is not None
    assert "does not appear to be a git repository" in prepared.fetch_error
    assert prepared.start_point == "origin/trunk"
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == last_known_base
    assert prepared.anchor.reason == "up-to-date"
    assert _git("rev-parse", "HEAD", cwd=anchor) == last_known_base
    assert (
        "Note: fetch failed for fixture; using last-known "
        f"'origin/trunk' ({prepared.fetch_error})."
    ) in capsys.readouterr().err
