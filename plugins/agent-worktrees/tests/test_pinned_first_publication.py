"""Pinned pre-push absence must not inherit strict post-push presence rules."""

from __future__ import annotations

import subprocess

import pytest

from agent_worktrees import git_ops, git_push_transport, pr_publication_state, pr_publish, tracking

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr_publish.publication_state"),
    pytest.mark.timeout(120),
]


def test_pinned_first_publication_accepts_absence_without_weakening_tracking(
    pr_repo, monkeypatch,
):
    config, wid, worktree, remote = pr_repo
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", lambda url: [])
    branch = "pr/new-publication"
    provisional = tracking.PRRecord(state="creating", branch=branch)
    with git_push_transport.pinned_destination("origin", (str(remote),)):
        assert pr_publish._tip("origin", branch, str(worktree)) == ""
        assert pr_publication_state.provisional_lease(
            provisional, "origin", branch, str(worktree),
        ) == ""
        with pytest.raises(OSError, match="tracking refresh"):
            git_push_transport.published_ref(
                "origin", (str(remote),), f"refs/heads/{branch}", worktree,
            )
        result = git_ops.push(
            "origin", f"HEAD:refs/heads/{branch}", cwd=worktree,
            force_with_lease=True, force_with_lease_expect="",
        )
        assert result.ok, result.stderr
        head = git_ops.git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
        assert pr_publish._tip("origin", branch, str(worktree)) == head
        with pytest.raises(ValueError, match="already exists"):
            pr_publication_state.provisional_lease(
                provisional, "origin", branch, str(worktree),
            )
        assert git_push_transport.published_ref(
            "origin", (str(remote),), f"refs/heads/{branch}", worktree,
        ) == head


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_pinned_read_failure_stays_unknown(monkeypatch, failure):
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", lambda url: [])

    def failed(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("git", 30)
        return subprocess.CompletedProcess("git", 1, "", "fixture read failure")

    monkeypatch.setattr(git_ops, "git", failed)
    with git_push_transport.pinned_destination("origin", ("https://example.com/org/repo.git",)):
        assert pr_publish._tip("origin", "pr/new", ".") is None
        with pytest.raises(ValueError, match="Cannot inspect"):
            pr_publication_state.provisional_lease(
                tracking.PRRecord(state="creating"), "origin", "pr/new", ".",
            )


def test_successful_suffix_match_is_not_the_exact_ref(monkeypatch):
    monkeypatch.setattr(git_ops, "_auth_config_args_for_url", lambda url: [])
    monkeypatch.setattr(
        git_ops, "git",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            "git", 0, f"{'a' * 40}\trefs/heads/archive/pr/new\n", "",
        ),
    )
    with git_push_transport.pinned_destination("origin", ("https://example.com/org/repo.git",)):
        assert pr_publish._tip("origin", "pr/new", ".") == ""
