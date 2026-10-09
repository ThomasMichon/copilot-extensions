"""Non-collected setup helpers for the PR behavioral-contract tests."""

from __future__ import annotations

from pathlib import Path
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking


def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


class PRWorkflowSetup:
    """Shared PR scenarios; contains helpers only, never collected tests."""

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

    def _move_head_to_fork(self, wt_path, remote_dir, branch):
        """The PR's head branch lives only on a fork (as the fork-PR flow
        publishes it): a bare 'fork' remote has it, origin doesn't."""
        fork_dir = remote_dir.parent / "fork.git"
        _git("init", "--bare", "-b", "master", str(fork_dir), cwd=wt_path)
        _git("remote", "add", "fork", str(fork_dir), cwd=wt_path)
        _git("push", "fork", f"refs/remotes/origin/{branch}:refs/heads/{branch}", cwd=wt_path)
        _git("push", "origin", "--delete", branch, cwd=wt_path)
        return fork_dir

    def _fork_headed_rerun(self, pr_repo):
        """A live PR published to a fork (recorded), under a config that no
        longer forks, with a new commit for create-pr to re-squash."""
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        fork_dir = self._move_head_to_fork(wt_path, remote_dir, rec.pr.branch)
        rec.pr.remote = "fork"
        tracking.save_record(rec)
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        return config, wid, wt_path, fork_dir, rec.pr.branch

    def _required_config(self, config):
        """``pr.required`` blocks the direct-to-master path entirely."""
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
