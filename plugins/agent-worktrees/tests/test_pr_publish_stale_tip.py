"""Stale tracked head recovery must never force an unrelated remote tip."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_publish


@pytest.mark.parametrize("ancestor", [True, False])
def test_stale_lease_retries_only_fast_forward(monkeypatch, ancestor):
    monkeypatch.setattr(pr_publish, "publish_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(pr_publish, "_pr_for", lambda *args: None)
    monkeypatch.setattr(pr_publish, "_tip", lambda *args: "remote-tip")
    monkeypatch.setattr(
        git_ops, "git", lambda *args, **kwargs: SimpleNamespace(returncode=0 if ancestor else 1),
    )
    calls = []

    def push(*args, **kwargs):
        calls.append(kwargs)
        return git_ops.PushResult(ok=len(calls) > 1, stderr="stale info" if len(calls) == 1 else "")

    monkeypatch.setattr(git_ops, "push", push)
    result = pr_publish.push_checked(
        None, "origin", "HEAD:refs/heads/pr/example", cwd=".", force_with_lease_expect="old",
    )
    assert result.ok == ancestor
    assert len(calls) == (2 if ancestor else 1)
    if ancestor:
        assert "force_with_lease" not in calls[1]
        assert "force_with_lease_expect" not in calls[1]
