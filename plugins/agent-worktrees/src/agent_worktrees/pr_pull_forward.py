"""``pr-status``'s post-merge pull-forward advice, aware of the PR's own base.

Mechanical extraction out of ``pr_ops.py`` (module-size guard), plus one
behavior: a PR that merged into a branch other than the repo's configured
default one (a repo whose PRs land on ``dev`` while its default branch is
``main``) is measured against, and advised onto, that branch -- ``git sync``
rebases onto the configured default branch, so it isn't recommended there.
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops, tracking
from .config import Config
from .tracking import PRRecord


def _pr_base(active: PRRecord, repo) -> str:
    """The branch *active* merged into, from the provider; "" when unknown."""
    from . import finalize_open_pr_gate as fopg
    try:
        return fopg.pr_base_ref(active, repo)
    except Exception:
        return ""


def pull_forward_recommendation(
    record: tracking.WorktreeRecord,
    active: PRRecord,
    config: Config,
    *,
    live: bool = False,
) -> dict | None:
    """Recommend the post-merge pull-forward when the active PR has merged.

    Returns recommendation fields, or ``None`` when no nudge is warranted.
    Fires only when the active PR is **merged** and the worktree branch is not
    already rebased on top of the updated base branch -- i.e. there is real
    pull-forward work to do.  Best-effort and side-effect-free (a single
    upstream fetch aside): any git hiccup falls back to recommending, since the
    agent's ``git sync`` is a safe no-op when already current.

    With ``live``, the provider is asked which branch the PR merged into; when
    that isn't the configured default branch, the advice targets it instead
    (``git rebase <remote>/<base>``), with ``pull_forward_base`` naming it.
    """
    if active.state != "merged":
        return None
    path = record.worktree_path
    if not (path and Path(path).exists()):
        return None
    repo = config.default_repo
    remote = repo.remote
    base = _pr_base(active, repo) if live else ""
    other_base = bool(base) and base != repo.default_branch
    upstream = f"{remote}/{base if other_base else repo.default_branch}"
    command = f"git rebase {upstream}" if other_base else "agent-worktrees git sync"
    # Refresh the upstream ref so "behind" reflects the just-landed merge.
    if git_ops.has_remote(remote, cwd=path):
        try:
            git_ops.fetch(remote, cwd=path)
        except Exception:
            pass
        else:
            # worktree-finality-and-obligations Phase 9: a merged PR's
            # pull-forward check is exactly the "pr-merge" freshness trigger
            # -- share this fetch with every other worktree of the repo.
            if record.repo:
                tracking.record_repo_fetch_confirmed(record.repo)
    behind: int | None = None
    branch = git_ops._get_current_branch_safe(path)
    if branch and git_ops.ref_exists(upstream, cwd=path):
        out = git_ops.git(
            "rev-list", "--count", f"{branch}..{upstream}",
            cwd=path, check=False,
        ).stdout.strip()
        try:
            behind = int(out)
        except ValueError:
            behind = None
    # Already on top of the updated base branch -- nothing to pull forward.
    if behind == 0:
        return None
    rec: dict = {
        "pull_forward_recommended": True,
        "pull_forward_command": command,
    }
    if other_base:
        rec["pull_forward_base"] = upstream
    if behind:
        rec["behind"] = behind
    why = (f" It merged into {upstream}, not the configured default branch "
           f"{remote}/{repo.default_branch} that `agent-worktrees git sync` rebases onto."
           if other_base else "")
    if not git_ops.is_clean(cwd=path):
        rec["pull_forward_blocked"] = "dirty"
        rec["next_action"] = (
            f"Active PR #{active.number} is merged, but this worktree has "
            "uncommitted changes. Commit or stash them, then run "
            f"`{command}` to pull forward (rebase onto {upstream}).{why}"
        )
    else:
        rec["next_action"] = (
            f"Active PR #{active.number} is merged. Pull this worktree forward: "
            f"`{command}` (rebase onto {upstream}; the merged "
            f"commits drop as already-applied).{why}"
        )
    return rec
