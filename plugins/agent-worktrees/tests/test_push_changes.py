"""Incremental PR pushes, branch guards, leases, and push-error reporting."""

from __future__ import annotations

import pytest
from pathlib import Path
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import PRWorkflowSetup, _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.push")


class TestExistingFeaturePush:
    """#3561: push() no longer bypasses a real pre-push hook, so
    _push_existing_feature's failure path needs to surface the real stderr
    (a hook rejection) and, separately, offer retry guidance only when the
    failure is actually retryable (a non-fast-forward race), not for every
    failure -- a hook rejection is not fixed by rebase-and-retry."""

    def test_reports_push_error(self, monkeypatch):
        monkeypatch.setattr(pr_ops, "_rev", lambda *_args, **_kwargs: "deadbeef")
        monkeypatch.setattr(
            pr_ops.git_ops,
            "push",
            lambda *_args, **_kwargs: git_ops.PushResult(
                ok=False, stderr="BLOCKED: release guard"
            ),
        )

        result = pr_ops._push_existing_feature(
            ".", "feature/change", "origin", None, None, None, {},
            config=None, worktree_id="", title="", body=None, open_pr=None,
            draft=False, attribution=None,
        )

        assert result["error"] == (
            "Failed to (re)push 'feature/change' to 'origin'.\n"
            "BLOCKED: release guard"
        )

    def test_reports_retry_guidance_for_non_fast_forward(self, monkeypatch):
        monkeypatch.setattr(pr_ops, "_rev", lambda *_args, **_kwargs: "deadbeef")
        monkeypatch.setattr(
            pr_ops.git_ops,
            "push",
            lambda *_args, **_kwargs: git_ops.PushResult(
                ok=False, stderr="[rejected] non-fast-forward"
            ),
        )

        result = pr_ops._push_existing_feature(
            ".", "feature/change", "origin", None, None, None, {},
            config=None, worktree_id="", title="", body=None, open_pr=None,
            draft=False, attribution=None,
        )

        assert result["error"] == (
            "Failed to (re)push 'feature/change' to 'origin'.\n"
            "The remote branch advanced; rebase and retry.\n"
            "This could be caused either by this worktree's own earlier "
            "create-pr/push-changes rewrite of the PR branch or by another actor "
            "updating the remote branch after your last fetch/observation. "
            "Fetch/inspect the remote PR branch, reconcile any divergent local "
            "PR history, then re-run "
            "agent-worktrees create-pr.\n"
            "[rejected] non-fast-forward"
        )


class TestPushChanges(PRWorkflowSetup):
    def test_push_changes_updates_feature_branch(self, published_pr_repo, capsys):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _remote_dir = published_pr_repo

        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        anchor = Path(config.repos["ext"].anchor)
        _git("checkout", "master", cwd=anchor)
        (anchor / "upstream.txt").write_text("unrelated upstream advance\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "advance upstream", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        # New feedback commit directly on the feature branch. create-pr returns
        # HEAD to the base branch (#1804), so check out the feature branch to
        # add feedback commits that push-changes then pushes to the PR branch.
        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Preserved the published PR tip" in combined
        assert "incremental updates" in combined

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote feature branch advanced
        assert _git(
            "merge-base", before, "origin/feature/add-feature-aaaa", cwd=wt_path,
        ) == before

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        local_head = _git("rev-parse", "HEAD", cwd=wt_path)
        assert rec.pr.head_sha == local_head
        assert rec.pr.state == "open"

    def test_push_changes_refuses_divergent_remote_feature_branch(self, pr_repo):
        """push-changes' lease safety (#5298) was only integration-tested
        through create_pr; add parametrized-in-spirit coverage for the
        snapshot scheme too -- a divergent remote head must reject the push,
        not force-overwrite it."""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        other = remote_dir.parent / "other-clone-push-changes"
        _git("clone", str(remote_dir), str(other), cwd=remote_dir.parent)
        _git("config", "user.email", "other@example.com", cwd=other)
        _git("config", "user.name", "Other", cwd=other)
        _git(
            "checkout", "-B", "feature/add-feature-aaaa",
            "origin/feature/add-feature-aaaa", cwd=other,
        )
        (other / "remote.txt").write_text("other actor\n")
        _git("add", "-A", cwd=other)
        _git("commit", "-m", "remote update", cwd=other)
        _git("push", "origin", "feature/add-feature-aaaa", cwd=other)
        remote_head = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=other)

        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)

        assert ok is False
        assert _git(
            "rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path
        ) == remote_head  # not overwritten

    def test_push_changes_is_blocked_by_a_real_client_side_pre_push_hook(self, published_pr_repo):
        """#3561: push() must not silently disable a repo's own release-guard
        pre-push hook (e.g. this repo's check-changefile-presence.py). Install
        a real, unconditionally-failing pre-push hook -- standing in for any
        such guard -- and confirm push_changes is genuinely blocked by it, the
        same way a raw ``git push`` already was before this fix."""
        import stat

        config, wid, wt_path, _remote_dir = published_pr_repo

        # Install a real client-side pre-push hook in the anchor's common git
        # dir (shared by every worktree, including wt_path) that always
        # refuses -- standing in for a real release guard.
        common_dir = Path(_git("rev-parse", "--git-common-dir", cwd=wt_path))
        if not common_dir.is_absolute():
            common_dir = wt_path / common_dir
        hooks_dir = common_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_path = hooks_dir / "pre-push"
        hook_path.write_text(
            "#!/bin/sh\n"
            "echo 'BLOCKED: missing changefile (simulated release guard)' >&2\n"
            "exit 1\n"
        )
        hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC)

        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)

        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        from agent_worktrees import finalize as fin
        ok = fin.push_changes(wid, config)
        assert ok is False  # the hook must actually block the push

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after == before  # remote feature branch did NOT advance

    def test_push_changes_blocks_a_leaking_recorded_branch(self, published_pr_repo):
        # pr-attribution-codenames Phase 5 (round 2): create-pr's guard only
        # covers the branch IT chooses -- a leaking name recorded some other
        # way (e.g. `set-pr --branch`) must still be blocked when
        # push-changes later republishes `record.pr.branch` directly.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _remote_dir = published_pr_repo

        leaking_branch = f"worktree/{wid}"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.branch = leaking_branch
        tracking.save_record(rec)

        # push-changes' snapshot-mode "on worktree/<id>" path resolves the
        # branch to (re)snapshot from the active PR's recorded branch --
        # simulate the ordinary post-create-pr state (HEAD back on the
        # worktree branch with a feedback commit) rather than checking out
        # the leaking name itself.
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is False
        assert not git_ops.remote_branch_exists(
            "origin", leaking_branch, cwd=str(wt_path)
        )

    def test_push_changes_blocks_recorded_machine_after_rename(self, published_pr_repo):
        # config.machine (live) vs record.machine (frozen) -- push-changes
        # must check both, exactly like create_pr (review round 3).
        import dataclasses
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _remote_dir = published_pr_repo

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.machine == "test"
        rec.pr.branch = "user/test/reused-head"
        tracking.save_record(rec)

        renamed_config = dataclasses.replace(config, machine="new-machine-name")
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, renamed_config)
        assert ok is False
        assert not git_ops.remote_branch_exists(
            "origin", "user/test/reused-head", cwd=str(wt_path)
        )

    def test_push_changes_rejects_wrong_branch(self, published_pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = published_pr_repo
        # HEAD on worktree/<id> (or a tracked feature branch) is valid now, so
        # push-changes only refuses a genuinely unrelated branch.
        _git("checkout", "-b", "unrelated-branch", cwd=wt_path)
        ok = fin.push_changes(wid, config)
        assert ok is False

    def test_push_changes_refspec_updates_head_ref(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        # Refspec: HEAD stayed on the worktree branch; PR head is a remote ref.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        before = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        anchor = Path(config.repos["ext"].anchor)
        _git("checkout", "master", cwd=anchor)
        (anchor / "upstream.txt").write_text("unrelated upstream advance\n")
        _git("add", "-A", cwd=anchor)
        _git("commit", "-m", "advance upstream", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

        # A feedback commit lands directly on worktree/<id> -- no checkout needed.
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True

        after = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote PR head advanced
        assert _git(
            "merge-base", before, "origin/pr/add-feature-aaaa", cwd=wt_path,
        ) == before
        # HEAD never left the worktree branch; the head ref is its tip.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == f"worktree/{wid}"
        assert _git("rev-parse", f"worktree/{wid}", cwd=wt_path) == \
            _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_sha == _git("rev-parse", "HEAD", cwd=wt_path)
        assert rec.pr.state == "open"

    def test_push_changes_refspec_refuses_divergent_remote_head(self, pr_repo):
        """Refspec-scheme counterpart of
        test_push_changes_refuses_divergent_remote_feature_branch above (#5298)."""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        config = self._refspec_config(config)
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"], first

        other = remote_dir.parent / "other-clone-push-changes-refspec"
        _git("clone", str(remote_dir), str(other), cwd=remote_dir.parent)
        _git("config", "user.email", "other@example.com", cwd=other)
        _git("config", "user.name", "Other", cwd=other)
        _git(
            "checkout", "-B", "pr/add-feature-aaaa", "origin/pr/add-feature-aaaa",
            cwd=other,
        )
        (other / "remote.txt").write_text("other actor\n")
        _git("add", "-A", cwd=other)
        _git("commit", "-m", "remote update", cwd=other)
        _git("push", "origin", "pr/add-feature-aaaa", cwd=other)
        remote_head = _git("rev-parse", "origin/pr/add-feature-aaaa", cwd=other)

        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)

        assert ok is False
        assert _git(
            "rev-parse", "origin/pr/add-feature-aaaa", cwd=wt_path
        ) == remote_head  # not overwritten

    def test_push_changes_refspec_rejects_wrong_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        # Move HEAD off the worktree branch -- refspec push-changes must refuse.
        _git("checkout", "-b", "sidebar", cwd=wt_path)
        ok = fin.push_changes(wid, config)
        assert ok is False

    @pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
    def test_push_changes_updates_a_fork_headed_pr_on_the_fork(self, pr_repo, scheme):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        if scheme == "refspec":
            config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        fork_dir = self._move_head_to_fork(wt_path, remote_dir, branch)
        config = self._fork_config(config)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        assert fin.push_changes(wid, config) is True

        tip = _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        on_fork = git_ops.git("--git-dir", str(fork_dir), "rev-parse", f"refs/heads/{branch}")
        assert on_fork.stdout.strip() == tip  # the PR moved
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)  # no stray copy
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_sha == tip
        assert rec.pr.remote == "fork"  # found once, recorded for the next push

    @pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
    def test_an_unreachable_recorded_fork_fails_push_changes_cleanly(self, pr_repo, monkeypatch, scheme):
        """The recorded fork is chosen from local identity alone; when it can't be
        fetched (gone, auth, timeout), push-changes returns False -- nothing is
        rebased or pushed -- instead of raising."""
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _fork_dir, _branch = self._fork_headed_rerun(pr_repo)
        if scheme == "refspec":
            config = self._refspec_config(config)
        real_fetch = git_ops.fetch

        def fetch(remote, *a, **k):
            if remote == "fork":
                raise git_ops.GitError(["fetch", remote], 128, "fatal: could not read from remote repository")
            return real_fetch(remote, *a, **k)

        pushed = []
        monkeypatch.setattr(git_ops, "fetch", fetch)
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or git_ops.PushResult(ok=True))
        assert fin.push_changes(wid, config) is False
        assert pushed == []

    def test_push_changes_refspec_blocks_a_leaking_recorded_branch(self, pr_repo):
        # Same guard as the snapshot-mode test above, exercised on the
        # refspec publish path.
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")

        leaking_branch = f"worktree/{wid}"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.branch = leaking_branch
        tracking.save_record(rec)

        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is False
        assert not git_ops.remote_branch_exists(
            "origin", leaking_branch, cwd=str(wt_path)
        )

    def test_push_changes_refspec_dirty_refused(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        config = self._refspec_config(config)
        pr_ops.create_pr(wid, config, title="Add feature")
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        ok = fin.push_changes(wid, config)
        assert ok is False

    def test_push_changes_refuses_direct_to_master(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        req_config = self._required_config(config)

        before = _git("ls-remote", str(remote_dir), "master", cwd=wt_path)
        # No create-pr was run -> no PR record -> direct push must be refused.
        ok = fin.push_changes(wid, req_config)
        assert ok is False
        after = _git("ls-remote", str(remote_dir), "master", cwd=wt_path)
        assert after == before  # remote master untouched

    def test_create_pr_path_still_works_when_required(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        req_config = self._required_config(config)
        # The PR path remains available: create-pr then push-changes updates
        # the feature branch, never master.
        pr_ops.create_pr(wid, req_config, title="Add feature")
        # create-pr returns HEAD to the base branch (#1804); check out the
        # feature branch to add a feedback commit that push-changes pushes.
        _git("checkout", "feature/add-feature-aaaa", cwd=wt_path)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        ok = fin.push_changes(wid, req_config)
        assert ok is True
