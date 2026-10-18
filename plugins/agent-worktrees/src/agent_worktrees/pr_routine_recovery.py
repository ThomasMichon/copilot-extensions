"""Reconcile a backed, already accepted publication through normal ownership gates."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path

from . import (
    activity,
    git_ops,
    output,
    pr_authority,
    pr_publication_state,
    pr_publish,
    pr_recovery,
    pr_recovery_plan,
    pr_rewrite,
    publication_deadline,
    tracking,
)
from .config import Config
from .providers.base import ProviderError


def reconcile(config: Config, record: tracking.WorktreeRecord | None, *, cwd: str) -> str:
    """Settle stale bookkeeping, never publish or authorize a history replacement.

    Called before either entrypoint acquires its publication lock. An empty
    result means no applicable reconciliation, not a failed admission; unsafe
    or unavailable evidence raises and stops the caller.
    """
    from .finalize import FinalizeLock

    repo = config.default_repo
    # Reconcile whichever PR the checkout actually selects -- the active PR
    # on 'worktree/<id>', or an older live PR on its own tracked private
    # head -- never only the former: a newer PR becoming active must not
    # silently skip settling an older PR's accepted-but-unacknowledged head.
    pr = pr_recovery.selected_pr(
        record.worktree_id, git_ops._get_current_branch_safe(cwd), record,
    ) if record is not None else None
    if pr is None or not pr.number or tracking._pr_is_terminal(pr) or not repo.pr.enabled:
        return ""
    budget = publication_deadline.lifecycle_budget(repo.pr)
    lock = FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock", timeout=budget, stale_after=budget,
    )
    try:
        lock.acquire()
        wait = publication_deadline.lock_wait(repo.pr.push_timeout_seconds)
        with pr_authority.guard(timeout=wait), pr_publish.publish_lock(
            cwd, push_timeout_seconds=repo.pr.push_timeout_seconds,
        ), pr_publish.metadata_lock(record.worktree_id, project=config.repo_name, timeout=wait):
            pr_publication_state.require_current(record, pr)
            target = pr_publish.push_target(repo, pr, cwd)
            if target is None:
                raise ValueError(pr_publish.UNREADABLE_REMOTE)
            observed = pr_publish._tip(target.remote, pr.branch, cwd)
            if observed == pr.head_sha:
                return ""
            if not observed:
                raise ValueError(
                    f"Cannot confirm PR ref '{pr.branch}' on remote '{target.remote}' "
                    "for routine reconciliation."
                )
            return _settle(config, record, pr, target.remote, observed, cwd=cwd)
    except TimeoutError as exc:
        raise ValueError(f"Routine reconciliation lock timed out: {exc}") from exc
    finally:
        lock.release()


def _settle(
    config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    remote: str, observed: str, *, cwd: str,
) -> str:
    pr_rewrite._validate(record.worktree_id, config, record, cwd, pr=pr, allow_feature_head=True)
    if not git_ops.is_clean(cwd=cwd):
        raise ValueError("Commit working-tree changes before routine PR reconciliation.")
    identity = pr_publish.push_identity(remote, cwd=cwd)
    legacy_destination = not pr.rewrite_identity
    if not identity or identity != (pr.rewrite_identity or pr_rewrite.legacy_identity(pr, cwd)):
        raise ValueError("Routine reconciliation requires the original publication destination.")
    source = pr_recovery._rev("HEAD", cwd)
    operation = pr_recovery_plan.build(
        config, record, pr, source_head=source, expected_head=observed,
        identity=identity, overrides=(), cwd=cwd,
    )
    pr_recovery_plan.revalidate(
        config, record, pr, operation, remote=remote, observe=pr_rewrite.observe_pr, cwd=cwd,
        allow_feature_head=True,
    )
    if operation.action != "settle-publication":
        if operation.evidence_status == "unusable" or not _verified_fallback(
            config, record, pr, remote, observed, source, cwd=cwd,
        ):
            raise ValueError("Routine reconciliation requires intact retained publication evidence.")
        output.warn("No backed settlement applies; continuing through verified publication recovery.")
        return ""
    if not git_ops.is_commit_ancestor(observed, source, cwd=cwd):
        raise ValueError("The live PR head is not an ancestor of the source; no routine reconciliation.")
    git_dir = pr_recovery._path(cwd).parent
    path = git_dir / f"agent-worktrees-pr-reconciliation-{uuid.uuid4().hex}.json"
    audit = {
        "schema": "agent-worktrees.pr-reconciliation", "version": 1,
        "authorization_basis": "normal exclusive publication ownership; backed accepted sync",
        "operation": asdict(operation),
        "saved_publication": pr_recovery_plan.saved_publication(pr),
        "outcome": "attempted", "recovery_refs": {},
    }
    tracking._atomic_write(path, json.dumps(audit, sort_keys=True) + "\n")
    settled = False
    settlement_attempted = False
    try:
        point = pr_recovery.retain_rewrite(
            record.worktree_id, record.branch, source, pr, cwd=cwd,
        )
        observed_ref = point.local_ref.rpartition("/")[0] + "/observed"
        git_ops.git(
            "update-ref", observed_ref, observed, "0" * len(observed),
            cwd=cwd, isolated_repository=True,
        )
        audit["recovery_refs"] = {
            "source": point.local_ref, "saved": point.published_ref, "observed": observed_ref,
        }
        tracking._atomic_write(path, json.dumps(audit, sort_keys=True) + "\n")
        pr_rewrite._validate(record.worktree_id, config, record, cwd, pr=pr, allow_feature_head=True)
        pr_recovery_plan.revalidate(
            config, record, pr, operation, remote=remote, observe=pr_rewrite.observe_pr, cwd=cwd,
            allow_feature_head=True,
        )
        settlement_attempted = True
        if legacy_destination:
            # The identity above was only verified through the dynamic legacy
            # fallback; persist it *before* advancing the tracked head, and
            # refuse outright on failure -- a silently-unrecorded destination
            # would leave push_checked() with nothing to reject a later
            # remote repoint against.
            ownership_error = pr_publish.record_rewrite_ownership(
                config, record, pr, pr.branch, pr.head_sha, identity,
            )
            if ownership_error:
                raise ValueError(
                    f"Routine reconciliation could not record its verified "
                    f"destination: {ownership_error}"
                )
        pr_publish.record_reconciled_head(
            config, record, pr, observed, rebase_base_sha=operation.rebase_base_sha,
        )
        settled = True
        audit["outcome"] = "succeeded"
        tracking._atomic_write(path, json.dumps(audit, sort_keys=True) + "\n")
    except (OSError, ValueError, git_ops.GitError, ProviderError) as exc:
        audit["outcome"] = "unknown" if settlement_attempted else "failed"
        tracking._atomic_write(path, json.dumps(audit, sort_keys=True) + "\n")
        raise ValueError(
            f"Routine reconciliation failed (tracking {'settled' if settled else 'not confirmed'}); "
            f"inspect {path}: {exc}"
        ) from exc
    activity.log_event("pr_publication_reconciled", worktree_id=record.worktree_id, branch=pr.branch)
    output.ok(f"Reconciled backed publication of PR head '{pr.branch}'; no push or PR edit.")
    return source


def _verified_fallback(
    config: Config, record: tracking.WorktreeRecord, pr: tracking.PRRecord,
    remote: str, observed: str, source: str, *, cwd: str,
) -> bool:
    """Defer only source-owned acknowledgement loss to the existing push guards."""
    if (
        git_ops.is_commit_ancestor(pr.head_sha, observed, cwd=cwd)
        and git_ops.is_commit_ancestor(observed, source, cwd=cwd)
    ):
        return True
    from . import pr_rebase

    # A publication rebase rewrites history, so the saved head is not
    # necessarily a literal ancestor of the newly accepted (rebased) head --
    # that must not block the verified replay proof below, only the simple
    # ancestor-chain case above.
    # The supported source is whichever checkout routine settlement already
    # admitted -- the worktree branch or the PR's own tracked private head
    # (see pr_rewrite._validate's allow_feature_head) -- never a hardcoded
    # worktree/<id>, which would mismatch HEAD on a rebased private head.
    checked_out = git_ops._get_current_branch_safe(cwd) or record.branch
    proof = pr_rebase.verify(
        record, config.default_repo, remote, f"{checked_out}:refs/heads/{pr.branch}",
        pr.head_sha, cwd=cwd,
    )
    return proof is not None and git_ops.is_commit_ancestor(observed, proof.old_source, cwd=cwd)
