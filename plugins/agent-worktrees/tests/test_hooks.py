"""Tests for agent_worktrees.hooks -- PR-workflow git hook guardrails."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_worktrees import git_ops, hooks


def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


@pytest.fixture
def anchor_and_worktree(tmp_path: Path):
    """An anchor repo + one linked worktree on a worktree/<id> branch."""
    anchor = tmp_path / "anchor"
    git_ops.git("init", "-b", "master", str(anchor))
    _git("config", "user.email", "t@e.com", cwd=anchor)
    _git("config", "user.name", "T", cwd=anchor)
    (anchor / "f.txt").write_text("x\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "init", cwd=anchor)

    wt = tmp_path / "wt"
    git_ops.git("worktree", "add", str(wt), "-b", "worktree/wt-aaaa", cwd=str(anchor))
    return anchor, wt


class TestDetection:
    def test_in_worktree_true(self, anchor_and_worktree):
        _, wt = anchor_and_worktree
        assert hooks.in_worktree(str(wt)) is True

    def test_in_worktree_false_for_anchor(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        assert hooks.in_worktree(str(anchor)) is False


class TestPreCommit:
    def test_blocks_default_branch_commit_in_worktree(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.chdir(wt)
        monkeypatch.setattr(hooks, "_current_branch", lambda cwd: "master")
        monkeypatch.setattr(hooks, "_default_branch", lambda cwd: "master")
        assert hooks._pre_commit() == 1

    def test_allows_worktree_branch_commit(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.chdir(wt)
        monkeypatch.setattr(hooks, "_default_branch", lambda cwd: "master")
        # Real branch is worktree/wt-aaaa -> allowed
        assert hooks._pre_commit() == 0

    def test_allows_anchor_commit_on_default(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.setattr(hooks, "_default_branch", lambda cwd: "master")
        # In the anchor, in_worktree is False -> always allowed
        assert hooks._pre_commit() == 0


class TestPrePush:
    def test_allows_when_pr_push_env_set(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.chdir(wt)
        monkeypatch.setenv("AGENT_WORKTREES_PR_PUSH", "1")
        assert hooks._pre_push() == 0

    def test_blocks_worktree_push_in_pr_mode(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.chdir(wt)
        monkeypatch.delenv("AGENT_WORKTREES_PR_PUSH", raising=False)
        monkeypatch.setattr(hooks, "_pr_enabled", lambda: True)
        assert hooks._pre_push() == 1

    def test_allows_when_pr_mode_disabled(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.chdir(wt)
        monkeypatch.delenv("AGENT_WORKTREES_PR_PUSH", raising=False)
        monkeypatch.setattr(hooks, "_pr_enabled", lambda: False)
        assert hooks._pre_push() == 0

    def test_allows_anchor_push(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.delenv("AGENT_WORKTREES_PR_PUSH", raising=False)
        monkeypatch.setattr(hooks, "_pr_enabled", lambda: True)
        assert hooks._pre_push() == 0


class TestAllowPrPush:
    def test_sets_and_restores(self):
        os.environ.pop("AGENT_WORKTREES_PR_PUSH", None)
        with hooks.allow_pr_push():
            assert os.environ["AGENT_WORKTREES_PR_PUSH"] == "1"
        assert "AGENT_WORKTREES_PR_PUSH" not in os.environ

    def test_restores_prior_value(self):
        os.environ["AGENT_WORKTREES_PR_PUSH"] = "prior"
        try:
            with hooks.allow_pr_push():
                assert os.environ["AGENT_WORKTREES_PR_PUSH"] == "1"
            assert os.environ["AGENT_WORKTREES_PR_PUSH"] == "prior"
        finally:
            os.environ.pop("AGENT_WORKTREES_PR_PUSH", None)


class TestInstallHooks:
    def test_installs_shims(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        installed = hooks.install_hooks(anchor)
        assert set(installed) == set(hooks.HOOK_NAMES)
        hdir = hooks.hooks_dir_for(anchor)
        for name in hooks.HOOK_NAMES:
            shim = hdir / name
            assert shim.exists()
            text = shim.read_text()
            assert hooks._SHIM_MARKER in text
            assert f"hook {name}" in text

    def test_idempotent(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        hooks.install_hooks(anchor)
        hooks.install_hooks(anchor)  # no raise, still present
        hdir = hooks.hooks_dir_for(anchor)
        assert (hdir / "pre-commit").exists()

    def test_preserves_foreign_hook(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        hdir = hooks.hooks_dir_for(anchor)
        hdir.mkdir(parents=True, exist_ok=True)
        (hdir / "pre-commit").write_text("#!/bin/sh\necho custom\n")
        hooks.install_hooks(anchor)
        # Foreign hook preserved as .local, our shim now primary
        assert (hdir / "pre-commit.local").exists()
        assert "custom" in (hdir / "pre-commit.local").read_text()
        assert hooks._SHIM_MARKER in (hdir / "pre-commit").read_text()
