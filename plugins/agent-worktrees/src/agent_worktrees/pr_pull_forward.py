"""``pr-status``'s post-merge pull-forward advice, aware of the PR's own base.

Mechanical extraction out of ``pr_ops.py`` (module-size guard), plus one
behavior: a PR that merged into a branch other than the repo's configured
default one (a repo whose PRs land on ``dev`` while its default branch is
``main``) is measured against, and advised onto, that branch -- ``git sync``
rebases onto the configured default branch, so it isn't recommended there.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from . import git_ops, tracking
from .config import Config
from .tracking import PRRecord

#: A branch name safe to show inside a copy-pasteable command; any other one is
#: offered only as structured argv (``pull_forward_argv``).
_SAFE_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def _pr_base_and_head(active: PRRecord, repo) -> tuple[str, str]:
    """The branch *active* merged into and its merged head, from one provider
    read; ``("", "")`` when unknown, and no head unless the provider says merged."""
    from . import finalize_open_pr_gate as fopg
    try:
        pulled = fopg.pull_for(active, repo)
    except Exception:
        pulled = None
    if not pulled:
        return "", ""
    result = pulled[4]
    base = (getattr(result, "base_ref", "") or "").strip().removeprefix("refs/heads/")
    merged = bool(getattr(result, "merged", False)) or (getattr(result, "state", "") or "").lower() == "merged"
    return base, (getattr(result, "head_sha", "") or "").strip() if merged else ""


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

    With ``live``, the provider is asked which branch the PR merged into, and its
    merged head; when that isn't the configured default branch, the advice targets
    it instead: ``git -C <worktree> rebase --onto refs/remotes/<remote>/<base>
    <merged head>`` (also as structured ``pull_forward_argv``), which replays only
    the commits made after the merge -- replaying the PR's own commits onto their
    squash can conflict although all of it landed -- and simply moves the branch
    to the base when there are none. Offered only when the merged head is a
    verified ancestor of the worktree's HEAD; ``pull_forward_base`` names the base.
    A base name that isn't plainly shell-safe gets no command text, only the argv.
    """
    if active.state != "merged":
        return None
    path = record.worktree_path
    if not (path and Path(path).exists()):
        return None
    repo = config.default_repo
    remote = repo.remote
    base, merged_head = _pr_base_and_head(active, repo) if live else ("", "")
    other_base = bool(base) and base != repo.default_branch
    upstream = f"{remote}/{base if other_base else repo.default_branch}"
    # A provider-supplied name never reaches executable text unquoted, the advice runs
    # in the tracked worktree, and only commits after the verified merge boundary replay.
    bounded = bool(merged_head) and git_ops.git(
        "merge-base", "--is-ancestor", merged_head, "HEAD", cwd=path, check=False).returncode == 0
    argv = ["git", "-C", path, "rebase", "--onto", f"refs/remotes/{upstream}", merged_head] \
        if other_base and bounded else []
    command = (" ".join(shlex.quote(a) for a in argv) if argv and _SAFE_BRANCH.fullmatch(base) else "") \
        if other_base else "agent-worktrees git sync"
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
    if branch and git_ops.ref_exists(f"refs/remotes/{upstream}", cwd=path):
        out = git_ops.git(
            "rev-list", "--count", f"{branch}..refs/remotes/{upstream}",
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
        rec["pull_forward_argv"] = argv
    if behind:
        rec["behind"] = behind
    # An unsafe base name never appears inside advice people paste: only in the data fields.
    safe = not other_base or bool(_SAFE_BRANCH.fullmatch(base))
    onto = upstream if safe else "the PR's base branch"
    shown = command or ("the structured pull_forward_argv" if argv else
                        "a rebase that replays only the commits made after the merge "
                        "(the merged head isn't a verified ancestor of HEAD here, so none is generated)")
    why = (f" It merged into {onto}, not the configured default branch "
           f"{remote}/{repo.default_branch} that `agent-worktrees git sync` rebases onto."
           if other_base else "")
    if not git_ops.is_clean(cwd=path):
        rec["pull_forward_blocked"] = "dirty"
        rec["next_action"] = (
            f"Active PR #{active.number} is merged, but this worktree has "
            "uncommitted changes. Commit or stash them, then run "
            f"`{shown}` to pull forward (rebase onto {onto}).{why}"
        )
    else:
        rec["next_action"] = (
            f"Active PR #{active.number} is merged. Pull this worktree forward: "
            f"`{shown}` (rebase onto {onto}; the merged "
            f"commits drop as already-applied).{why}"
        )
    return rec
