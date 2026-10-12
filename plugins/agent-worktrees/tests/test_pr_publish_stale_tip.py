"""Stale tracked head recovery must never force an unrelated remote tip."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_publish


@pytest.mark.parametrize("ancestor", [True, False])
@pytest.mark.parametrize("refspec", ["HEAD:refs/heads/pr/example", "pr/example"])
def test_stale_lease_retries_only_fast_forward(monkeypatch, ancestor, refspec):
    monkeypatch.setattr(pr_publish, "publish_lock", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(pr_publish, "_pr_for", lambda *args: None)
    def tip(remote, branch, cwd):
        assert branch == "pr/example"
        return "remote-tip"

    monkeypatch.setattr(pr_publish, "_tip", tip)
    def ancestry(*args, **kwargs):
        if args[:2] == ("remote", "get-url"):
            return SimpleNamespace(returncode=0, stdout="https://github.com/owner/repo.git\n", stderr="")
        if args[0] == "config":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        assert args == (
            "--no-replace-objects", "merge-base", "--is-ancestor", "remote-tip",
            refspec.partition(":")[0],
        )
        assert kwargs["check"] is False
        assert kwargs["isolated_repository"] is True
        return SimpleNamespace(returncode=0 if ancestor else 1)

    monkeypatch.setattr(git_ops, "git", ancestry)
    calls = []

    def push(*args, **kwargs):
        calls.append(kwargs)
        return git_ops.PushResult(ok=len(calls) > 1, stderr="stale info" if len(calls) == 1 else "")

    monkeypatch.setattr(git_ops, "push", push)
    result = pr_publish.push_checked(
        None, "origin", refspec, cwd=".", force_with_lease_expect="old",
    )
    assert result.ok == ancestor
    assert len(calls) == (2 if ancestor else 1)
    if ancestor:
        assert "force_with_lease" not in calls[1]
        assert "force_with_lease_expect" not in calls[1]
