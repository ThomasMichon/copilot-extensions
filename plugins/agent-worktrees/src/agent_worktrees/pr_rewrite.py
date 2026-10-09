"""Explicit publication of intentionally rewritten, exclusively owned PR history."""

from __future__ import annotations

import re
from pathlib import Path

from . import activity, config as cfg, git_ops, hooks, output, pr_authority, pr_publish, providers, tracking
from .config import Config
from .providers.attribution import BranchLeakError, validate_effective_head
from .providers.base import ProviderError, PullResult


def observe_pr(config: Config, pr: tracking.PRRecord) -> PullResult:
    if pr.number is None:
        raise ValueError("Cannot observe an unnumbered PR.")
    prcfg = config.default_repo.pr
    provider = providers.get_provider(pr.provider or prcfg.provider)
    return provider.get_pull(
        pr.repo, int(pr.number), api_base=prcfg.api_base,
        token=providers.account_token_for_slug(pr.repo, prcfg),
    )


def _validate(worktree_id: str, config: Config, record: tracking.WorktreeRecord | None,
              cwd: str) -> tracking.PRRecord:
    repo = config.default_repo
    pr = record.active_pr() if record else None
    if not repo.pr.enabled or record is None or pr is None or pr.state != "open":
        raise ValueError("--rewrite-pr requires an open tracked PR, never direct/shared publication.")
    branch = f"worktree/{worktree_id}"
    if (record.worktree_id != worktree_id or record.repo.lower() != config.repo_name.lower() or record.branch != branch
            or Path(record.worktree_path).resolve() != Path(cwd).resolve()
            or git_ops._get_current_branch_safe(cwd) != branch):
        raise ValueError("--rewrite-pr requires the owning worktree on its recorded worktree branch.")
    if pr.repo and pr.repo.lower() != (git_ops.remote_slug(repo.remote, cwd=cwd) or "").lower():
        raise ValueError("Tracked PR belongs to another repository; nothing was pushed.")
    if pr.rewrite_owner != f"{worktree_id}:{pr.branch}" or not pr.rewrite_identity:
        raise ValueError("Missing create-pr ownership. Run create-pr on the published, unrevised tip first.")
    if (pr.branch in {repo.default_branch, "main", "master", "dev", branch}
            or not pr.branch.endswith("-" + git_ops.worktree_suffix(worktree_id))
            or "/" not in pr.branch
            or pr.branch.startswith(("worktree/", "release/"))):
        raise ValueError("--rewrite-pr only rewrites a worktree-scoped private PR head.")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", pr.head_sha):
        raise ValueError("--rewrite-pr requires the previously recorded full expected-head SHA.")
    if any(p is not pr and p.branch == pr.branch and not tracking._pr_is_terminal(p) for p in record.prs):
        raise ValueError("Another live PR in this worktree shares the head; it cannot be rewritten.")
    pr_authority.assert_exclusive(config, record, pr)
    validate_effective_head(
        pr.branch, worktree_id=worktree_id, machine=(config.machine, record.machine),
        source_attribution=repo.pr.source_attribution,
    )
    return pr


def push_changes(worktree_id: str, config: Config, record: tracking.WorktreeRecord | None,
                 *, dry_run: bool = False) -> bool:
    """Publish as-is; never rebase, squash, or refresh the saved lease."""
    from .finalize import FinalizeLock

    repo = config.default_repo
    cwd = record.worktree_path if record else str(Path(repo.worktree_root) / worktree_id)
    if not Path(cwd).is_dir():
        output.err(f"Worktree path not found: {cwd}")
        return False
    lock = FinalizeLock(Path(repo.worktree_root) / ".finalize.lock")
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock.")
        return False
    try:
        with pr_authority.guard(), pr_publish.publish_lock(cwd), pr_publish.metadata_lock(worktree_id, project=config.repo_name):
            # Re-read after admission so a concurrent set-pr or publication cannot
            # change which PR the request authorizes.
            path = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
            fresh = tracking.load_record(path) if path.exists() else None
            if (record is None or fresh is None or fresh.active_pr() != record.active_pr()):
                raise ValueError("Tracked PR changed while preparing the rewrite; nothing was pushed.")
            pr = _validate(worktree_id, config, fresh, cwd)
            target = pr_publish.push_target(repo, pr, cwd)
            if target is None:
                raise ValueError(pr_publish.UNREADABLE_REMOTE)
            identity = pr_publish.push_identity(target.remote, cwd=cwd)
            slug = pr_publish.push_slug(target.remote, cwd=cwd)
            if not identity or not slug:
                raise ValueError(pr_publish.REPOINTED)
            if identity != pr.rewrite_identity:
                raise ValueError("PR publication destination changed; nothing was pushed.")
            if pr.number:
                observed = observe_pr(config, pr)
                if observed.merged or observed.state.lower() != "open" or observed.head_sha != pr.head_sha:
                    raise ValueError("Provider PR is not open at the recorded head; inspect and reconcile it.")
            if pr_publish._tip(target.remote, pr.branch, cwd) != pr.head_sha:
                raise ValueError("PR remote head no longer matches the recorded lease; inspect and reconcile it.")
            if not git_ops.is_clean(cwd=cwd):
                raise ValueError("Working tree has uncommitted changes; commit them before --rewrite-pr.")
            head_sha = git_ops.git("rev-parse", f"refs/heads/{fresh.branch}", cwd=cwd).stdout.strip()
            if dry_run:
                print(f"[dry-run] Would rewrite {target.remote}/{pr.branch} with exact lease {pr.head_sha}.")
                return True
            if repo.pr.head_scheme == "snapshot":
                git_ops.git("branch", "-f", pr.branch, head_sha, cwd=cwd)
            # Pin the source object as well as the destination: even a concurrent
            # local checkout cannot substitute another branch during hook execution.
            with hooks.allow_pr_push():
                result = pr_publish.push_checked(
                    fresh, target.remote, f"{head_sha}:refs/heads/{pr.branch}", cwd=cwd,
                    expected_head_repo=slug, expected_head_identity=identity,
                    force_with_lease_expect=pr.head_sha, allow_history_rewrite=True,
                )
            if not result:
                output.err(f"PR rewrite failed.{result.failure_detail}")
                return False
            pr_publish.record_pushed_head(
                config, fresh, worktree_id, pr, head_sha,
                remote=target.remote, head_repo=slug, head_identity=identity,
            )
            activity.log_event("pr_history_rewritten", worktree_id=worktree_id, branch=pr.branch)
            output.ok(f"Explicitly rewrote PR head '{pr.branch}' using its exact recorded lease.")
            return True
    except (ValueError, BranchLeakError, TimeoutError, git_ops.GitError, ProviderError) as exc:
        output.err(str(exc))
        return False
    finally:
        lock.release()
