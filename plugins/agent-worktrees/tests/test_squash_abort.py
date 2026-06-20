"""Tests for the pre-squash failure path (issue #783).

`push-changes` must NOT silently fall back to pushing unsquashed commits when
the pre-squash step fails. `git_ops.squash_branch` surfaces the failure reason,
and `finalize.push_changes` aborts (unless `--allow-unsquashed`).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_worktrees import git_ops

# ---------------------------------------------------------------------------
# Helpers -- build a real git repo with N commits ahead of a base ref
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> None:
    res = git_ops.git(*args, cwd=str(repo), check=False)
    assert res.returncode == 0, f"git {' '.join(args)} failed: {res.stderr}"


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "checkout", "-q", "-b", "base")
    (repo / "f.txt").write_text("0\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base commit")
    return repo


def _add_commits(repo: Path, n: int) -> None:
    """Create a feature branch with *n* commits ahead of `base`."""
    _git(repo, "checkout", "-q", "-b", "feature")
    for i in range(n):
        (repo / "f.txt").write_text(f"{i + 1}\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        # bypass hooks when authoring so the failing-hook scenario is isolated
        # to the squash re-commit
        _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", f"c{i + 1}")


def _install_failing_pre_commit_hook(repo: Path) -> None:
    """Install a pre-commit hook that always fails."""
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(
        "#!/bin/sh\necho 'ruff: F401 unused import' >&2\nexit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)


# ---------------------------------------------------------------------------
# git_ops.squash_branch
# ---------------------------------------------------------------------------

def test_squash_success_returns_true_none(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _add_commits(repo, 3)
    ok, reason = git_ops.squash_branch("base", "squashed", cwd=str(repo))
    assert ok is True
    assert reason is None
    # exactly one commit ahead of base now
    cnt = git_ops.git("rev-list", "--count", "base..HEAD", cwd=str(repo), check=False)
    assert cnt.stdout.strip() == "1"


def test_squash_noop_single_commit(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _add_commits(repo, 1)
    ok, reason = git_ops.squash_branch("base", "squashed", cwd=str(repo))
    assert ok is True
    assert reason is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook script")
def test_squash_failure_surfaces_reason_and_restores(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _add_commits(repo, 3)
    orig_head = git_ops.git("rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
    _install_failing_pre_commit_hook(repo)

    ok, reason = git_ops.squash_branch("base", "squashed", cwd=str(repo))

    assert ok is False
    assert reason is not None
    # the underlying hook diagnostic is surfaced
    assert "hook" in reason.lower() or "ruff" in reason.lower()
    # branch restored to original commits (3 ahead, unsquashed, untouched)
    restored = git_ops.git("rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
    assert restored == orig_head
    cnt = git_ops.git("rev-list", "--count", "base..HEAD", cwd=str(repo), check=False)
    assert cnt.stdout.strip() == "3"


def test_squash_bad_upstream_returns_reason(tmp_path: Path):
    repo = _make_repo(tmp_path)
    _add_commits(repo, 2)
    ok, reason = git_ops.squash_branch("no-such-ref", "squashed", cwd=str(repo))
    assert ok is False
    assert reason and "merge-base" in reason


# ---------------------------------------------------------------------------
# finalize.push_changes -- abort vs. opt-in fallback
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# finalize.push_changes -- abort vs. opt-in fallback
# ---------------------------------------------------------------------------

def _make_pushable_repo(tmp_path: Path, n_commits: int):
    """Build a repo at <tmp_path>/repo on branch worktree/repo, n commits ahead
    of an `origin/base` remote-tracking ref. Returns (repo_path, worktree_id)."""
    from agent_worktrees import config as cfg

    repo = _make_repo(tmp_path)  # branch `base`, one commit
    base_sha = git_ops.git("rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
    # Simulate the remote-tracking ref push_changes diff against.
    _git(repo, "update-ref", "refs/remotes/origin/base", base_sha)
    # Feature branch must be named worktree/<id>.
    _git(repo, "checkout", "-q", "-b", "worktree/repo")
    for i in range(n_commits):
        (repo / "f.txt").write_text(f"{i + 1}\n", encoding="utf-8")
        _git(repo, "add", "f.txt")
        _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", f"c{i + 1}")

    repo_cfg = cfg.RepoConfig(
        anchor=str(repo),
        worktree_root=str(tmp_path),
        default_branch="base",
        remote="origin",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="repo", repos={"repo": repo_cfg},
    )
    return repo, "repo", config


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook script")
def test_push_changes_aborts_on_squash_failure(tmp_path: Path, monkeypatch):
    from agent_worktrees import finalize

    repo, wt_id, config = _make_pushable_repo(tmp_path, 3)
    orig_head = git_ops.git("rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
    _install_failing_pre_commit_hook(repo)

    # Don't touch a real remote; abort happens before any push anyway.
    monkeypatch.setattr(finalize.git_ops, "fetch", lambda *a, **k: None)
    # A push would only happen far past the squash step; guard it just in case.
    monkeypatch.setattr(finalize.git_ops, "merge_ff", lambda *a, **k: False)

    ok = finalize.push_changes(wt_id, config, allow_unsquashed=False)

    assert ok is False
    # original unsquashed commits preserved (still 3 ahead of origin/base),
    # proving the squash was rolled back and nothing progressed toward a push.
    head = git_ops.git("rev-parse", "HEAD", cwd=str(repo), check=False).stdout.strip()
    assert head == orig_head


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook script")
def test_push_changes_surfaces_reason_in_output(tmp_path: Path, monkeypatch, capsys):
    """The user-visible abort output must carry the underlying failure reason,
    not just return it internally (issue #783 acceptance)."""
    from agent_worktrees import finalize

    repo, wt_id, config = _make_pushable_repo(tmp_path, 3)
    _install_failing_pre_commit_hook(repo)
    monkeypatch.setattr(finalize.git_ops, "fetch", lambda *a, **k: None)

    ok = finalize.push_changes(wt_id, config, allow_unsquashed=False)

    assert ok is False
    combined = capsys.readouterr()
    text = (combined.out + combined.err).lower()
    assert "pre-squash failed" in text
    assert "hook" in text or "reason" in text
    cnt = git_ops.git(
        "rev-list", "--count", "refs/remotes/origin/base..HEAD",
        cwd=str(repo), check=False,
    )
    assert cnt.stdout.strip() == "3"


@pytest.mark.skipif(os.name == "nt", reason="POSIX hook script")
def test_allow_unsquashed_proceeds_past_squash(tmp_path: Path, monkeypatch):
    """With --allow-unsquashed, a squash failure does NOT abort at the squash
    step -- the flow continues (here we stop it right after via a sentinel)."""
    from agent_worktrees import finalize

    repo, wt_id, config = _make_pushable_repo(tmp_path, 3)
    _install_failing_pre_commit_hook(repo)
    monkeypatch.setattr(finalize.git_ops, "fetch", lambda *a, **k: None)

    reached_rebase = {"hit": False}

    def _fake_rebase(*a, **k):
        reached_rebase["hit"] = True
        return False  # stop the flow cleanly right after the squash decision

    monkeypatch.setattr(finalize.git_ops, "rebase", _fake_rebase)

    ok = finalize.push_changes(wt_id, config, allow_unsquashed=True)

    assert ok is False  # we forced rebase to fail
    assert reached_rebase["hit"] is True, (
        "with --allow-unsquashed the flow must proceed past the squash step"
    )

