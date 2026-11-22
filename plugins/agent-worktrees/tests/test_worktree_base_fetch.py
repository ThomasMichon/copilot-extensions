"""Configured-base preparation against fresh, local Git repositories."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import (
    git_ops, git_repository_environment, inrepo_config_source, worktree_creation,
)


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


def test_foreground_creation_fetch_waits_without_an_aggregate_source_deadline(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import git_acquisition_budget, tracking_write

    seed, anchor = local_repositories
    expected = _commit(seed, "foreground.txt")
    _git("push", "origin", "trunk", cwd=seed)
    fetch = git_ops._fetch_refspecs
    observed: list[tuple[float | None, float | None]] = []

    def observe(*args: Any, **kwargs: Any) -> None:
        observed.append((kwargs["timeout"], git_acquisition_budget.remaining_timeout(None)))
        fetch(*args, **kwargs)

    monkeypatch.setattr(git_ops, "_fetch_refspecs", observe)
    with tracking_write.foreground_completion():
        prepared = git_ops.prepare_worktree_base(anchor, remote="origin", default_branch="trunk")
    assert prepared.fetched and prepared.fetch_error is None
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == expected
    assert observed and all(item == (None, None) for item in observed)


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
    local_repositories: tuple[Path, Path], policy_head: str, capsys: pytest.CaptureFixture[str],
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

    prepared = worktree_creation._prepare_worktree_source(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
        label="fixture",
    )

    assert prepared.start_point == "origin/trunk"
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert prepared.fetched is True
    assert (prepared.fetch_error is not None) is (policy_head == "deleted")
    if policy_head == "deleted":
        warning = capsys.readouterr().err
        assert "policy refresh failed for fixture" in warning
        assert "using refreshed 'origin/trunk'" in warning
        assert "using last-known 'origin/trunk'" not in warning


@pytest.mark.parametrize(
    "mapping", [
        "exact", "wildcard", "short-source", "source-only", "ambiguous",
        "excluded-overlap", "base-collision",
    ],
)
def test_preparation_preserves_policy_head_alias_mapping(
    local_repositories: tuple[Path, Path], mapping: str,
) -> None:
    seed, anchor = local_repositories
    policy_path = ".agent-worktrees/config.yaml"
    _git("checkout", "-b", "production", cwd=seed)
    _commit(seed, policy_path, "default_branch: trunk\nmarker: old\n")
    _git("checkout", "-b", "stable", cwd=seed)
    _commit(seed, policy_path, "default_branch: trunk\nmarker: wrong\n")
    _git("push", "origin", "production", "stable", cwd=seed)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        "+refs/heads/production:refs/remotes/origin/stable", cwd=anchor,
    )
    _git(
        "fetch", "origin", "--refmap=",
        "+refs/heads/production:refs/remotes/origin/stable", cwd=anchor,
    )
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/stable", cwd=anchor)
    policy_ref = "origin/stable"
    if mapping == "wildcard":
        _git(
            "config", "--replace-all", "remote.origin.fetch",
            "+refs/heads/production*:refs/remotes/origin/stable*", cwd=anchor,
        )
    elif mapping == "short-source":
        _git(
            "config", "--replace-all", "remote.origin.fetch",
            "+production:refs/remotes/origin/stable", cwd=anchor,
        )
    elif mapping == "source-only":
        _git("config", "--add", "remote.origin.fetch", "refs/heads/side", cwd=anchor)
    elif mapping == "ambiguous":
        _git(
            "config", "--add", "remote.origin.fetch",
            "+refs/heads/stable:refs/remotes/origin/stable", cwd=anchor,
        )
    elif mapping == "excluded-overlap":
        _git(
            "config", "--add", "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*", cwd=anchor,
        )
        _git("config", "--add", "remote.origin.fetch", "^refs/heads/stable", cwd=anchor)
    elif mapping == "base-collision":
        _git(
            "config", "--replace-all", "remote.origin.fetch",
            "+refs/heads/production:refs/remotes/origin/trunk", cwd=anchor,
        )
        _git(
            "fetch", "origin", "--refmap=",
            "+refs/heads/production:refs/remotes/origin/trunk", cwd=anchor,
        )
        _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk", cwd=anchor)
        policy_ref = "origin/trunk"
    old_policy = _git("rev-parse", policy_ref, cwd=anchor)
    old_anchor = _git("rev-parse", "HEAD", cwd=anchor)
    _git("checkout", "production", cwd=seed)
    new_policy = _commit(seed, policy_path, "default_branch: trunk\nmarker: refreshed\n")
    _git("push", "origin", "production", cwd=seed)
    _git("checkout", "trunk", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk",
        fast_forward_anchor=mapping == "base-collision",
    )

    if mapping == "base-collision":
        assert _git("rev-parse", policy_ref, cwd=anchor) == old_policy
        assert _git("rev-parse", "HEAD", cwd=anchor) == old_anchor
        assert _git("rev-parse", prepared.start_point, cwd=anchor) == old_anchor
        assert prepared.fetched is False
        assert prepared.fetch_error is not None
        discovered = inrepo_config_source.load_inrepo_config_from_committed_ref(
            anchor, (Path(policy_path),),
        )
        assert discovered["marker"] == "old"
        return
    assert prepared.fetched is True
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    if mapping == "ambiguous":
        assert prepared.fetch_error is not None
        assert _git("rev-parse", "origin/stable", cwd=anchor) == old_policy
        return
    assert prepared.fetch_error is None
    assert _git("rev-parse", "origin/stable", cwd=anchor) == new_policy
    discovered = inrepo_config_source.load_inrepo_config_from_committed_ref(
        anchor, (Path(policy_path),),
    )
    assert discovered["marker"] == "refreshed"


@pytest.mark.parametrize("source_ref", ["trunk", "heads/trunk", "refs/heads/trunk"])
@pytest.mark.parametrize("excluded", [False, True])
def test_preparation_reuses_the_matching_base_for_policy(
    local_repositories: tuple[Path, Path], source_ref: str, excluded: bool,
) -> None:
    seed, anchor = local_repositories
    path = ".agent-worktrees/config.yaml"
    _commit(seed, path, "default_branch: dev\nmarker: old\n")
    _git("branch", "dev", cwd=seed)
    _git("push", "origin", "trunk", "dev", cwd=seed)
    _git(
        "fetch", "origin", "--refmap=",
        "+refs/heads/trunk:refs/remotes/origin/trunk",
        "+refs/heads/dev:refs/remotes/origin/dev", cwd=anchor,
    )
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        f"+{source_ref}:refs/remotes/origin/trunk", cwd=anchor,
    )
    if excluded:
        _git("config", "--add", "remote.origin.fetch", "^refs/heads/trunk", cwd=anchor)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    _git("checkout", "dev", cwd=seed)
    new_policy = _commit(seed, path, "default_branch: dev\nmarker: refreshed\n")
    _git("push", "origin", "dev", cwd=seed)

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
    )

    assert _git("rev-parse", "origin/dev", cwd=anchor) == new_policy
    assert prepared.fetched is True
    assert prepared.fetch_error is None
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base


@pytest.mark.parametrize("source_ref", ["production", "heads/production", "refs/heads/production"])
def test_preparation_respects_a_different_excluded_policy_source(
    local_repositories: tuple[Path, Path], source_ref: str,
) -> None:
    seed, anchor = local_repositories
    path = ".agent-worktrees/config.yaml"
    _git("checkout", "-b", "production", cwd=seed)
    old_policy = _commit(seed, path, "default_branch: trunk\nmarker: old\n")
    _git("push", "origin", "production", cwd=seed)
    _git(
        "fetch", "origin", "--refmap=",
        "+refs/heads/production:refs/remotes/origin/stable", cwd=anchor,
    )
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/stable", cwd=anchor)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        f"+{source_ref}:refs/remotes/origin/stable", cwd=anchor,
    )
    _git("config", "--add", "remote.origin.fetch", "^refs/heads/production", cwd=anchor)
    _commit(seed, path, "default_branch: trunk\nmarker: excluded\n")
    _git("push", "origin", "production", cwd=seed)
    _git("checkout", "trunk", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
    )

    assert _git("rev-parse", "origin/stable", cwd=anchor) == old_policy
    assert prepared.fetched is True
    assert prepared.fetch_error is not None
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base


def test_preparation_refreshes_the_cached_bootstrap_hop_after_head_fetch_failure(
    local_repositories: tuple[Path, Path],
) -> None:
    seed, anchor = local_repositories
    path = ".agent-worktrees/config.yaml"
    _git("checkout", "-b", "retired", cwd=seed)
    _commit(seed, path, "default_branch: dev\nmarker: old\n")
    _git("branch", "dev", cwd=seed)
    _git("push", "origin", "retired", "dev", cwd=seed)
    _git(
        "fetch", "origin", "--refmap=",
        "+refs/heads/retired:refs/remotes/origin/retired",
        "+refs/heads/dev:refs/remotes/origin/dev", cwd=anchor,
    )
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/retired", cwd=anchor)
    _git("push", "origin", "--delete", "retired", cwd=seed)
    _git("checkout", "dev", cwd=seed)
    new_policy = _commit(seed, path, "default_branch: dev\nmarker: refreshed\n")
    _git("push", "origin", "dev", cwd=seed)
    _git("checkout", "trunk", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
    )

    assert _git("rev-parse", "origin/dev", cwd=anchor) == new_policy
    assert prepared.fetched is True
    assert prepared.fetch_error is not None
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert inrepo_config_source.load_inrepo_config_from_committed_ref(
        anchor, (Path(path),),
    )["marker"] == "refreshed"


@pytest.mark.parametrize("selector", ["directory", "configuration"])
def test_preparation_scopes_the_entire_operation_to_its_anchor(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, selector: str,
) -> None:
    seed, anchor = local_repositories
    foreign = anchor.parent / "foreign"
    _git("clone", _git("remote", "get-url", "origin", cwd=anchor), str(foreign), cwd=anchor.parent)
    old_foreign = _git("rev-parse", "origin/trunk", cwd=foreign)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    if selector == "directory":
        monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
        monkeypatch.setenv("GIT_INDEX_FILE", str(foreign / ".git" / "index"))
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(foreign))

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk",
    )

    assert _git("rev-parse", "origin/trunk", cwd=anchor) == new_base
    assert (anchor / "new-base.txt").is_file()
    assert _git("rev-parse", "origin/trunk", cwd=foreign) == old_foreign
    assert _git("rev-parse", "HEAD", cwd=foreign) == old_foreign
    assert not (foreign / "new-base.txt").exists()
    assert prepared.fetched is True
    assert prepared.anchor.updated is True


def test_preparation_preserves_the_public_fetch_policy_reload_mapping(
    local_repositories: tuple[Path, Path],
) -> None:
    seed, anchor = local_repositories
    path = ".agent-worktrees/config.yaml"
    _commit(seed, path, "default_branch: trunk\nmarker: old\n")
    _git("push", "origin", "trunk", cwd=seed)
    _git(
        "fetch", "origin", "--refmap=",
        "+refs/heads/trunk:refs/remotes/origin/policy", cwd=anchor,
    )
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/policy", cwd=anchor)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        "+refs/heads/trunk:refs/remotes/origin/policy", cwd=anchor,
    )
    new_policy = _commit(seed, path, "default_branch: trunk\nmarker: refreshed\n")
    _git("push", "origin", "trunk", cwd=seed)

    git_ops.fetch("origin", cwd=anchor, branch="trunk")

    assert _git("rev-parse", "origin/policy", cwd=anchor) == new_policy
    assert inrepo_config_source.load_inrepo_config_from_committed_ref(
        anchor, (Path(path),),
    )["marker"] == "refreshed"


@pytest.mark.parametrize("probe", ["head", "bootstrap"])
def test_preparation_reports_unavailable_policy_probes(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, probe: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed, anchor = local_repositories
    new_base = _commit(seed, ".agent-worktrees/config.yaml", "default_branch: trunk\n")
    _git("push", "origin", "trunk", cwd=seed)
    original_git = git_ops.git

    def unavailable(
        *args: str, **kwargs: Any,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        head_probe = args[:2] == ("symbolic-ref", "-q") and "refs/remotes/origin/HEAD" in args
        if (probe == "head" and head_probe) or (
            probe == "bootstrap" and args[0] in {"cat-file", "show"}
        ):
            raise subprocess.TimeoutExpired(["git", *args], 0.1)
        return original_git(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", unavailable)
    prepared = worktree_creation._prepare_worktree_source(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
        label="fixture",
    )

    assert prepared.fetched is True
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert prepared.fetch_error is not None
    warning = capsys.readouterr().err
    assert "policy refresh failed for fixture" in warning
    assert "using refreshed 'origin/trunk'" in warning


def test_preparation_does_not_fetch_policy_after_auth_exhausts_deadline(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, anchor = local_repositories
    old_policy = _git("rev-parse", "origin/trunk", cwd=anchor)
    origin_url = _git("remote", "get-url", "origin", cwd=anchor)
    _git("remote", "add", "upstream", origin_url, cwd=anchor)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    real_monotonic = time.monotonic
    elapsed = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + elapsed[0])

    def delayed_auth(remote: str, *, cwd: str | Path) -> list[str]:
        if remote == "origin":
            elapsed[0] += git_ops.DEFAULT_FETCH_TIMEOUT + 1
        return []

    monkeypatch.setattr(git_ops, "_auth_config_args", delayed_auth)
    prepared = git_ops.prepare_worktree_base(
        anchor, remote="upstream", default_branch="trunk", fast_forward_anchor=False,
    )

    assert elapsed[0] > git_ops.DEFAULT_FETCH_TIMEOUT
    assert prepared.fetched is True
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert _git("rev-parse", "origin/trunk", cwd=anchor) == old_policy
    assert prepared.fetch_error is not None


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


@pytest.mark.parametrize("local_default", [True, False])
def test_preparation_excludes_overlapping_policy_destinations(
    local_repositories: tuple[Path, Path], local_default: bool,
) -> None:
    seed, anchor = local_repositories
    old_anchor = _git("rev-parse", "HEAD", cwd=anchor)
    _git("checkout", "-b", "production", cwd=seed)
    old_policy = _commit(seed, ".agent-worktrees/config.yaml", "marker: policy\n")
    _git("push", "origin", "production", cwd=seed)
    origin_url = _git("remote", "get-url", "origin", cwd=anchor)
    _git("config", "remote.origin/team.url", origin_url, cwd=anchor)
    _git(
        "config", "remote.origin/team.fetch",
        "+refs/heads/*:refs/remotes/origin/team/*", cwd=anchor,
    )
    target = "refs/remotes/origin/team/trunk"
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        f"+refs/heads/production:{target}", cwd=anchor,
    )
    _git("fetch", "origin", "--refmap=", f"+refs/heads/production:{target}", cwd=anchor)
    _git("symbolic-ref", "refs/remotes/origin/HEAD", target, cwd=anchor)
    _commit(seed, "new-policy.txt")
    _git("push", "origin", "production", cwd=seed)
    _git("checkout", "trunk", cwd=seed)
    _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    if not local_default:
        _git("checkout", "-b", "feature", cwd=anchor)
        _git("branch", "-D", "trunk", cwd=anchor)
        with pytest.raises(ValueError, match="local.*trunk|trunk.*local"):
            git_ops.prepare_worktree_base(anchor, remote="origin/team", default_branch="trunk")
    else:
        prepared = git_ops.prepare_worktree_base(
            anchor, remote="origin/team", default_branch="trunk",
        )
        assert prepared.fetched is False
        assert prepared.fetch_error is not None
        assert _git("rev-parse", prepared.start_point, cwd=anchor) == old_anchor
    assert _git("rev-parse", target, cwd=anchor) == old_policy
    assert _git("rev-parse", "HEAD", cwd=anchor) == old_anchor
    assert not (anchor / "new-base.txt").exists()


@pytest.mark.parametrize("source_ref", ["HEAD", "release", "shared"])
def test_preparation_matches_native_remote_short_source_resolution(
    local_repositories: tuple[Path, Path], source_ref: str,
) -> None:
    seed, anchor = local_repositories
    path = ".agent-worktrees/config.yaml"
    _commit(seed, path, "default_branch: trunk\nmarker: old\n")
    _git("push", "origin", "trunk", cwd=seed)
    if source_ref != "HEAD":
        _git("tag", source_ref, cwd=seed)
        _git("push", "origin", f"refs/tags/{source_ref}", cwd=seed)
    if source_ref == "shared":
        _git("checkout", "-b", "shared", cwd=seed)
        _commit(seed, path, "default_branch: trunk\nmarker: wrong-branch\n")
        _git("push", "origin", "refs/heads/shared", cwd=seed)
        _git("checkout", "trunk", cwd=seed)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        f"+{source_ref}:refs/remotes/origin/stable", cwd=anchor,
    )
    if source_ref == "HEAD":
        _git("config", "--add", "remote.origin.fetch", "^refs/heads/trunk", cwd=anchor)
    _git(
        "fetch", "origin", "--refmap=",
        f"+{source_ref}:refs/remotes/origin/stable", cwd=anchor,
    )
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/stable", cwd=anchor)
    new_policy = _commit(seed, path, "default_branch: trunk\nmarker: refreshed\n")
    _git("push", "origin", "trunk", cwd=seed)
    if source_ref != "HEAD":
        _git("tag", "-f", source_ref, cwd=seed)
        _git("push", "origin", f"+refs/tags/{source_ref}", cwd=seed)
    _git(
        "fetch", "origin", "--refmap=",
        f"+{source_ref}:refs/remotes/expected/policy", cwd=anchor,
    )
    native_policy = _git("rev-parse", "refs/remotes/expected/policy", cwd=anchor)
    assert native_policy == new_policy

    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
    )

    assert prepared.fetched is True
    assert prepared.fetch_error is None
    assert _git("rev-parse", "refs/remotes/origin/stable", cwd=anchor) == native_policy
    assert inrepo_config_source.load_inrepo_config_from_committed_ref(
        anchor, (Path(path),),
    )["marker"] == "refreshed"


def test_preparation_does_not_repeat_unavailable_short_source_lookup(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, anchor = local_repositories
    path = ".agent-worktrees/config.yaml"
    old_policy = _commit(seed, path, "default_branch: stable\nmarker: cached\n")
    _git("tag", "release", cwd=seed)
    _git("push", "origin", "trunk", "refs/tags/release", cwd=seed)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        "+release:refs/remotes/origin/stable", cwd=anchor,
    )
    _git("fetch", "origin", "--refmap=", "+release:refs/remotes/origin/stable", cwd=anchor)
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/stable", cwd=anchor)
    _git("push", "origin", ":refs/tags/release", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    original_git = git_ops.git
    observed: list[tuple[str, ...]] = []

    def counted_git(
        *args: str, **kwargs: Any,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        if "ls-remote" in args:
            observed.append(args)
        return original_git(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", counted_git)
    prepared = git_ops.prepare_worktree_base(
        anchor, remote="origin", default_branch="trunk", fast_forward_anchor=False,
    )

    assert len(observed) == 1
    assert prepared.fetched is True
    assert prepared.fetch_error is not None
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert _git("rev-parse", "origin/stable", cwd=anchor) == old_policy


@pytest.mark.parametrize("encoding", ["count", "parameters", "parameter-pairs"])
def test_preparation_retains_credentials_but_not_selectors_at_native_fetch(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, encoding: str,
) -> None:
    seed, anchor = local_repositories
    foreign = anchor.parent / "foreign"
    _git("clone", _git("remote", "get-url", "origin", cwd=anchor), str(foreign), cwd=anchor.parent)
    old_foreign = _git("rev-parse", "HEAD", cwd=foreign)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    opaque = "X-Fixture-Auth: opaque space=one 'two' \\three"
    first = "X-Fixture-First: previous"
    settings = [
        ("http.extraHeader", first), ("core.worktree", str(foreign)),
        ("http.extraHeader", opaque),
    ]
    if encoding == "count":
        monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(settings)))
        for index, (key, value) in enumerate(settings):
            monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
            monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    else:
        def quote(value: str) -> str:
            return "'" + value.replace("'", "'\\''") + "'"

        parameters = " ".join(
            quote(f"{key}={value}") if encoding == "parameters" else f"{quote(key)}={quote(value)}"
            for key, value in settings
        )
        monkeypatch.setenv("GIT_CONFIG_PARAMETERS", parameters)
    original_run = subprocess.run
    fixture = original_run(
        ["git", "config", "--get-all", "http.extraHeader"], cwd=anchor,
        env=dict(os.environ), capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert fixture.returncode == 0
    assert fixture.stdout.rstrip("\n") == f"{first}\n{opaque}"
    observed: list[tuple[int, bool, int]] = []

    def checked_run(*args: Any, **kwargs: Any) -> Any:
        command = args[0]
        if isinstance(command, list) and "fetch" in command:
            def probe(key: str) -> subprocess.CompletedProcess[str]:
                return original_run(
                    [command[0], "config", "--get-all", key], cwd=kwargs["cwd"],
                    env=kwargs["env"], capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

            credential = probe("http.extraHeader")
            observed.append((
                credential.returncode, credential.stdout.rstrip("\n") == f"{first}\n{opaque}",
                probe("core.worktree").returncode,
            ))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", checked_run)
    prepared = git_ops.prepare_worktree_base(anchor, remote="origin", default_branch="trunk")

    assert observed and all(value == (0, True, 1) for value in observed), observed
    assert prepared.fetched is True
    assert prepared.fetch_error is None
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert _git("rev-parse", "HEAD", cwd=foreign) == old_foreign
    assert not (foreign / "new-base.txt").exists()


@pytest.mark.parametrize("environment_os", ["nt", "posix"])
def test_preparation_credential_config_keeps_native_environment_key_case(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    environment_os: str,
) -> None:
    _, anchor = local_repositories
    environment = git_repository_environment.repository_identity_env()
    environment.update({
        "git_config_count": "1",
        "git_config_key_0": "http.extraHeader",
        "git_config_value_0": "X-Fixture-Auth: native-case",
    })
    monkeypatch.setattr(
        git_repository_environment, "os",
        SimpleNamespace(name=environment_os, environ={}),
    )
    child_env = git_repository_environment.repository_identity_env(
        environment, preserve_credentials=True,
    )
    result = subprocess.run(
        ["git", "config", "--get", "http.extraHeader"], cwd=anchor, env=child_env,
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == (0 if environment_os == "nt" else 1)
    assert result.stdout.strip() == (
        "X-Fixture-Auth: native-case" if environment_os == "nt" else ""
    )


@pytest.mark.parametrize(
    ("head_branch", "local_default"),
    [("trunk", True), ("trunk", False), ("side", True)],
)
def test_preparation_resolves_explicit_head_before_base_conflict(
    local_repositories: tuple[Path, Path], head_branch: str, local_default: bool,
) -> None:
    seed, anchor = local_repositories
    old_anchor = _git("rev-parse", "HEAD", cwd=anchor)
    if head_branch == "side":
        _git("checkout", "side", cwd=seed)
        _commit(seed, ".agent-worktrees/config.yaml", "default_branch: trunk\nmarker: policy\n")
        _git("push", "origin", "side", cwd=seed)
    origin = Path(_git("remote", "get-url", "origin", cwd=anchor))
    _git("symbolic-ref", "HEAD", f"refs/heads/{head_branch}", cwd=origin)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        "+HEAD:refs/remotes/origin/trunk", cwd=anchor,
    )
    _git("fetch", "origin", "--refmap=", "+HEAD:refs/remotes/origin/trunk", cwd=anchor)
    old_policy = _git("rev-parse", "origin/trunk", cwd=anchor)
    _git("checkout", "trunk", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    if not local_default:
        _git("checkout", "-b", "feature", cwd=anchor)
        _git("branch", "-D", "trunk", cwd=anchor)

    prepared = git_ops.prepare_worktree_base(anchor, remote="origin", default_branch="trunk")

    if head_branch == "side":
        assert prepared.fetched is False
        assert prepared.fetch_error is not None
        assert _git("rev-parse", "origin/trunk", cwd=anchor) == old_policy
        assert _git("rev-parse", prepared.start_point, cwd=anchor) == old_anchor
        assert _git("rev-parse", "HEAD", cwd=anchor) == old_anchor
    else:
        assert prepared.fetched is True
        assert prepared.fetch_error is None
        assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
        assert _git("rev-parse", "origin/trunk", cwd=anchor) == new_base
        assert _git("rev-parse", "HEAD", cwd=anchor) == (
            new_base if local_default else old_anchor
        )


def test_preparation_redacts_authenticated_source_probe_timeout(
    local_repositories: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed, anchor = local_repositories
    _git("checkout", "-b", "production", cwd=seed)
    _commit(seed, ".agent-worktrees/config.yaml", "default_branch: trunk\n")
    _git("push", "origin", "production", cwd=seed)
    _git(
        "config", "--replace-all", "remote.origin.fetch",
        "+production:refs/remotes/origin/stable", cwd=anchor,
    )
    _git(
        "fetch", "origin", "--refmap=",
        "+refs/heads/production:refs/remotes/origin/stable", cwd=anchor,
    )
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/stable", cwd=anchor)
    _git("checkout", "trunk", cwd=seed)
    new_base = _commit(seed, "new-base.txt")
    _git("push", "origin", "trunk", cwd=seed)
    opaque = "encoded-fixture-value-not-for-diagnostics"

    def fixture_auth(remote: str, *, cwd: str | Path) -> list[str]:
        return ["-c", "http.extraheader=X-Fixture-Auth: " + opaque]

    original_run = subprocess.run
    observed: list[bool] = []

    def timed_run(*args: Any, **kwargs: Any) -> Any:
        command = args[0]
        if isinstance(command, list) and "ls-remote" in command:
            observed.append(any(opaque in argument for argument in command))
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return original_run(*args, **kwargs)

    monkeypatch.setattr(git_ops, "_auth_config_args", fixture_auth)
    monkeypatch.setattr(subprocess, "run", timed_run)
    prepared = worktree_creation._prepare_worktree_source(
        anchor, remote="origin", default_branch="trunk",
        fast_forward_anchor=False, label="fixture",
    )

    assert observed and all(observed)
    assert prepared.fetched is True
    assert _git("rev-parse", prepared.start_point, cwd=anchor) == new_base
    assert prepared.fetch_error is not None
    assert "timed out" in prepared.fetch_error
    assert opaque not in prepared.fetch_error
    assert opaque not in capsys.readouterr().err
