"""In-process dispatch guards; real Git recovery cases remain in the full tier."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_publish, pr_rebase


@pytest.mark.guard
@pytest.mark.parametrize("tip,ancestor,has_repo", [
    ("b" * 40, False, True),
    ("b" * 40, True, True),
    ("", False, True),
    ("b" * 40, False, False),
])
def test_initial_stale_publication_dispatches_only_proven_recovery(monkeypatch, tip, ancestor, has_repo):
    record = object()
    repo = object() if has_repo else None
    pr = SimpleNamespace(remote="", rewrite_identity="")
    stale = git_ops.PushResult(ok=False, stderr="rejected (stale info)")
    success = git_ops.PushResult(ok=True)
    pushes, replays = [], []
    monkeypatch.setattr(pr_publish, "_pr_for", lambda *_: pr)
    monkeypatch.setattr(pr_publish, "publish_lock", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(pr_publish, "push_slug", lambda *_a, **_k: "owner/repo")
    monkeypatch.setattr(pr_publish, "push_identity", lambda *_a, **_k: "github.com/owner/repo")
    monkeypatch.setattr(pr_publish, "_tip", lambda *_: tip)
    monkeypatch.setattr(git_ops, "is_commit_ancestor", lambda *_a, **_k: ancestor)

    def push(*args, **kwargs):
        pushes.append((args, kwargs))
        return stale if len(pushes) == 1 else success

    def replay(*args, **kwargs):
        replays.append((args, kwargs))
        return success

    monkeypatch.setattr(git_ops, "push", push)
    monkeypatch.setattr(pr_rebase, "push", replay)
    expected = "a" * 40
    refspec = "worktree/abcd:refs/heads/pr/topic-abcd"
    result = pr_publish.push_checked(
        record, "origin", refspec, cwd="fixture", repo=repo,
        force_with_lease_expect=expected,
    )
    if tip and not ancestor and has_repo:
        assert result is success
        assert len(pushes) == len(replays) == 1
        args, kwargs = replays[0]
        assert args == (record, repo, "origin", refspec, expected)
        assert kwargs["reconcile_head"] == tip
    elif tip and ancestor:
        assert result is success
        assert len(pushes) == 2 and not replays
        assert "force_with_lease_expect" not in pushes[1][1]
    else:
        assert result is stale
        assert len(pushes) == 1 and not replays
