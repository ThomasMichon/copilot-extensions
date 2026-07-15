"""Tests for agent_worktrees.pr_complete -- post-merge worktree reconciliation.

Real git operations against the ``pr_repo`` fixture (bare remote + anchor + a
worktree branch with two commits).  The crux case is the squash-merge: the
worktree's work lands as one upstream commit, and pr-complete must fast-forward
*past* it (hard reset, no replay) rather than refuse (ff) or replay (rebase).
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import git_ops, pr_complete


def _git(*args: str, cwd) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _ahead(branch: str, upstream: str, *, cwd) -> int:
    return int(_git("rev-list", "--count", f"{upstream}..{branch}", cwd=cwd) or "0")


def _squash_merge_upstream(anchor: Path, *, files: dict[str, str], msg: str) -> None:
    """Simulate a squash-merge: one upstream commit adding the given files."""
    for name, content in files.items():
        (anchor / name).write_text(content)
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", msg, cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)


class TestPrComplete:
    def test_reset_past_squash_merge(self, pr_repo):
        """The worktree's work is squash-merged upstream -> hard-reset past it."""
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        # The worktree added a.txt="one" + b.txt="two" (fixture); squash-merge
        # the identical content as ONE upstream commit.
        _squash_merge_upstream(
            anchor, files={"a.txt": "one\n", "b.txt": "two\n"},
            msg="squash: worktree work (#1)",
        )
        before = _git("rev-parse", "HEAD", cwd=wt_path)

        res = pr_complete.complete_worktree(wid, config)

        assert res["success"] is True
        assert res["action"] == "reset-past-squash"
        assert res["dropped"] == 2
        # HEAD is now exactly the upstream tip -- no replayed commits.
        assert _git("rev-parse", "HEAD", cwd=wt_path) == _git(
            "rev-parse", "origin/master", cwd=wt_path)
        assert _ahead(f"worktree/{wid}", "origin/master", cwd=wt_path) == 0
        # Pre-complete state is recoverable.
        assert _git("rev-parse", pr_complete.BACKUP_REF, cwd=wt_path) == before

    def test_squash_merge_with_divergent_upstream_no_conflict(self, pr_repo):
        """A squash-merge PLUS an unrelated upstream change: still a clean reset.

        This is the phantom-conflict case a plain rebase can trip on -- the
        squash folded the change and upstream moved on.  pr-complete resets
        past it with no conflict.
        """
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        _squash_merge_upstream(
            anchor, files={"a.txt": "one\n", "b.txt": "two\n"},
            msg="squash: worktree work",
        )
        # An additional unrelated upstream commit after the squash.
        (anchor / "other.txt").write_text("unrelated\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "later upstream", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        res = pr_complete.complete_worktree(wid, config)
        assert res["success"] is True
        assert res["action"] == "reset-past-squash"
        assert _git("rev-parse", "HEAD", cwd=wt_path) == _git(
            "rev-parse", "origin/master", cwd=wt_path)
        assert (wt_path / "other.txt").exists()

    def test_up_to_date_when_already_reconciled(self, pr_repo):
        """After a reset, a second pr-complete is a no-op 'up-to-date'."""
        config, wid, _wt, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        _squash_merge_upstream(
            anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash")
        assert pr_complete.complete_worktree(wid, config)["action"] == "reset-past-squash"
        res2 = pr_complete.complete_worktree(wid, config)
        assert res2["success"] is True
        assert res2["action"] == "up-to-date"

    def test_fast_forward_when_no_local_commits(self, pr_repo):
        """No local commits, upstream advanced -> plain fast-forward."""
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        # Reset the worktree branch to origin/master (no local work), then
        # advance upstream.
        _git("reset", "--hard", "origin/master", cwd=wt_path)
        (anchor / "up.txt").write_text("advance\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "upstream advance", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        res = pr_complete.complete_worktree(wid, config)
        assert res["success"] is True
        assert res["action"] == "fast-forwarded"
        assert (wt_path / "up.txt").exists()

    def test_rebase_preserves_genuinely_new_commits(self, pr_repo):
        """Part of the work is merged, but a NEW local commit remains -> rebase."""
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        # Squash-merge only the fixture's work upstream.
        _squash_merge_upstream(
            anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash")
        # Add a genuinely-new local commit the squash does NOT contain.
        (wt_path / "new.txt").write_text("brand new\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "new local work", cwd=wt_path)

        res = pr_complete.complete_worktree(wid, config)
        assert res["success"] is True
        assert res["action"] == "rebased"
        # The new commit is preserved on top of the updated upstream.
        assert (wt_path / "new.txt").exists()
        assert _ahead(f"worktree/{wid}", "origin/master", cwd=wt_path) == 1

    def test_reconcile_preserves_post_merge_divergence_net_zero(self, pr_repo):
        """A post-merge commit that diverges from upstream but nets to the
        merge-base MUST survive the reconcile (aperture-labs #2854).

        The regression: ``_branch_fully_merged`` enumerates only the paths in
        ``merge_base..branch``.  A commit that reverts a file back to its
        merge-base value is *net-zero* on that axis, so the path is skipped and
        the branch is judged "fully merged" -- and the old hard reset then
        silently restored upstream's (unwanted) value, dropping the operator's
        deliberate change.  Rebase-first replays that commit and preserves it.
        """
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        branch = f"worktree/{wid}"
        # Shared base carrying config.txt="stable" on upstream (the merge-base).
        (anchor / "config.txt").write_text("stable\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "base: config=stable", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)
        # Rebuild the worktree branch from that shared base.
        _git("fetch", "origin", cwd=wt_path)
        _git("reset", "--hard", "origin/master", cwd=wt_path)
        # PR work: add feature.txt, then set config=branch ...
        (wt_path / "feature.txt").write_text("F\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "PR: add feature", cwd=wt_path)
        (wt_path / "config.txt").write_text("branch\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "PR: set config=branch", cwd=wt_path)
        # ... then the deliberate divergence that must survive: put config back
        # to the base "stable" (net-zero vs merge-base, but != upstream's tip).
        (wt_path / "config.txt").write_text("stable\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "keep config=stable", cwd=wt_path)
        # Upstream squash-merges the PR as feature.txt + config=branch.
        (anchor / "feature.txt").write_text("F\n")
        (anchor / "config.txt").write_text("branch\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "squash PR", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        res = pr_complete.complete_worktree(wid, config)

        assert res["success"] is True, res
        # The operator's config=stable survives (old code reset it to "branch").
        assert (wt_path / "config.txt").read_text() == "stable\n", res
        assert res["action"] == "rebased"
        assert _ahead(branch, "origin/master", cwd=wt_path) == 1
        # The merged PR's feature.txt is present (carried by upstream).
        assert (wt_path / "feature.txt").read_text() == "F\n"

    def test_behind_but_unmerged_is_normal_rebase(self, pr_repo):
        """Upstream advanced unrelatedly; the PR is NOT merged -> rebase forward."""
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        # Unrelated upstream advance; the worktree's a.txt/b.txt are NOT merged.
        (anchor / "unrelated.txt").write_text("elsewhere\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "unrelated advance", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        res = pr_complete.complete_worktree(wid, config)
        assert res["success"] is True
        assert res["action"] == "rebased"
        # Both original commits survive (nothing was merged).
        assert _ahead(f"worktree/{wid}", "origin/master", cwd=wt_path) == 2
        assert (wt_path / "unrelated.txt").exists()

    def test_refuses_dirty_tree(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        res = pr_complete.complete_worktree(wid, config)
        assert res["success"] is False
        assert res["action"] == "error"
        assert "uncommitted" in res["error"]

    def test_dry_run_changes_nothing(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        _squash_merge_upstream(
            anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash")
        before = _git("rev-parse", "HEAD", cwd=wt_path)
        res = pr_complete.complete_worktree(wid, config, dry_run=True)
        assert res["success"] is True
        assert res["action"] == "reset-past-squash"
        assert _git("rev-parse", "HEAD", cwd=wt_path) == before  # unchanged

    def test_deleted_head_branch_case(self, pr_repo):
        """The remote PR head branch being gone must not block reconciliation.

        pr-complete's squash detection uses local refs (git cherry vs the local
        upstream tracking ref), so a deleted remote head branch (#2147) is
        irrelevant -- the reset still succeeds.
        """
        config, wid, wt_path, _ = pr_repo
        anchor = Path(config.default_repo.anchor)
        # Publish then delete a PR head branch on the remote (simulating the
        # gate deleting it post-merge), and squash-merge the work.
        _git("push", "origin", f"worktree/{wid}:refs/heads/pr/x", cwd=wt_path)
        _git("push", "origin", "--delete", "pr/x", cwd=wt_path)
        _squash_merge_upstream(
            anchor, files={"a.txt": "one\n", "b.txt": "two\n"}, msg="squash")

        res = pr_complete.complete_worktree(wid, config)
        assert res["success"] is True
        assert res["action"] == "reset-past-squash"
