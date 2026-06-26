"""Tests for agent_worktrees.git_collab -- sync / feature-branch / merge-to-feature.

These exercise the real git operations against the shared ``pr_repo`` fixture
(bare remote + anchor + a worktree branch with two commits on ``master``).
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import git_collab, git_ops


def _git(*args: str, cwd) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _ahead(branch: str, upstream: str, *, cwd) -> int:
    out = _git("rev-list", "--count", f"{upstream}..{branch}", cwd=cwd)
    return int(out or "0")


# ---------------------------------------------------------------------------
# sync_forward
# ---------------------------------------------------------------------------

class TestSyncForward:
    def test_pull_forward_preserves_local_work(self, pr_repo):
        """master advances; sync rebases the worktree onto it, keeping local work."""
        config, wid, wt_path, remote = pr_repo
        anchor = Path(config.default_repo.anchor)

        # Advance origin/master with an unrelated commit.
        (anchor / "upstream.txt").write_text("from master\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "upstream advance", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        assert git_collab.sync_forward(wid, config) is True

        # Worktree's two commits are preserved on top of the new master.
        assert _ahead(f"worktree/{wid}", "origin/master", cwd=wt_path) == 2
        # The new upstream commit is now in the worktree's history.
        assert git_ops.ref_exists("origin/master", cwd=wt_path)
        assert (wt_path / "upstream.txt").exists()

    def test_squash_merged_commits_drop(self, pr_repo):
        """When the worktree's changes land squashed upstream, sync drops them."""
        config, wid, wt_path, remote = pr_repo
        anchor = Path(config.default_repo.anchor)

        # Simulate a squash-merge: one upstream commit adding the SAME files
        # (identical content) the worktree added across its two commits.
        (anchor / "a.txt").write_text("one\n")
        (anchor / "b.txt").write_text("two\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "squash: worktree work", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        assert git_collab.sync_forward(wid, config) is True

        # Both worktree commits became empty on rebase and were dropped.
        assert _ahead(f"worktree/{wid}", "origin/master", cwd=wt_path) == 0

    def test_refuses_dirty_tree(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        assert git_collab.sync_forward(wid, config) is False

    def test_dry_run_makes_no_change(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        before = _git("rev-parse", "HEAD", cwd=wt_path)
        assert git_collab.sync_forward(wid, config, dry_run=True) is True
        assert _git("rev-parse", "HEAD", cwd=wt_path) == before


# ---------------------------------------------------------------------------
# manage_feature_branch
# ---------------------------------------------------------------------------

class TestFeatureBranch:
    def test_create_local_at_head(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        assert git_collab.manage_feature_branch(wid, config, "shared") is True
        assert git_ops.local_branch_exists("feature/shared", cwd=wt_path)
        # feature points at the worktree HEAD.
        assert _git("rev-parse", "feature/shared", cwd=wt_path) == _git(
            "rev-parse", "HEAD", cwd=wt_path
        )

    def test_push_publishes_to_remote(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        assert git_collab.manage_feature_branch(
            wid, config, "shared", push=True
        ) is True
        assert git_ops.remote_branch_exists("origin", "feature/shared", cwd=wt_path)

    def test_accepts_feature_prefixed_name(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        assert git_collab.manage_feature_branch(wid, config, "feature/already") is True
        assert git_ops.local_branch_exists("feature/already", cwd=wt_path)

    def test_sync_fast_forwards_from_remote(self, pr_repo):
        """A second checkout advances the remote feature; --sync pulls it forward."""
        config, wid, wt_path, remote = pr_repo
        anchor = Path(config.default_repo.anchor)

        # Publish the shared branch from the worktree.
        assert git_collab.manage_feature_branch(
            wid, config, "shared", push=True
        ) is True
        head0 = _git("rev-parse", "feature/shared", cwd=wt_path)

        # Advance origin/feature/shared from the anchor (a stand-in peer). The
        # anchor shares refs with the worktree, so feature/shared already exists;
        # just check it out there rather than recreating it.
        _git("checkout", "feature/shared", cwd=anchor)
        (anchor / "peer.txt").write_text("peer work\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "peer commit", cwd=anchor)
        _git("push", "origin", "feature/shared", cwd=anchor)
        _git("checkout", "master", cwd=anchor)

        assert git_collab.manage_feature_branch(
            wid, config, "shared", sync=True
        ) is True
        head1 = _git("rev-parse", "feature/shared", cwd=wt_path)
        assert head1 != head0
        assert head1 == _git("rev-parse", "origin/feature/shared", cwd=wt_path)

    def test_refuses_to_drop_commits_on_diverged_feature(self, pr_repo):
        """If feature has commits not in HEAD, default mode refuses to move it."""
        config, wid, wt_path, remote = pr_repo
        # Create feature with an extra commit beyond the worktree HEAD.
        _git("branch", "feature/shared", "HEAD", cwd=wt_path)
        _git("checkout", "feature/shared", cwd=wt_path)
        (wt_path / "extra.txt").write_text("only on feature\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "feature-only", cwd=wt_path)
        _git("checkout", f"worktree/{wid}", cwd=wt_path)

        assert git_collab.manage_feature_branch(wid, config, "shared") is False


# ---------------------------------------------------------------------------
# merge_to_feature
# ---------------------------------------------------------------------------

class TestMergeToFeature:
    def test_handoff_ff_and_push(self, pr_repo):
        """Publish a shared branch, add work, hand it back: ff + push, linear."""
        config, wid, wt_path, remote = pr_repo

        # Host publishes the shared feature at the current HEAD.
        assert git_collab.manage_feature_branch(
            wid, config, "shared", push=True
        ) is True
        feature_head0 = _git("rev-parse", "origin/feature/shared", cwd=wt_path)

        # Delegate does more work on its worktree branch.
        (wt_path / "slice.txt").write_text("delegate slice\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "delegate work", cwd=wt_path)

        assert git_collab.merge_to_feature(wid, config, "shared") is True

        # Remote feature advanced to include the new work...
        feature_head1 = _git("rev-parse", "origin/feature/shared", cwd=wt_path)
        assert feature_head1 != feature_head0
        assert feature_head1 == _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        # ...and it is a strict fast-forward (old head is an ancestor of new).
        assert git_ops.git(
            "merge-base", "--is-ancestor", feature_head0, feature_head1,
            cwd=str(wt_path), check=False,
        ).returncode == 0

    def test_no_push_stops_at_local_ff(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        assert git_collab.manage_feature_branch(
            wid, config, "shared", push=True
        ) is True
        remote_before = _git("rev-parse", "origin/feature/shared", cwd=wt_path)

        (wt_path / "slice.txt").write_text("delegate slice\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "delegate work", cwd=wt_path)

        assert git_collab.merge_to_feature(
            wid, config, "shared", push=False
        ) is True
        # Local feature advanced, remote unchanged.
        assert _git("rev-parse", "feature/shared", cwd=wt_path) == _git(
            "rev-parse", f"worktree/{wid}", cwd=wt_path
        )
        _git("fetch", "origin", cwd=wt_path)
        assert _git("rev-parse", "origin/feature/shared", cwd=wt_path) == remote_before

    def test_refuses_when_feature_not_published(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        assert git_collab.merge_to_feature(wid, config, "never-published") is False

    def test_refuses_dirty_tree(self, pr_repo):
        config, wid, wt_path, remote = pr_repo
        assert git_collab.manage_feature_branch(
            wid, config, "shared", push=True
        ) is True
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        assert git_collab.merge_to_feature(wid, config, "shared") is False
