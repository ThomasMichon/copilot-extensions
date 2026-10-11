"""Bounded finalized identity inspection."""

import subprocess
from types import SimpleNamespace

from agent_worktrees import finalized_checkout


def test_identity_timeout_is_bounded_and_does_not_assert_final(monkeypatch):
    calls = []

    def git(*args, timeout, **kwargs):
        assert timeout == 5
        calls.append(args)
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(finalized_checkout.git_ops, "git", git)
    record = SimpleNamespace(
        status="finalized", worktree_path="fixture",
        finalized_checkout={"head": "a" * 40, "branch": "worktree/fixture"},
    )
    assert finalized_checkout.matches(record) is False


def test_identity_and_dirtiness_use_one_git_process(monkeypatch):
    calls = []

    def git(*args, timeout, **kwargs):
        calls.append(args)
        assert timeout == 5
        return SimpleNamespace(stdout=f"# branch.oid {'a' * 40}\n# branch.head renamed\n")

    monkeypatch.setattr(finalized_checkout.git_ops, "git", git)
    record = SimpleNamespace(
        status="finalized", worktree_path="fixture",
        finalized_checkout={"head": "a" * 40, "branch": "renamed"},
    )
    assert finalized_checkout.matches(record)
    assert len(calls) == 1
