"""In-process dispatch guards; real Git recovery cases remain in the full tier."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_publish, pr_rebase
from agent_worktrees import providers
from agent_worktrees.providers.base import PullResult


@pytest.mark.guard
@pytest.mark.parametrize("recovered", [True, False])
@pytest.mark.parametrize("tip,ancestor,has_repo", [
    ("b" * 40, False, True),
    ("b" * 40, True, True),
    ("", False, True),
    ("b" * 40, False, False),
])
def test_initial_stale_publication_dispatches_only_proven_recovery(monkeypatch, tip, ancestor, has_repo, recovered):
    record = object()
    repo = object() if has_repo else None
    pr = SimpleNamespace(remote="", rewrite_identity="")
    stale = git_ops.PushResult(ok=False, stderr="rejected (stale info)")
    success = git_ops.PushResult(ok=True)
    refused = git_ops.PushResult(ok=False, stdout="recovery stdout", stderr="recovery refused")
    pushes, replays = [], []
    monkeypatch.setattr(pr_publish, "_pr_for", lambda *_: pr)
    monkeypatch.setattr(pr_publish, "publish_lock", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(pr_publish, "push_slug", lambda *_a, **_k: "owner/repo")
    monkeypatch.setattr(pr_publish, "push_identity", lambda *_a, **_k: "github.com/owner/repo")
    monkeypatch.setattr(pr_publish, "_tip", lambda *_: tip)
    monkeypatch.setattr(git_ops, "is_commit_ancestor", lambda *_a, **_k: ancestor)
    def git(*args, **kwargs):
        if args[:2] == ("remote", "get-url"):
            return SimpleNamespace(
                returncode=0, stdout="https://github.com/owner/repo.git\n", stderr=""
            )
        if args[:3] == ("config", "--null", "--get-all"):
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        pytest.fail(f"unexpected fixture Git command: {args!r}")

    monkeypatch.setattr(git_ops, "git", git)

    def git(*args, **kwargs):
        if args[:2] == ("remote", "get-url"):
            return SimpleNamespace(returncode=0, stdout="https://github.com/owner/repo.git\n")
        assert args[0] == "config"
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(git_ops, "git", git)

    def push(*args, **kwargs):
        pushes.append((args, kwargs))
        return stale if len(pushes) == 1 else success

    def replay(*args, **kwargs):
        replays.append((args, kwargs))
        return success if recovered else refused

    monkeypatch.setattr(git_ops, "push", push)
    monkeypatch.setattr(pr_rebase, "push", replay)
    expected = "a" * 40
    refspec = "worktree/abcd:refs/heads/pr/topic-abcd"
    result = pr_publish.push_checked(
        record, "origin", refspec, cwd="fixture", repo=repo,
        force_with_lease_expect=expected,
    )
    if tip and not ancestor and has_repo:
        assert result is (success if recovered else stale)
        if not recovered:
            assert "stale info" in result.stderr and "recovery refused" in result.stderr
            assert "recovery stdout" in result.stdout
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


@pytest.mark.guard
@pytest.mark.parametrize("change,accepted", [
    ({}, True), ({"state": "active"}, True),
    ({"state": "closed"}, False), ({"state": "merged"}, False),
    ({"merged": True}, False), ({"number": 8}, False),
    ({"head_ref": "other"}, False), ({"base_ref": "main"}, False),
    ({"head_url": "https://other.example/owner/repo"}, False),
    ({"head_url": "https://github.com/other/repo"}, False), ({"head_url": ""}, False),
])
def test_partial_confirmation_requires_live_unchanged_provider_association(monkeypatch, change, accepted):
    pull = PullResult(
        number=7, state="open", head_sha="b" * 40, head_ref="pr/topic-abcd",
        base_ref="dev", head_url="https://github.com/owner/repo.git",
    )
    for key, value in change.items():
        setattr(pull, key, value)
    repo = SimpleNamespace(
        default_branch="dev", pr=SimpleNamespace(provider="github", api_base=""),
    )
    pr = SimpleNamespace(number=7, repo="owner/repo", provider="github")
    monkeypatch.setattr(providers, "get_provider", lambda *_: SimpleNamespace(get_pull=lambda *_a, **_k: pull))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "")
    result = pr_publish._provider_head(
        repo, pr, expected_ref="pr/topic-abcd", expected_identity="github.com/owner/repo",
    )
    assert result == ("b" * 40 if accepted else "")


@pytest.mark.guard
@pytest.mark.parametrize("url,accepted", [
    ("https://dev.azure.com/org/project/_git/repo", True),
    ("git@ssh.dev.azure.com:v3/org/project/repo", True),
    ("https://dev.azure.com/other/project/_git/repo", False),
    ("https://dev.azure.com/org/other/_git/repo", False),
    ("https://other.example/org/project/_git/repo", False),
    ("http://dev.azure.com/org/project/_git/repo", False),
])
def test_partial_confirmation_preserves_secure_azure_repository_aliases(monkeypatch, url, accepted):
    pull = PullResult(
        number=7, state="active", head_sha="b" * 40, head_ref="pr/topic-abcd",
        base_ref="dev", head_url=url,
    )
    repo = SimpleNamespace(
        default_branch="dev", pr=SimpleNamespace(provider="azure-devops", api_base=""),
    )
    pr = SimpleNamespace(number=7, repo="org/project/repo", provider="azure-devops")
    monkeypatch.setattr(providers, "get_provider", lambda *_: SimpleNamespace(get_pull=lambda *_a, **_k: pull))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *_: "")
    result = pr_publish._provider_head(
        repo, pr, expected_ref="pr/topic-abcd",
        expected_identity="ssh.dev.azure.com/v3/org/project/repo",
    )
    assert result == ("b" * 40 if accepted else "")
