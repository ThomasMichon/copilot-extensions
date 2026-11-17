"""Configured-base preparation against fresh, local Git repositories."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_worktrees import git_ops, inrepo_config_source, worktree_creation
from agent_worktrees.git_repository_environment import repository_identity_scope


def _git(*args: str, cwd: Path) -> str:
    result = git_ops.git(
        *args, cwd=cwd, isolated_repository=True, no_hooks=True, timeout=10,
    )
    assert isinstance(result.stdout, str)
    return result.stdout.strip()


def _commit(repo: Path, name: str, content: str | None = None) -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(name if content is None else content, encoding="utf-8")
    _git("add", name, cwd=repo)
    _git("commit", "-m", f"Add {name}", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


@pytest.fixture
def local_repositories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path]]:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    templates = tmp_path / "empty-templates"
    templates.mkdir()
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(templates))
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


@pytest.mark.parametrize(
    ("fetch_refspec", "missing_tracking_base", "anchor_branch"),
    [
        ("+refs/heads/side:refs/remotes/origin/side", False, "trunk"),
        ("+refs/heads/side:refs/remotes/origin/side", True, "trunk"),
        ("+refs/heads/trunk:refs/remotes/origin/side", False, "trunk"),
        ("+refs/heads/*:refs/heads/*", False, "feature"),
    ],
    ids=["narrow-stale", "narrow-missing", "alternate-tracking", "mirror-local-head"],
)
def test_preparation_refreshes_only_the_configured_base(
    local_repositories: tuple[Path, Path], fetch_refspec: str,
    missing_tracking_base: bool, anchor_branch: str,
) -> None:
    seed, anchor = local_repositories
    old_base = _git("rev-parse", "origin/trunk", cwd=anchor)
    old_side = _git("rev-parse", "origin/side", cwd=anchor)
    _git("config", "--replace-all", "remote.origin.fetch", fetch_refspec, cwd=anchor)
    if anchor_branch != "trunk":
        _git("checkout", "-b", anchor_branch, cwd=anchor)
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
    if anchor_branch == "trunk":
        assert prepared.anchor.updated is True
        assert _git("rev-parse", "HEAD", cwd=anchor) == new_base
    else:
        assert prepared.anchor.reason == "non-default-branch"
        assert _git("rev-parse", "HEAD", cwd=anchor) == old_base
        assert _git("rev-parse", "trunk", cwd=anchor) == old_base


@pytest.mark.parametrize("transition", ["new-ref", "stale-ref", "deleted-base", "non-origin"])
def test_preparation_preserves_committed_policy_branch_discovery(
    local_repositories: tuple[Path, Path], transition: str,
) -> None:
    seed, anchor = local_repositories
    policy_path = ".agent-worktrees/config.yaml"
    _commit(seed, policy_path, "default_branch: dev\n")
    _git("branch", "dev", cwd=seed)
    _git("push", "origin", "trunk", "dev", cwd=seed)
    _git(
        "fetch", "origin",
        "+refs/heads/trunk:refs/remotes/origin/trunk",
        "+refs/heads/dev:refs/remotes/origin/dev", cwd=anchor,
    )
    candidates = (Path(policy_path),)
    initial = inrepo_config_source.load_inrepo_config_from_committed_ref(anchor, candidates)
    assert initial["default_branch"] == "dev"
    if transition == "stale-ref":
        _git("branch", "stable", cwd=seed)
        _git("push", "origin", "stable", cwd=seed)
        _git(
            "fetch", "origin", "+refs/heads/stable:refs/remotes/origin/stable", cwd=anchor,
        )
    old_dev = _git("rev-parse", "origin/dev", cwd=anchor)
    old_side = _git("rev-parse", "origin/side", cwd=anchor)
    if transition == "stale-ref":
        _git("checkout", "stable", cwd=seed)
    else:
        _git("checkout", "-b", "stable", cwd=seed)
    new_base = _commit(seed, policy_path, "default_branch: stable\n")
    _git("checkout", "trunk", cwd=seed)
    _commit(seed, policy_path, "default_branch: stable\n")
    _git("push", "origin", "trunk", "stable", cwd=seed)
    if transition == "deleted-base":
        _git("push", "origin", "--delete", "dev", cwd=seed)
    remote = "origin"
    if transition == "non-origin":
        remote = "upstream"
        origin_url = _git("remote", "get-url", "origin", cwd=anchor)
        _git("remote", "remove", "origin", cwd=anchor)
        _git("update-ref", "refs/remotes/origin/side", old_side, cwd=anchor)
        _git("remote", "add", "upstream", origin_url, cwd=anchor)
        _git(
            "config", "--replace-all", "remote.upstream.fetch",
            "+refs/heads/*:refs/remotes/origin/*", cwd=anchor,
        )
        _git(
            "fetch", "upstream",
            "+refs/heads/trunk:refs/remotes/origin/trunk",
            "+refs/heads/dev:refs/remotes/origin/dev", cwd=anchor,
        )
        _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk", cwd=anchor)
        _git("checkout", "trunk", cwd=seed)
        _commit(seed, policy_path, "default_branch: stable\nmarker: refreshed\n")
        _git("push", "origin", "trunk", cwd=seed)

    prepared = git_ops.prepare_worktree_base(
        anchor, remote=remote, default_branch="dev", fast_forward_anchor=False,
    )

    assert prepared.fetched is (transition != "deleted-base")
    assert (prepared.fetch_error is not None) is (transition == "deleted-base")
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == old_dev
    discovered = inrepo_config_source.load_inrepo_config_from_committed_ref(anchor, candidates)
    assert discovered["default_branch"] == "stable"
    if transition == "non-origin":
        assert "marker: refreshed" in _git(
            "show", f"origin/trunk:{policy_path}", cwd=anchor,
        )
    following = git_ops.prepare_worktree_base(
        anchor, remote=remote, default_branch=discovered["default_branch"],
        fast_forward_anchor=False,
    )
    assert following.fetched is True
    assert following.start_point == f"{remote}/stable"
    assert _git("rev-parse", following.start_point, cwd=anchor) == new_base
    assert _git("rev-parse", "origin/side", cwd=anchor) == old_side


@pytest.mark.parametrize("policy_head", ["direct", "deleted"])
def test_preparation_refreshes_base_despite_unavailable_policy_head(
    local_repositories: tuple[Path, Path], policy_head: str,
) -> None:
    seed, anchor = local_repositories
    if policy_head == "direct":
        _git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD", cwd=anchor)
        _git("update-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk", cwd=anchor)
    else:
        _git("branch", "retired-policy", cwd=seed)
        _git("push", "origin", "retired-policy", cwd=seed)
        _git(
            "fetch", "origin",
            "+refs/heads/retired-policy:refs/remotes/origin/retired-policy", cwd=anchor,
        )
        _git(
            "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/retired-policy", cwd=anchor,
        )
        _git("push", "origin", "--delete", "retired-policy", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
    )

    assert prepared.start_point == "origin/trunk"
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert prepared.fetched is (policy_head == "direct")
    assert (prepared.fetch_error is not None) is (policy_head == "deleted")


def test_preparation_caller_reports_literal_offline_failure_and_last_known_base(
    local_repositories: tuple[Path, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    seed, anchor = local_repositories
    last_known_base = _git("rev-parse", "origin/trunk", cwd=anchor)
    new_base = _commit(seed, "unfetched.txt")
    _git("push", "origin", "trunk", cwd=seed)
    assert new_base != last_known_base
    missing = anchor.parent / "missing.git"
    _git("remote", "set-url", "origin", str(missing), cwd=anchor)

    prepared = worktree_creation._prepare_worktree_source(
        anchor, remote="origin", default_branch="trunk",
        fast_forward_anchor=True, label="fixture",
    )

    assert prepared.fetched is False
    assert prepared.fetch_error is not None
    assert missing.name in prepared.fetch_error
    assert prepared.start_point == "origin/trunk"
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == last_known_base
    assert prepared.anchor.reason == "up-to-date"
    assert _git("rev-parse", "HEAD", cwd=anchor) == last_known_base
    assert (
        "Note: fetch failed for fixture; using last-known "
        f"'origin/trunk' ({prepared.fetch_error})."
    ) in capsys.readouterr().err
