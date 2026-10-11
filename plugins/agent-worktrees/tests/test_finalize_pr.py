"""PR finalize preconditions, race safety, and merged-pointer reconciliation."""

from __future__ import annotations

import pytest
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import PRWorkflowSetup, _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.finalize")


class TestPRFinalize(PRWorkflowSetup):
    def test_precondition_fails_before_push(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        # Record a pr.branch that was never pushed.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr = tracking.PRRecord(state="creating", branch="feature/never-pushed-aaaa")
        tracking.save_record(rec)
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is False
        assert "not upstream" in err

    def test_precondition_ok_after_create_pr(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_precondition_detects_unpushed(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        # Add a local commit on the feature branch without pushing (create-pr
        # returns HEAD to the base branch (#1804), so check out the feature
        # branch first to add a feedback commit to it).
        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "feedback", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is False
        assert "unpushed" in err

    def _simulate_squash_merge(self, config, wid, feature):
        """Squash-merge *feature* into origin/master (mimics a Gitea merge).

        Leaves ``origin/<feature>`` at its stale pre-merge head -- the exact
        condition that tripped the old precondition (#1045).
        """
        anchor = config.default_repo.anchor
        _git("fetch", "origin", cwd=anchor)
        _git("checkout", "master", cwd=anchor)
        _git("merge", "--squash", f"origin/{feature}", cwd=anchor)
        _git("commit", "-m", f"Squash merge {feature}", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

    def test_precondition_ok_after_merge(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        # origin/<feature> is stale (pre-merge); the OLD check would false-block.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_finalize_refuses_dirty_worktree_at_merged_head(self, published_pr_repo):
        # #4400 review finding: _pr_finalize_precondition only inspects
        # commits, never the working tree -- a modified/untracked file at an
        # otherwise-safe merged head must still block finalize, or the
        # destructive cleanup that follows would silently discard it.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)
        (wt_path / "uncommitted.txt").write_text("real work, never committed\n")

        ok = fin.validate_and_finalize(wid, config)
        assert ok is False
        assert (wt_path / "uncommitted.txt").exists()

    def test_finalize_reruns_precondition_after_lock_acquired(self, published_pr_repo, monkeypatch):
        # #4400 round 14: the merged-head precondition previously ran only
        # BEFORE the finalize lock was acquired -- a commit landing in that
        # TOCTOU window (between the preflight and the destructive cleanup)
        # would be silently discarded. validate_and_finalize must re-run the
        # SAME precondition immediately after the lock is held.
        from agent_worktrees import finalize as fin
        from agent_worktrees import finalize_lock
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)

        real_acquire = finalize_lock.FinalizeLock.acquire

        def racing_acquire(lock_self):
            real_acquire(lock_self)
            # Simulate a commit landing in the TOCTOU window, after the
            # preflight validated safety but before lock-protected cleanup.
            (wt_path / "raced_in.txt").write_text("landed after preflight\n")
            git_ops.git("add", "-A", cwd=str(wt_path))
            git_ops.git("commit", "-m", "race window commit", cwd=str(wt_path))

        monkeypatch.setattr(finalize_lock.FinalizeLock, "acquire", racing_acquire)

        ok = fin.validate_and_finalize(wid, config)
        assert ok is False
        assert (wt_path / "raced_in.txt").exists()

    def test_finalize_catches_race_commit_landing_during_process_termination(
        self, published_pr_repo, monkeypatch,
    ):
        # #4400 round 15: round 14's re-check ran ONCE, immediately after the
        # finalize lock was acquired -- but further code (record reload,
        # pointer reconciliation, live-session checks, process termination)
        # still ran AFTER that point and BEFORE the actual destructive
        # removal, leaving a residual window. The re-check must run as the
        # LAST possible step, immediately before the destructive git
        # operation -- proven here by racing a commit in AFTER process
        # termination, later than round 14's check point.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)

        real_terminate = fin.procs.terminate_processes_under

        def racing_terminate(worktree_path):
            result = real_terminate(worktree_path)
            (wt_path / "raced_in_late.txt").write_text("landed during cleanup\n")
            git_ops.git("add", "-A", cwd=str(wt_path))
            git_ops.git("commit", "-m", "late race window commit", cwd=str(wt_path))
            return result

        monkeypatch.setattr(fin.procs, "terminate_processes_under", racing_terminate)

        ok = fin.validate_and_finalize(wid, config)
        assert ok is False
        assert (wt_path / "raced_in_late.txt").exists()

    def test_finalize_rechecks_precondition_before_reconciliation(
        self, published_pr_repo, monkeypatch,
    ):
        # #4400 round 16: reconciliation (`_reconcile_merged_pointers`)
        # rebases the worktree branch onto upstream -- and git's rebase
        # silently DROPS a commit whose patch is already reachable/applied
        # upstream, exactly the kind of commit a race could land right
        # before this call, with no trace left to detect afterward. The
        # precondition must re-run immediately before reconciliation is
        # invoked, not only once after lock acquisition and once at the
        # very end. Verified deterministically via call-order spies (a
        # full git-level reproduction would need to reconstruct git's own
        # patch-id "already applied" heuristic, which is unreliable to pin
        # down across git versions) rather than assuming a specific git
        # rebase outcome.
        from agent_worktrees import finalize as fin
        config, wid, _wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)

        from agent_worktrees import finalize_open_pr_gate

        call_order: list[str] = []
        real_reconcile = fin._reconcile_merged_pointers
        real_precheck = finalize_open_pr_gate.pr_precondition_recheck

        def spy_reconcile(*a, **k):
            call_order.append("reconcile")
            return real_reconcile(*a, **k)

        def spy_precheck(*a, **k):
            call_order.append("precheck")
            return real_precheck(*a, **k)

        monkeypatch.setattr(fin, "_reconcile_merged_pointers", spy_reconcile)
        monkeypatch.setattr(
            finalize_open_pr_gate, "pr_precondition_recheck", spy_precheck,
        )

        ok = fin.validate_and_finalize(wid, config)
        assert ok is True
        assert "precheck" in call_order and "reconcile" in call_order
        assert call_order.index("precheck") < call_order.index("reconcile"), (
            f"precondition recheck must run BEFORE reconciliation, got {call_order}"
        )

    def test_precondition_ok_after_merge_remote_branch_deleted(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        # Provider deleted the remote feature branch on merge.
        _git("push", "origin", "--delete", feature, cwd=config.default_repo.anchor)
        _git("fetch", "origin", "--prune", cwd=str(wt_path))
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err

    def test_reconcile_aligns_worktree_base_after_merge(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        # Drift scenario: HEAD checked out on the feature branch. create-pr
        # returns HEAD to the base branch (#1804), so establish the drift here.
        _git("checkout", feature, cwd=wt_path)
        self._simulate_squash_merge(config, wid, feature)
        _git("fetch", "origin", cwd=str(wt_path))
        repo = config.default_repo
        wt_branch = f"worktree/{wid}"

        # HEAD is on the feature branch (drift); worktree/<id> is a free pointer.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == feature

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # worktree/<id> now aligns with origin/master -> 0 ahead in the picker.
        wt_sha = _git("rev-parse", wt_branch, cwd=wt_path)
        up_sha = _git("rev-parse", "origin/master", cwd=wt_path)
        assert wt_sha == up_sha
        # The live feature checkout is untouched.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == feature

    def test_reconcile_fast_forwards_anchor_default_branch(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        repo = config.default_repo
        anchor = repo.anchor
        # Rewind the anchor's local master behind origin to prove the FF.
        _git("reset", "--hard", "HEAD~1", cwd=anchor)
        assert _git("rev-parse", "master", cwd=anchor) != \
            _git("rev-parse", "origin/master", cwd=anchor)

        fin._reconcile_merged_pointers(repo, str(wt_path), anchor, f"worktree/{wid}")

        assert _git("rev-parse", "master", cwd=anchor) == \
            _git("rev-parse", "origin/master", cwd=anchor)

    def test_reconcile_fast_forwards_base_when_head_on_base(self, published_pr_repo):
        """#1804: create-pr leaves HEAD on worktree/<id> at the squashed commit,
        so after the squash-merge the base is *diverged* (1 ahead + behind).
        Reconcile realigns it in place to origin/master once the content is
        confirmed on upstream -- HEAD never leaves worktree/<id>, no work lost.
        (Same in-place path the refspec scheme uses.)"""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        feature = "feature/add-feature-aaaa"
        wt_branch = f"worktree/{wid}"
        # create-pr leaves HEAD on the worktree base branch.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch

        self._simulate_squash_merge(config, wid, feature)
        _git("fetch", "origin", cwd=str(wt_path))
        repo = config.default_repo
        # The merge advanced origin/master, so the base branch is now behind.
        assert _git("rev-parse", wt_branch, cwd=wt_path) != \
            _git("rev-parse", "origin/master", cwd=wt_path)

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # Fast-forwarded in place: base branch == origin/master, HEAD unchanged.
        assert _git("rev-parse", wt_branch, cwd=wt_path) == \
            _git("rev-parse", "origin/master", cwd=wt_path)
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch

    def test_precondition_ok_refspec_after_create_pr(self, pr_repo):
        # #1815: finalize precondition passes in refspec -- the tracked PR head
        # (pr/<slug>) is a remote ref, and content-on-upstream / remote-exists
        # both hold; no local feature branch is required.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        import dataclasses
        repo = config.default_repo
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, strategy="detach"))
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_reconcile_refspec_realigns_after_merge_in_place(self, pr_repo):
        # #1815: refspec worktree/<id> sits ahead while the PR is open, so after
        # the squash-merge it is diverged (ahead+behind) and a plain FF can't
        # align it. Reconcile realigns it in place once content is confirmed on
        # upstream -- HEAD stays on worktree/<id>, no work lost.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        wt_branch = f"worktree/{wid}"
        # refspec: worktree/<id> carries the squashed commit (1 ahead of master).
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch
        assert len(git_ops.get_commits_ahead(
            wt_branch, "origin/master", cwd=str(wt_path))) == 1

        self._simulate_squash_merge(config, wid, "pr/add-feature-aaaa")
        _git("fetch", "origin", cwd=str(wt_path))
        # Diverged now: 1 ahead (pre-merge squash) + behind (the merge commit).
        repo = config.default_repo
        assert _git("rev-parse", wt_branch, cwd=wt_path) != \
            _git("rev-parse", "origin/master", cwd=wt_path)

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # Realigned in place to origin/master; HEAD never left worktree/<id>.
        assert _git("rev-parse", wt_branch, cwd=wt_path) == \
            _git("rev-parse", "origin/master", cwd=wt_path)
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == wt_branch

    def test_reconcile_refspec_leaves_open_pr_ahead(self, pr_repo):
        # An OPEN refspec PR must NOT be realigned -- its content is not yet on
        # upstream, so reconcile leaves worktree/<id> ahead (honest #1815/#5).
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        wt_branch = f"worktree/{wid}"
        before = _git("rev-parse", wt_branch, cwd=wt_path)
        repo = config.default_repo
        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)
        # Unchanged -- the PR is still open (content not on upstream).
        assert _git("rev-parse", wt_branch, cwd=wt_path) == before
        assert len(git_ops.get_commits_ahead(
            wt_branch, "origin/master", cwd=str(wt_path))) == 1

    def test_finalize_refuses_unmerged_direct(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, _wt_path, _ = pr_repo
        req_config = self._required_config(config)
        # Unmerged work, no PR -> finalize must refuse (not prune).
        ok = fin.validate_and_finalize(wid, req_config)
        assert ok is False
