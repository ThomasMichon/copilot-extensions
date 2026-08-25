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


class TestConfigResolution:
    """#234 defect 3: resolve PR mode from the anchor's committed in-repo config
    with no --project / active-project context, as a bare git-hook must."""

    def test_anchor_from_worktree(self, anchor_and_worktree):
        anchor, wt = anchor_and_worktree
        assert hooks._anchor_from_cwd(str(wt)).resolve() == anchor.resolve()

    def test_pr_enabled_reads_inrepo_config(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
        # No committed config -> PR mode off (fails open, not raising).
        assert hooks._pr_enabled(str(wt)) is False
        # Committed in-repo config with pr.enabled -> resolved via the anchor
        # from a worktree cwd (and from the anchor itself).
        cfg_dir = anchor / ".agent-worktrees"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.yaml").write_text("pr:\n  enabled: true\n")
        assert hooks._pr_enabled(str(wt)) is True
        assert hooks._pr_enabled(str(anchor)) is True


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
        # An UNREGISTERED (non-worktree-class) anchor is base-repo mode -> allowed.
        assert hooks._pre_commit() == 0


class TestAnchorCommitGuard:
    """The session-independent anchor guard: a worktree-class repo's ANCHOR must
    not receive direct commits (mirrors the Copilot preToolUse anchor_write_guard,
    but at git level so it fires even when the plugin's hooks don't load)."""

    @staticmethod
    def _fake_entry():
        import types
        return types.SimpleNamespace(name="myrepo")

    def test_resolver_matches_registered_anchor(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        from agent_worktrees import repos
        resolved = str(hooks._anchor_from_cwd(str(anchor)))
        entry = repos.RepoEntry(name="myrepo", repo_class="worktree",
                                paths={repos._current_platform(): resolved})
        monkeypatch.setattr(repos, "list_repos", lambda class_filter=None: [entry])
        got = hooks._worktree_class_anchor(str(anchor))
        assert got is not None and got.name == "myrepo"

    def test_blocks_worktree_class_anchor_commit(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.delenv("ANCHOR_WRITE_GUARD", raising=False)
        monkeypatch.setattr(hooks, "_worktree_class_anchor", lambda cwd: self._fake_entry())
        from agent_worktrees import allow_edits
        monkeypatch.setattr(allow_edits, "is_active", lambda name: False)
        assert hooks._pre_commit() == 1

    def test_kill_switch_allows_anchor_commit(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.setattr(hooks, "_worktree_class_anchor", lambda cwd: self._fake_entry())
        monkeypatch.setenv("ANCHOR_WRITE_GUARD", "off")
        assert hooks._pre_commit() == 0

    def test_break_glass_allows_anchor_commit(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.delenv("ANCHOR_WRITE_GUARD", raising=False)
        monkeypatch.setattr(hooks, "_worktree_class_anchor", lambda cwd: self._fake_entry())
        from agent_worktrees import allow_edits
        monkeypatch.setattr(allow_edits, "is_active", lambda name: name == "myrepo")
        assert hooks._pre_commit() == 0

    def test_non_worktree_class_anchor_allowed(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.delenv("ANCHOR_WRITE_GUARD", raising=False)
        monkeypatch.setattr(hooks, "_worktree_class_anchor", lambda cwd: None)
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
        monkeypatch.setattr(hooks, "_pr_enabled", lambda cwd: True)
        assert hooks._pre_push() == 1

    def test_allows_when_pr_mode_disabled(self, anchor_and_worktree, monkeypatch):
        anchor, wt = anchor_and_worktree
        monkeypatch.chdir(wt)
        monkeypatch.delenv("AGENT_WORKTREES_PR_PUSH", raising=False)
        monkeypatch.setattr(hooks, "_pr_enabled", lambda cwd: False)
        assert hooks._pre_push() == 0

    def test_allows_anchor_push(self, anchor_and_worktree, monkeypatch):
        anchor, _ = anchor_and_worktree
        monkeypatch.chdir(anchor)
        monkeypatch.delenv("AGENT_WORKTREES_PR_PUSH", raising=False)
        monkeypatch.setattr(hooks, "_pr_enabled", lambda cwd: True)
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
            # The PR guard is gated on AGENT_WORKTREES_HOOKS=1 ...
            assert 'if [ "$AGENT_WORKTREES_HOOKS" = "1" ]' in text
            # ... but the preserved foreign hook (.local) runs unconditionally
            # (its invocation is NOT inside the HOOKS guard block).
            guard_idx = text.index('"$AGENT_WORKTREES_HOOKS"')
            fi_idx = text.index("\nfi\n", guard_idx)
            local_idx = text.index(f"{name}.local")
            assert local_idx > fi_idx

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


class TestHooksPathReconciliation:
    """core.hooksPath cleanup is an adopt (mutation) concern; detection is
    read-only for install/update warn (Phase 6, effort declarative-worktree-launch)."""

    def test_stale_none_when_unset(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        assert hooks.stale_hooks_path(anchor) is None

    def test_stale_detected_when_shadowing(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        _git("config", "core.hooksPath", "tools/hooks", cwd=anchor)
        assert hooks.stale_hooks_path(anchor) == "tools/hooks"

    def test_not_stale_when_points_at_managed_dir(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        managed = hooks.hooks_dir_for(anchor)
        _git("config", "core.hooksPath", str(managed), cwd=anchor)
        assert hooks.stale_hooks_path(anchor) is None

    def test_clear_unsets_and_returns_value(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        _git("config", "core.hooksPath", "tools/hooks", cwd=anchor)
        assert hooks.clear_stale_hooks_path(anchor) == "tools/hooks"
        # Now unset -> git honors .git/hooks again, nothing left to clear.
        assert hooks._local_hooks_path(anchor) is None
        assert hooks.clear_stale_hooks_path(anchor) is None

    def test_clear_noop_when_nothing_stale(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        assert hooks.clear_stale_hooks_path(anchor) is None

    def test_hook_health_fresh_then_armed_then_stale(self, anchor_and_worktree):
        anchor, _ = anchor_and_worktree
        # Fresh: no shims, no stale hooksPath.
        assert hooks.hook_health(anchor) == (False, None)
        # Armed: shims present.
        hooks.install_hooks(anchor)
        assert hooks.hook_health(anchor) == (True, None)
        # A shadowing core.hooksPath is reported even with shims present.
        _git("config", "core.hooksPath", "tools/hooks", cwd=anchor)
        present, stale = hooks.hook_health(anchor)
        assert present is True
        assert stale == "tools/hooks"
