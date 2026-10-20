"""Incremental publication to a remote-only private PR head."""

from __future__ import annotations

from pathlib import Path

from . import activity, git_ops, hooks, output, pr_authority, publication_deadline
from . import push_diagnostics, tracking
from .config import Config


def push_refspec(
    worktree_id: str,
    config: Config,
    record: tracking.WorktreeRecord,
    wt_branch: str,
    worktree_path: str,
    lock_path: Path,
    *,
    dry_run: bool = False,
    reconciled_source: str = "",
) -> bool:
    """Publish the worktree tip without rebasing or touching the remote base."""
    from .finalize import FinalizeLock

    repo = config.default_repo
    remote = repo.remote

    head = git_ops._get_current_branch_safe(worktree_path)
    if head != wt_branch:
        output.err(
            f"PR mode (refspec): push-changes updates the PR head from "
            f"'{wt_branch}', but HEAD is on '{head}'. Checkout '{wt_branch}' "
            f"first."
        )
        return False

    # The PR head ref to update is the active (live) PR's branch.
    pushed_pr = record.active_pr() or record.pr
    feature = pushed_pr.branch if (pushed_pr and pushed_pr.branch) else ""
    if not feature:
        output.err("PR mode (refspec): no tracked PR head ref to update.")
        return False
    from . import pr_publish
    push_target = pr_publish.push_target(repo, pushed_pr, worktree_path)  # a fork-headed PR's fork
    if push_target is None:
        output.err(pr_publish.UNREADABLE_REMOTE)
        return False
    push_to = push_target.remote

    from .providers.attribution import BranchLeakError, validate_effective_head
    try:
        # Physical metadata and execution owners are independently private.
        validate_effective_head(
            feature, worktree_id=worktree_id,
            machine=(config.machine, record.machine, record.execution_machine),
            source_attribution=repo.pr.source_attribution,
        )
    except BranchLeakError as exc:
        output.err(str(exc))
        return False

    if not git_ops.is_clean(cwd=worktree_path):
        dirty = git_ops.get_dirty_files(cwd=worktree_path)
        detail = "\n".join(f"    {ln}" for ln in dirty)
        output.err(
            "Working tree has uncommitted changes. Commit them before "
            f"push-changes:\n{detail}"
        )
        return False

    if dry_run:
        print(
            f"[dry-run] Would push "
            f"{wt_branch}:refs/heads/{feature} to {push_to}."
        )
        return True

    budget = publication_deadline.lifecycle_budget(repo.pr)
    lock = pr_authority.PublicationLock(
        FinalizeLock(lock_path, timeout=budget, stale_after=budget), record, pushed_pr,
        timeout=publication_deadline.lock_wait(repo.pr.push_timeout_seconds),
    )
    try:
        lock.acquire()
    except (TimeoutError, ValueError) as exc:
        output.err(str(exc))
        return False

    try:
        print(f"Fetching from {remote}...")
        git_ops.fetch(remote, cwd=worktree_path)
        if push_to != remote:
            try:
                git_ops.fetch(push_to, cwd=worktree_path)
            except git_ops.GitError as exc:
                output.err(f"Couldn't fetch the PR's fork remote '{push_to}': {exc}. Nothing was pushed.")
                return False
        if record.repo:
            tracking.record_repo_fetch_confirmed(record.repo)

        lease_expect = pushed_pr.head_sha if pushed_pr is not None else ""
        if not lease_expect:
            output.err(push_diagnostics.missing_expected_sha_error(
                feature_branch=feature, retry_command="agent-worktrees push-changes"))
            return False
        with hooks.allow_pr_push():
            pushed = pr_publish.push_checked(
                record, push_to, f"{reconciled_source or wt_branch}:refs/heads/{feature}",
                timeout=publication_deadline.configured(repo.pr),
                cwd=worktree_path, expected_head_repo=push_target.head_repo, expected_head_identity=push_target.head_identity,
                force_with_lease_expect=None if reconciled_source else (lease_expect or None),
                force_with_lease=not bool(reconciled_source), repo=repo,
            )
        if not pushed:
            output.err(f"Failed to push {reconciled_source or wt_branch} to {push_to}/{feature}.")
            if pushed.retryable:
                output.err(
                    push_diagnostics.pr_branch_non_fast_forward_hint(
                        retry_command="agent-worktrees push-changes"
                    )
                )
            detail = push_diagnostics.push_failure_detail(pushed)
            if detail:
                output.err(detail.lstrip("\n"))
            if pushed_pr is not None and pushed_pr.state in ("", "creating"):
                tracking.save_record(record)
            return False

        head_sha = getattr(pushed, "published_head_sha", "") or reconciled_source or git_ops.git(
            "rev-parse", "HEAD", cwd=worktree_path, check=False
        ).stdout.strip()
        pr_publish.record_pushed_head(config, record, worktree_id, pushed_pr, head_sha,
                                      remote=push_to, head_repo=pushed.head_repo,
                                      head_identity=getattr(pushed, "head_identity", ""), rebase_base_sha=getattr(pushed, "rebase_base_sha", ""))

        activity.log_event(
            "pr_changes_pushed", worktree_id=worktree_id, branch=feature,
        )
        output.ok(pr_publish.push_history_message(pushed, feature, wt_branch))
        output.ok(
            f"Pushed {wt_branch} to {push_to}/{feature}. "
            f"The open PR is updated."
        )
        return True
    finally:
        lock.release()
