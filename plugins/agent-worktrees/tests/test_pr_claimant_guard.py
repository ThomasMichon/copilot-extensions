"""``pr_cli.require_claimant_worktree`` -- the CWD-is-the-owning-worktree
invariant behind every ``pr-*`` command that can address a foreign repo.

Owning project is the CWD; the target repo is always an explicit argument.
Running from a directory that cannot be traced back to a live, tracked
worktree (an untracked dir, a bare project anchor, or -- the common mistake
-- the *target* repo's own checkout) must be rejected with guidance toward
the correct calling pattern, not a bare failure an agent could read as a
reason to fall back to raw gh/az/git.
"""

from __future__ import annotations

from agent_worktrees import pr_cli
from agent_worktrees import pr_merge_cli
from agent_worktrees import worktree_identity


def test_resolves_to_the_worktree_id_when_cwd_is_a_tracked_worktree(monkeypatch):
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: "wt-123"
    )
    worktree_id, error = pr_cli.require_claimant_worktree("pr-merge")
    assert worktree_id == "wt-123"
    assert error == ""


def test_fails_honestly_when_cwd_traces_to_no_claimant(monkeypatch):
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: None
    )
    worktree_id, error = pr_cli.require_claimant_worktree("pr-merge")
    assert worktree_id is None
    assert "pr-merge" in error
    assert "no claimant" in error
    # Guides toward the correct pattern instead of a bare failure.
    assert "owner/target-repo" in error
    assert "your own project's worktree" in error
    # Explicitly steers away from the raw-tool fallback this exists to prevent.
    assert "gh/az repos/git" in error


def test_pr_watch_dispatch_refuses_without_a_claimant_worktree(monkeypatch, capsys):
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: None
    )
    rc = pr_cli.cmd_pr_watch_dispatch(["wait", "owner/name", "1"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "no claimant" in captured.out or "no claimant" in captured.err


def test_pr_merge_dispatch_refuses_without_a_claimant_worktree(monkeypatch, capsys):
    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id_from_cwd", lambda config=None: None
    )
    rc = pr_merge_cli.cmd_pr_merge_dispatch(["owner/name", "1"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "no claimant" in captured.out or "no claimant" in captured.err
