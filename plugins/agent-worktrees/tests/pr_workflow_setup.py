"""Shared setup helpers for real-Git PR workflow integration tests."""

from __future__ import annotations

from pr_test_helpers import _git

from agent_worktrees import config as cfg
from agent_worktrees import pr_ops, tracking


class PRWorkflowSetup:
    def _refspec_config(self, config):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, head_scheme="refspec")
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def _fork_config(self, config):
        """pr.fork enabled (after create-pr, which then took the plain path)."""
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, fork=cfg.ForkConfig(enabled=True, remote="fork"))
        return dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})

    def _move_head_to_fork(self, wt_path, remote_dir, branch, record):
        """The PR's head branch lives only on a fork (as the fork-PR flow
        publishes it): a bare 'fork' remote has it, origin doesn't."""
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        rec = tracking.load_record(record.yaml_path)
        rec.pr.rewrite_owner = rec.pr.rewrite_identity = ""
        tracking.save_record(rec)
        return fork_dir

    def _branch_on_both(self, config, wid, wt_path, remote_dir):
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{rec.pr.branch}:refs/heads/{rec.pr.branch}",
             cwd=wt_path)
        return rec

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

    def _fork_headed_rerun(self, pr_repo):
        """A live PR published to a fork (recorded), under a config that no
        longer forks, with a new commit for create-pr to re-squash."""
        config, wid, wt_path, remote_dir = pr_repo
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = remote_dir.parent / "fork.git"
        if rec.pr is not None and rec.pr.remote == "fork" and fork_dir.is_dir():
            assert (wt_path / "c.txt").read_text() == "feedback\n"
            return config, wid, wt_path, fork_dir, rec.pr.branch
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = self._move_head_to_fork(wt_path, remote_dir, rec.pr.branch, rec)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.remote = "fork"
        tracking.save_record(rec)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        return config, wid, wt_path, fork_dir, rec.pr.branch

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

    def _required_config(self, config):
        repo = config.default_repo
        return cfg.Config(
            srcroot=config.srcroot, machine=config.machine,
            platform=config.platform, repo_name=config.repo_name,
            repos={config.repo_name: cfg.RepoConfig(
                anchor=repo.anchor, worktree_root=repo.worktree_root,
                default_branch=repo.default_branch, remote=repo.remote,
                pr=cfg.PRConfig(
                    enabled=True, required=True,
                    provider="gitea", branch_prefix="feature",
                    head_scheme="snapshot",
                ),
            )},
        )
