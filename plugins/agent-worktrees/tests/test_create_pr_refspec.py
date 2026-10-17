"""Refspec PR creation and repeated or parallel head publication."""

from __future__ import annotations

import pytest
from pathlib import Path
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.create_refspec")


class TestCreatePRRefspec:
    def _refspec_config(self, config, **pr_overrides):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, head_scheme="refspec", **pr_overrides)
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def test_pushes_from_worktree_branch_no_feature_branch(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True, res
        assert res["branch"] == "pr/add-feature-aaaa"

        # HEAD never leaves the worktree branch.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        # No local feature/pr branch is created.
        assert not git_ops.local_branch_exists("pr/add-feature-aaaa", cwd=str(wt_path))
        # The head ref exists on the remote.
        assert git_ops.remote_branch_exists("origin", "pr/add-feature-aaaa", cwd=str(wt_path))
        # worktree/<id> sits 1 ahead of master (NOT reset to upstream).
        ahead = git_ops.get_commits_ahead(f"worktree/{wid}", "origin/master", cwd=str(wt_path))
        assert len(ahead) == 1
        # The remote head is the worktree branch's own commit.
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == \
            _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)

    def test_tracking_records_refspec_head(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr is not None
        assert rec.pr.branch == "pr/add-feature-aaaa"
        assert rec.pr.state == "open"

    def test_refspec_rerun_is_idempotent(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first
        before = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        # Re-run from worktree/<id> (still 1-ahead, live PR) re-pushes cleanly --
        # no "already exists" error, HEAD stays put, no duplicate PR record.
        second = pr_ops.create_pr(wid, config, title="Add feature")
        assert second["success"] is True, second
        assert second["branch"] == "pr/add-feature-aaaa"
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        after = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        assert after == before  # same squashed content re-pushed
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 1

    def test_reused_worktree_after_squash_merge_opens_clean_pr(self, pr_repo):
        """#546: a reused worktree whose prior PR was **squash-merged** must
        open a fresh PR without a spurious rebase conflict.

        Reproduces the real failure: PR #1's single squashed commit lands on
        origin/master as a NEW commit (a squash-merge), then new work is added
        on the same worktree. create-pr must rebase-drop the already-merged
        commit (by patch-id) and squash only the new work -- not fuse the two
        into one patch that re-conflicts with master.
        """
        config, wid, wt_path, remote = pr_repo
        config = self._refspec_config(config)
        wt_branch = f"worktree/{wid}"

        # PR #1: squashes work1+work2 into one commit on the PR head ref.
        r1 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r1["success"], r1
        pr1_head = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)

        # Simulate GitHub's **squash-merge** of PR #1 onto master: apply the PR
        # head's patch as a brand-new commit on master (new SHA, same patch).
        anchor = config.repos["ext"].anchor
        _git("fetch", "origin", cwd=Path(anchor))
        _git("checkout", "master", cwd=Path(anchor))
        _git("merge", "--squash", pr1_head, cwd=Path(anchor))
        _git("commit", "-m", "Add feature (squash-merged) (#1)", cwd=Path(anchor))
        _git("push", "origin", "master", cwd=Path(anchor))
        merged_sha = _git("rev-parse", "origin/master", cwd=Path(anchor))
        pr_ops.set_pr(wid, number=1, state="merged")

        # New work for PR #2 on the SAME (reused) worktree branch: modify a file
        # that PR #1 introduced (now already on master). The squash-first order
        # fuses PR #1's create-a.txt with this modify-a.txt into one patch that
        # re-adds a.txt over master's copy -> add/add conflict. Rebase-first
        # drops PR #1's commit (patch-id) so only this modify applies, cleanly.
        _git("fetch", "origin", cwd=wt_path)
        _git("checkout", wt_branch, cwd=wt_path)
        (wt_path / "a.txt").write_text("one\nmodified for pr2\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "new work for PR2", cwd=wt_path)

        # With the squash-first order this returned an error ("Rebase onto
        # origin/master hit conflicts"); rebase-first drops the merged commit.
        r2 = pr_ops.create_pr(wid, config, title="Second feature")
        assert r2["success"], r2
        assert r2["branch"] == "pr/second-feature-aaaa"

        # The new PR is based on the post-merge master and carries ONLY the new
        # work -- exactly one commit ahead, touching only a.txt (PR #1's create
        # of a.txt/b.txt is not re-introduced by it).
        ahead = git_ops.get_commits_ahead(wt_branch, "origin/master", cwd=str(wt_path))
        assert len(ahead) == 1, ahead
        assert _git("merge-base", wt_branch, "origin/master", cwd=wt_path) == merged_sha
        pr2_files = _git(
            "diff", "--name-only", "origin/master", wt_branch, cwd=wt_path
        ).split()
        assert pr2_files == ["a.txt"], pr2_files
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert [p.state for p in rec.prs] == ["merged", "open"]

    def test_custom_head_pattern(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config, head_pattern="submit/{slug}-{suffix}")
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["branch"] == "submit/add-feature-aaaa"
        assert git_ops.remote_branch_exists("origin", "submit/add-feature-aaaa", cwd=str(wt_path))
        assert not git_ops.local_branch_exists("submit/add-feature-aaaa", cwd=str(wt_path))

    def test_snapshot_mode_when_pinned(self, pr_repo):
        # The stock pr_repo pins head_scheme=snapshot: a local feature branch is
        # created and pushed under the feature/ namespace. (The plugin default is
        # now refspec (#1815); the fixture pins snapshot to keep exercising it.)
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["branch"] == "feature/add-feature-aaaa"
        assert git_ops.local_branch_exists("feature/add-feature-aaaa", cwd=str(wt_path))

    def test_refspec_open_failed_rerun_idempotent(self, pr_repo):
        # push-succeeded-but-open-not-done leaves a live tracked PR at 'open'
        # with number=None (auto_open off). Re-running create-pr must be
        # idempotent -- reuse the tracked PR, re-push, no "already exists".
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"]
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.number is None and rec.pr.state == "open"
        second = pr_ops.create_pr(wid, config, title="Add feature")
        assert second["success"] is True, second
        assert "error" not in second
        assert second["branch"] == "pr/add-feature-aaaa"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 1

    def test_refspec_new_without_live_pr_uses_refspec(self, pr_repo):
        # --new with no existing live PR is still pure refspec (no snapshot
        # fallback -- the fallback only triggers for a *parallel* live PR).
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        res = pr_ops.create_pr(wid, config, title="Add feature", new=True)
        assert res["branch"] == "pr/add-feature-aaaa"
        assert not git_ops.local_branch_exists("pr/add-feature-aaaa", cwd=str(wt_path))
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"

    def test_refspec_new_parallel_snapshots_without_disturbing_first(self, pr_repo):
        # --new while a refspec PR is live: the parallel PR snapshots onto its
        # own branch WITHOUT resetting worktree/<id> or touching PR #1's head.
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        r1 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r1["branch"] == "pr/add-feature-aaaa"
        wt_before = _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        pr1_head_before = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Second thing", new=True)
        assert r2["success"], r2
        assert r2["branch"] == "pr/second-thing-aaaa"

        # HEAD never left the worktree branch; worktree/<id> was NOT reset.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == wt_before
        # PR #1's remote head is untouched by the parallel push.
        assert _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path) == pr1_head_before
        # Both PR heads exist on the remote; two PRs tracked.
        assert git_ops.remote_branch_exists("origin", "pr/add-feature-aaaa", cwd=str(wt_path))
        assert git_ops.remote_branch_exists("origin", "pr/second-thing-aaaa", cwd=str(wt_path))
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert {p.branch for p in rec.prs} == {
            "pr/add-feature-aaaa", "pr/second-thing-aaaa",
        }
