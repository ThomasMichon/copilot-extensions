"""PR publication target identity, remote resolution, locking, and reruns."""

from __future__ import annotations

import pytest
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking
from pr_test_helpers import PRWorkflowSetup, _git

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.publication")


class TestPRPublication(PRWorkflowSetup):
    def _branch_on_both(self, config, wid, wt_path, remote_dir):
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{rec.pr.branch}:refs/heads/{rec.pr.branch}",
             cwd=wt_path)
        return rec

    def test_a_branch_on_both_remotes_goes_where_the_prs_head_is(self, published_pr_repo, monkeypatch):
        """The state the old push-changes left behind: create-pr published the PR
        to the fork, a later push recreated its branch on origin and moved it. The
        provider's PR head (still the fork's tip) picks the fork."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = self._branch_on_both(config, wid, wt_path, remote_dir)
        pr_head = _git("rev-parse", f"refs/remotes/origin/{rec.pr.branch}", cwd=wt_path)
        (wt_path / "stray.txt").write_text("pushed to origin by the old push-changes\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "stray push", cwd=wt_path)
        _git("push", "origin", f"HEAD:refs/heads/{rec.pr.branch}", cwd=wt_path)
        repo = self._fork_config(config).repos["ext"]
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: pr_head)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "fork"
        origin_tip = _git("rev-parse", "HEAD", cwd=wt_path)
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: origin_tip)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "origin"

    def test_a_branch_on_both_remotes_without_a_deciding_head_refuses(self, published_pr_repo, monkeypatch):
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = self._branch_on_both(config, wid, wt_path, remote_dir)
        config = self._fork_config(config)
        repo = config.repos["ext"]
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: "")  # unreadable
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        same = _git("rev-parse", f"refs/remotes/origin/{rec.pr.branch}", cwd=wt_path)
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: same)  # both match
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        assert fin.push_changes(wid, config) is False

    def test_without_a_fork_remote_the_repo_remote_is_used(self, published_pr_repo):
        from agent_worktrees import pr_publish
        config, wid, wt_path, _ = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "origin"

    def test_an_unreadable_fork_remote_refuses_rather_than_guessing(self, published_pr_repo):
        """A fork that can't be read might hold the PR's head: pushing to origin
        instead would recreate the stray branch. Nothing is pushed."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        _git("remote", "add", "fork", str(remote_dir.parent / "no-such-fork.git"), cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        config = self._fork_config(config)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        assert fin.push_changes(wid, config) is False
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    def test_a_disabled_fork_config_ignores_a_local_fork_remote(self, published_pr_repo):
        """`pr.fork.remote` defaults to "fork" even when forking is off: an
        unrelated (here unreachable) local `fork` remote must not stop a plain
        repo's push-changes."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        _git("remote", "add", "fork", str(remote_dir.parent / "no-such-fork.git"), cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "origin"
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        assert fin.push_changes(wid, config) is True

    def test_a_recorded_fork_remote_that_is_gone_refuses(self, published_pr_repo):
        """The PR was published to a fork (recorded at create-pr); that remote
        was renamed or removed since. Its updates must never fall back to origin."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        self._move_head_to_fork(wt_path, remote_dir, branch)
        rec.pr.remote = "fork"
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.remote == "fork"  # round-trips through the record
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "fork"
        _git("remote", "remove", "fork", cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        assert fin.push_changes(wid, config) is False
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    def test_a_legacy_fork_pr_whose_configured_fork_remote_is_gone_refuses(self, published_pr_repo):
        """A PR recorded before its remote was kept, published to the configured
        fork, whose `fork` remote was removed or renamed since: falling back to
        origin would recreate the stray branch, so nothing is pushed."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        self._move_head_to_fork(wt_path, remote_dir, branch)
        rec.pr.remote = ""
        rec.pr.head_repo = ""
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        config = self._fork_config(config)
        _git("remote", "remove", "fork", cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        assert fin.push_changes(wid, config) is False
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    @staticmethod
    def _blocked_while(wt_path):
        """Start a stand-in for another worktree's fork setup (it takes the
        publication lock to repoint): True when it's still waiting."""
        import threading

        from agent_worktrees import pr_publish

        def repoint():
            with pr_publish.publish_lock(str(wt_path)):
                pass

        waiter = threading.Thread(target=repoint, daemon=True)
        waiter.start()
        waiter.join(0.3)
        return waiter.is_alive()

    def test_a_legacy_targets_tips_and_identity_are_one_locked_snapshot(self, published_pr_repo, monkeypatch):
        """A PR with no recorded remote is resolved by comparing branch tips, then
        reading the chosen fork's identity: a repoint can't land between them."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        self._move_head_to_fork(wt_path, remote_dir, rec.pr.branch)
        rec.pr.remote = ""
        rec.pr.head_repo = ""
        config = self._fork_config(config)
        real_tip = pr_publish._tip
        seen = []

        def tip(remote, branch, cwd):
            seen.append(self._blocked_while(wt_path))
            return real_tip(remote, branch, cwd)

        monkeypatch.setattr(pr_publish, "_tip", tip)
        target = pr_publish.push_target(config.repos["ext"], rec.pr, str(wt_path))
        assert target is not None and target.remote == "fork"
        assert target.head_identity == pr_publish.push_identity("fork", cwd=str(wt_path))
        assert seen and all(seen)

    def test_retiring_a_pruned_pr_reads_its_remote_and_branch_in_one_locked_snapshot(
            self, published_pr_repo, monkeypatch):
        """create-pr marks a live PR merged once its branch is gone from the remote
        holding it: a repoint between choosing that remote and checking the branch
        would find it "absent" on another fork and retire a live PR."""
        config, wid, wt_path, _remote_dir = published_pr_repo
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        from agent_worktrees import pr_publish
        real_tip = pr_publish._tip
        seen = []

        def tip(remote, branch, cwd):
            seen.append(self._blocked_while(wt_path))
            return real_tip(remote, branch, cwd)

        monkeypatch.setattr(pr_publish, "_tip", tip)
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        assert seen and all(seen)

    def test_a_deleted_head_is_gone_even_beside_an_archived_branch_of_the_same_name(self, published_pr_repo):
        """`ls-remote <branch>` is a tail glob: `archive/<branch>` must not keep
        a deleted PR head looking present to the retirement check."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, _remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        repo = config.repos["ext"]
        assert not pr_publish.head_branch_gone(repo, rec.pr, str(wt_path))
        _git("push", "origin", f"refs/remotes/origin/{branch}:refs/heads/archive/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        assert pr_publish.head_branch_gone(repo, rec.pr, str(wt_path))

    def test_the_publication_lock_is_reentrant_within_a_thread(self, published_pr_repo, monkeypatch):
        from concurrent.futures import ThreadPoolExecutor

        from agent_worktrees import pr_publish
        config, wid, wt_path, _remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_ACQUIRE_TIMEOUT_S", 0.05)
        with pr_publish.publish_lock(str(wt_path)):
            assert pr_publish.push_target(config.repos["ext"], rec.pr, str(wt_path)) is not None
            with pr_publish.publish_lock(str(wt_path)):
                pass
        def acquire_after_release():
            with pr_publish.publish_lock(str(wt_path)):
                return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(acquire_after_release).result(timeout=30)

    def test_a_missing_fork_remote_decides_nothing_even_when_origin_matches(self, published_pr_repo, monkeypatch):
        """A legacy PR whose configured fork remote is gone: origin holding the
        provider's head SHA isn't proof the head lives there (a stray copy can
        hold the same commit), so nothing is pushed until the remote is back."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = ""
        repo = self._fork_config(config).repos["ext"]  # fork configured, no `fork` remote here
        origin_tip = _git("rev-parse", f"refs/remotes/origin/{rec.pr.branch}", cwd=wt_path)
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: origin_tip)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: "")  # unreadable
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        monkeypatch.setattr(pr_publish, "_provider_head", lambda repo, pr: "0" * 40)  # elsewhere
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None

    def test_a_fork_remote_without_the_branch_resolves_a_usable_target(self, published_pr_repo):
        """The fork is present but doesn't hold the branch: origin, as a target."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = ""
        fork_dir = remote_dir.parent / "empty-fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        repo = self._fork_config(config).repos["ext"]
        target = pr_publish.push_target(repo, rec.pr, str(wt_path))
        assert isinstance(target, pr_publish.PushTarget) and target.remote == "origin"
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "origin"

    def test_the_reusing_fast_path_forwards_the_forks_identity(self, published_pr_repo, monkeypatch):
        """A live PR re-run with nothing new to squash re-pushes its feature branch;
        that push must carry the fork's immutable identity like the other re-run."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, _ = published_pr_repo
        seen = {}
        monkeypatch.setattr(pr_publish, "update_remote",
                            lambda *a, **k: ("fork", "alice", "alice/ext", "github.com/alice/ext"))
        # The identity is the one read when the target was chosen; a later read of the shared
        # remote (another worktree may have repointed it) must never replace it.
        monkeypatch.setattr(pr_publish, "push_identity", lambda *a, **k: "elsewhere.example/alice/ext")
        monkeypatch.setattr(git_ops, "get_commits_ahead", lambda *a, **k: [])
        monkeypatch.setattr(pr_ops, "_push_existing_feature",
                            lambda *a, **k: seen.update(k) or {"success": True})
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        assert seen.get("fork_head_repo") == "alice/ext"
        assert seen.get("fork_head_identity") == "github.com/alice/ext"
        assert seen.get("pr_head", "").startswith("alice:")

    def test_create_pr_rerun_updates_a_fork_headed_pr_on_its_fork(self, pr_repo, monkeypatch):
        """Today's config resolves origin, but the live PR's head is on the fork
        it was published to: the re-squashed head goes there, stays recorded, and
        the PR head is the fork owner's (so a not-yet-opened PR opens from it)."""
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert result.get("success"), result
        assert result["remote"] == "fork"
        assert result["pr_head"] == f"alice:{branch}"
        on_fork = git_ops.git("--git-dir", str(fork_dir), "rev-parse", f"refs/heads/{branch}")
        assert on_fork.stdout.strip() == result["head_sha"]
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert (rec.pr.remote, rec.pr.head_repo) == ("fork", "alice/ext")

    def test_a_fork_repointed_to_the_same_slug_on_another_host_refuses(self, pr_repo, monkeypatch):
        """`owner/name` alone isn't the fork: the same slug on another host (here,
        another destination the slug check can't tell apart) is another repository.
        The recorded host/owner/name identity refuses it, both when choosing the
        remote and under the publication lock."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_repo == "alice/ext" and rec.pr.head_identity  # recorded, with its host
        other = fork_dir.parent / "same-slug-elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) is None
        pushed = []
        real_push = git_ops.push
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or real_push(*a, **k))
        result = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path))
        assert not result and result.stderr == pr_publish.REPOINTED and pushed == []
        (wt_path / "d.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "more feedback", cwd=wt_path)
        assert fin.push_changes(wid, config) is False
        assert pushed == []

    def test_a_recorded_fork_remote_repointed_elsewhere_refuses(self, pr_repo, monkeypatch):
        """Fork setup repoints `fork` when the identity changes: a remote that now
        names another repo than the PR's head repo isn't the PR's fork."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, _fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_repo = "alice/ext"
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.head_repo == "alice/ext"  # round-trips through the record
        repo = config.repos["ext"]
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: "Alice/Ext")
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "fork"
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: "bob/ext")
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert result.get("error") == pr_publish.UNREADABLE_REMOTE
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)

    def test_a_fork_remote_whose_push_url_names_another_repo_refuses(self, pr_repo):
        """`git push fork` honors `remote.fork.pushurl`: a fork fetched from the
        PR's head repo but pushing to another one would publish the update (and
        lease it on a tip read) elsewhere. Recorded or discovered, it's refused."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        other = fork_dir.parent / "other.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_repo = git_ops.remote_slug("fork", cwd=wt_path)
        repo = self._fork_config(config).repos["ext"]
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "fork"
        _git("remote", "set-url", "--push", "fork", str(other), cwd=wt_path)
        assert pr_publish.push_slug("fork", cwd=wt_path) is None
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        rec.pr.remote = rec.pr.head_repo = ""  # an older record: found from the fork's tip
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        tracking.save_record(rec)
        assert fin.push_changes(wid, self._fork_config(config)) is False
        assert not git_ops.git("--git-dir", str(other), "rev-parse", "--verify", "-q",
                               f"refs/heads/{branch}", check=False).stdout.strip()

    def test_a_fork_repointed_after_it_was_chosen_is_refused_at_push_time(self, pr_repo, monkeypatch):
        """Another worktree's fork setup repoints the shared remote between
        resolution and push: the push re-checks under the publication lock,
        and repointing takes that same lock, so it can't land mid-push."""
        import threading

        from agent_worktrees import pr_publish
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_repo = pr_publish.push_slug("fork", cwd=str(wt_path))
        rec.pr.head_identity = pr_publish.push_identity("fork", cwd=str(wt_path))  # every fork push knows it
        pushed = []

        def fake_push(remote, refspec, **kw):
            def repoint():  # stands in for another worktree's fork setup
                with pr_publish.publish_lock(str(wt_path)):
                    pass

            waiter = threading.Thread(target=repoint, daemon=True)
            waiter.start()
            waiter.join(0.5)
            pushed.append((remote, refspec, waiter.is_alive()))  # it's still waiting for the lock
            return git_ops.PushResult(ok=True)

        monkeypatch.setattr(git_ops, "push", fake_push)
        assert pr_publish.push_checked(rec, "fork", branch, cwd=str(wt_path))
        assert pushed == [("fork", branch, True)]
        other = fork_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)  # repointed since it was chosen
        result = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path))
        assert not result and result.stderr == pr_publish.REPOINTED
        assert len(pushed) == 1

    def test_a_fresh_fork_push_repointed_before_the_lock_is_refused(
        self, pr_repo, monkeypatch,
    ):
        """A fork target chosen for a fresh/unrecorded PR must still be checked
        under the publication lock; an empty recorded head_repo is not a bypass."""
        from agent_worktrees import pr_publish
        _config, _wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{_wid}.yaml")
        expected = pr_publish.push_slug("fork", cwd=str(wt_path))
        other = fork_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)
        pushed = []
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or git_ops.PushResult(ok=True))

        result = pr_publish.push_checked(
            rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path),
            expected_head_repo=expected,
        )

        assert not result and result.stderr == pr_publish.REPOINTED
        assert pushed == []

    def test_a_fresh_fork_target_is_checked_by_its_full_identity_under_the_lock(self, pr_repo, monkeypatch):
        """A fresh/legacy target has no recorded identity: the one read when the target
        was chosen is required, so a same-slug repoint before the lock is refused, and
        a fork push with no identity at all is refused too."""
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        _config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = rec.pr.head_repo = rec.pr.head_identity = ""  # fresh/legacy: nothing recorded
        chosen = pr_publish.push_identity("fork", cwd=str(wt_path))
        other = fork_dir.parent / "same-slug-elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "fork", str(other), cwd=wt_path)  # repointed after it was chosen
        pushed = []
        monkeypatch.setattr(git_ops, "push", lambda *a, **k: pushed.append(a) or git_ops.PushResult(ok=True))
        for identity in (chosen, ""):
            result = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path),
                                             expected_head_repo="alice/ext", expected_head_identity=identity)
            assert not result and result.stderr == pr_publish.REPOINTED, identity
        assert pushed == []
        now = pr_publish.push_identity("fork", cwd=str(wt_path))
        ok = pr_publish.push_checked(rec, "fork", f"HEAD:refs/heads/{branch}", cwd=str(wt_path),
                                     expected_head_repo="alice/ext", expected_head_identity=now)
        assert ok and ok.head_identity == now and len(pushed) == 1

    def test_a_plaintext_push_url_is_never_the_same_destination_as_a_secure_fetch_url(self, tmp_path):
        """git_ops.push's credential header is built for the fetch URL: an http:// (or
        git://) push URL with the same host/path would carry it over plaintext."""
        from agent_worktrees import pr_publish
        _git("init", "-q", str(tmp_path), cwd=tmp_path)
        _git("remote", "add", "r", "https://github.com/alice/ext.git", cwd=tmp_path)
        for plain in ("http://github.com/alice/ext.git", "git://github.com/alice/ext.git"):
            _git("remote", "set-url", "--push", "r", plain, cwd=tmp_path)
            assert pr_publish.push_slug("r", cwd=str(tmp_path)) is None, plain
        _git("remote", "set-url", "--push", "r", "git@github.com:alice/ext.git", cwd=tmp_path)
        assert pr_publish.push_slug("r", cwd=str(tmp_path)) == "alice/ext"  # ssh and https still agree

    def test_the_repo_remote_fallbacks_refuse_a_push_url_naming_another_repo(self, pr_repo):
        """The repo's own remote is the target of a PR with no fork configured, and of
        one no configured fork holds: like a fork, it must push where it fetches from,
        since the push's lease and credential come from the fetch URL."""
        from agent_worktrees import finalize as fin
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = pr_repo
        assert pr_ops.create_pr(wid, config, title="Add feature").get("success")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo, fork_repo = config.repos["ext"], self._fork_config(config).repos["ext"]
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)  # configured, without the branch
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == repo.remote
        assert pr_publish.push_remote(fork_repo, rec.pr, str(wt_path)) == repo.remote
        other = remote_dir.parent / "elsewhere.git"
        _git("init", "--bare", "-b", "master", str(other), cwd=wt_path)
        _git("remote", "set-url", "--push", repo.remote, str(other), cwd=wt_path)
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) is None
        assert pr_publish.push_remote(fork_repo, rec.pr, str(wt_path)) is None
        (wt_path / "e.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "more", cwd=wt_path)
        assert fin.push_changes(wid, config) is False
        assert not git_ops.git("--git-dir", str(other), "rev-parse", "--verify", "-q",
                               f"refs/heads/{rec.pr.branch}", check=False).stdout.strip()

    def test_an_explicit_fork_owner_survives_a_rerun(self, pr_repo, monkeypatch):
        """The PR head's owner recorded at publish (an explicit pr.fork.owner) wins over
        the fork repository's owner on a rerun, e.g. after opening was deferred."""
        from agent_worktrees import pr_publish
        real_slug = pr_publish.push_slug
        monkeypatch.setattr(pr_publish, "push_slug", lambda r, *, cwd: (
            "alice/ext" if r == "fork" else real_slug(r, cwd=cwd)))
        config, wid, wt_path, _fork_dir, branch = self._fork_headed_rerun(pr_repo)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.head_owner = "alice-org"
        tracking.save_record(rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        where, owner, slug, _identity = pr_publish.update_remote(config.repos["ext"], rec, branch, str(wt_path), "origin")
        assert (where, owner, slug) == ("fork", "alice-org", "alice/ext")

    def test_a_push_url_must_name_the_same_host_and_repo_as_the_fetch_url(self, tmp_path):
        """Same ``owner/name`` on another host is another destination; a repo's
        ssh and https forms are the same one."""
        from agent_worktrees import pr_publish
        _git("init", "-q", str(tmp_path), cwd=tmp_path)
        _git("remote", "add", "r", "https://github.com/alice/ext.git", cwd=tmp_path)
        assert pr_publish.push_slug("r", cwd=str(tmp_path)) == "alice/ext"
        for same in ("git@github.com:alice/ext.git", "ssh://git@github.com/Alice/ext",
                     "https://user@github.com/alice/ext/", "https://github.com:443/alice/ext",
                     "ssh://git@github.com:22/alice/ext.git"):
            _git("remote", "set-url", "--push", "r", same, cwd=tmp_path)
            assert pr_publish.push_slug("r", cwd=str(tmp_path)) == "alice/ext", same
        for other in ("https://gitlab.example.com/alice/ext.git", "git@example.com:alice/ext.git",
                      "https://github.com:8443/alice/ext.git", "ssh://git@github.com:2222/alice/ext",
                      str(tmp_path / "alice" / "ext.git")):
            _git("remote", "set-url", "--push", "r", other, cwd=tmp_path)
            assert pr_publish.push_slug("r", cwd=str(tmp_path)) is None, other
        _git("remote", "add", "l", str(tmp_path / "forks" / "ext.git"), cwd=tmp_path)
        assert pr_publish.push_slug("l", cwd=str(tmp_path)) is not None
        _git("remote", "set-url", "--push", "l", str(tmp_path / "other" / "forks" / "ext.git"),
             cwd=tmp_path)
        assert pr_publish.push_slug("l", cwd=str(tmp_path)) is None  # same tail, another repo

    def test_file_url_identity_preserves_non_local_authority(self, tmp_path):
        """UNC-style file authorities are part of the destination identity."""
        from agent_worktrees import pr_publish
        cwd = str(tmp_path)

        assert pr_publish._identity("file:///x", cwd) == pr_publish._identity(
            "file://localhost/x", cwd,
        )
        assert pr_publish._identity("file://SERVER-A/share/repo.git", cwd) == (
            pr_publish._identity("file://server-a/share/repo.git", cwd)
        )
        assert pr_publish._identity("file://server-a/share/repo.git", cwd) != (
            pr_publish._identity("file://server-b/share/repo.git", cwd)
        )

    def test_publication_lock_timeout_is_controlled(self, tmp_path, monkeypatch):
        """A contended publication lock fails as a PushResult, not a traceback."""
        import subprocess
        import sys
        import textwrap
        import time

        from agent_worktrees import pr_publish, push_timeout

        # A holder's push may retry once without its auth override, each attempt bounded.
        assert pr_publish.PUBLISH_LOCK_ACQUIRE_TIMEOUT_S > 2 * push_timeout.DEFAULT_PUSH_TIMEOUT
        _git("init", "-q", str(tmp_path), cwd=tmp_path)
        marker = tmp_path / "locked"
        script = textwrap.dedent(
            """
            import sys, time
            from pathlib import Path
            from agent_worktrees import pr_publish

            with pr_publish.publish_lock(sys.argv[1]):
                Path(sys.argv[2]).write_text("locked", encoding="utf-8")
                time.sleep(5)
            """
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not marker.exists() and holder.poll() is None:
                time.sleep(0.01)
            assert marker.exists(), holder.communicate(timeout=1)
            monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_ACQUIRE_TIMEOUT_S", 0.05)
            monkeypatch.setattr(pr_publish, "PUBLISH_LOCK_RETRY_INTERVAL_S", 0.01)

            result = pr_publish.push_checked(
                None, "origin", "HEAD:refs/heads/main", cwd=str(tmp_path),
            )

            assert not result
            assert "Timed out waiting 0.05s for the PR publication lock" in result.stderr
            assert "Nothing was pushed." in result.stderr
        finally:
            if holder.poll() is None:
                holder.terminate()
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=5)

    def test_create_pr_rerun_never_overwrites_a_fork_head_another_checkout_pushed(self, pr_repo):
        """The fork head moved since this checkout published it (another checkout
        pushed): the re-squash's push is leased against the PR tip this checkout
        last observed (#5298), so it's refused and the other push survives."""
        config, wid, wt_path, fork_dir, branch = self._fork_headed_rerun(pr_repo)
        other = wt_path.parent / "second-checkout"
        _git("clone", "-q", "-b", branch, str(fork_dir), str(other), cwd=wt_path.parent)
        _git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q",
             "--allow-empty", "-m", "pushed from another checkout", cwd=other)
        _git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=other)
        theirs = _git("rev-parse", "HEAD", cwd=other)
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert not result.get("success") and "Failed to push" in result.get("error", ""), result
        on_fork = git_ops.git("--git-dir", str(fork_dir), "rev-parse", f"refs/heads/{branch}")
        assert on_fork.stdout.strip() == theirs

    def test_create_pr_rerun_refuses_when_the_recorded_fork_is_gone(self, pr_repo):
        from agent_worktrees import pr_publish
        config, wid, wt_path, _fork_dir, branch = self._fork_headed_rerun(pr_repo)
        _git("remote", "remove", "fork", cwd=wt_path)
        result = pr_ops.create_pr(wid, config, title="Add feature")
        assert result.get("error") == pr_publish.UNREADABLE_REMOTE
        assert not _git("ls-remote", "--heads", "origin", branch, cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr.remote == "fork"

    def test_a_probe_matches_the_exact_branch_not_a_suffix(self, published_pr_repo):
        """ls-remote patterns are tail globs: a fork holding only
        `archive/<branch>` doesn't hold the PR's branch."""
        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/archive/{branch}",
             cwd=wt_path)
        assert pr_publish._tip("fork", branch, str(wt_path)) == ""
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        tip = _git("rev-parse", f"refs/remotes/origin/{branch}", cwd=wt_path)
        assert pr_publish._tip("fork", branch, str(wt_path)) == tip

    def test_a_probe_that_times_out_counts_as_unreadable(self, pr_repo, monkeypatch):
        import subprocess

        from agent_worktrees import pr_publish
        config, wid, wt_path, _ = pr_repo

        real_git = pr_publish.git_ops.git

        def hang(*args, **kw):
            if "ls-remote" not in args:
                return real_git(*args, **kw)
            assert kw.get("timeout") == pr_publish.PROBE_TIMEOUT
            raise subprocess.TimeoutExpired(args, kw["timeout"])

        monkeypatch.setattr(pr_publish.git_ops, "git", hang)
        assert pr_publish._tip("fork", "some-branch", str(wt_path)) is None

    def test_a_role_overrides_fork_remote_is_found_too(self, published_pr_repo):
        """create-pr publishes through the role-resolved fork remote, which a
        role override can rename: push-changes must look there as well."""
        import dataclasses

        from agent_worktrees import pr_publish
        config, wid, wt_path, remote_dir = published_pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        branch = rec.pr.branch
        fork_dir = remote_dir.parent / "myfork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "myfork", str(fork_dir), cwd=wt_path)
        _git("push", "myfork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        repo = config.repos["ext"]
        role = cfg.PRRoleOverride(fork=cfg.ForkConfig(enabled=True, remote="myfork"))
        repo = dataclasses.replace(repo, pr=dataclasses.replace(repo.pr, roles={"write": role}))
        assert pr_publish.push_remote(repo, rec.pr, str(wt_path)) == "myfork"
        assert pr_publish.push_remote(config.repos["ext"], rec.pr, str(wt_path)) == "origin"
