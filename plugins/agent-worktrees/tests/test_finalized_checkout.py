"""Bounded finalized identity inspection."""

import subprocess
from types import SimpleNamespace

import pytest

from agent_worktrees import finalized_checkout


@pytest.mark.parametrize("failing_call", [1, 2])
def test_identity_timeout_is_bounded_and_does_not_assert_final(monkeypatch, failing_call):
    calls = []

    def git(*args, timeout, **kwargs):
        assert timeout == 5
        calls.append(args)
        if len(calls) == failing_call:
            raise subprocess.TimeoutExpired(args, timeout)
        return SimpleNamespace(stdout="a" * 40)

    monkeypatch.setattr(finalized_checkout.git_ops, "git", git)
    record = SimpleNamespace(
        status="finalized", worktree_path="fixture",
        finalized_checkout={"head": "a" * 40, "branch": "worktree/fixture"},
    )
    assert finalized_checkout.matches(record) is False
