"""Explicit publication of intentionally rewritten, exclusively owned PR history."""

from __future__ import annotations

import re
import hashlib
import subprocess
from pathlib import Path

from . import activity, config as cfg, git_ops, hooks, output, pr_authority, pr_break_glass, pr_publication_state, pr_publish, pr_recovery, providers, publication_deadline, tracking
from .config import Config
from .providers.attribution import BranchLeakError, validate_effective_head
from .providers.base import ProviderError, PullResult
from .providers.pr_source import same_repository


def observe_pr(config: Config, pr: tracking.PRRecord) -> PullResult:
    if pr.number is None:
        raise ValueError("Cannot observe an unnumbered PR.")
    prcfg = config.default_repo.pr
    if pr.provider and pr.provider != prcfg.provider:
        raise ValueError("Tracked PR provider differs from configuration; refusing credential resolution.")
    provider = providers.get_provider(pr.provider or prcfg.provider)
    return provider.get_pull(
        pr.repo, int(pr.number), api_base=prcfg.api_base,
        token=providers.account_token_for_slug(pr.repo, prcfg),
    )


def legacy_identity(pr: tracking.PRRecord, cwd: str) -> str:
    if pr.head_identity:
        return pr.head_identity
    match = re.fullmatch(r"(https://[^?#]+)/(?:pull|pulls|pullrequest)/(\d+)", pr.url)
    if match and pr.number is not None and int(match[2]) == pr.number:
        return pr_publish._identity(match[1], cwd)
    return ""


def _validate(worktree_id: str, config: Config, record: tracking.WorktreeRecord | None,
              cwd: str, *, recovery: bool = False) -> tracking.PRRecord:
    repo = config.default_repo
    pr = record.active_pr() if record else None
    states = ("open", "active", "creating") if recovery else ("open", "active")
    if not repo.pr.enabled or record is None or pr is None or pr.state not in states:
        raise ValueError("--rewrite-pr requires an open tracked PR, never direct/shared publication.")
    if pr.provider and pr.provider != repo.pr.provider:
        raise ValueError("Tracked PR provider differs from configuration; refusing credential resolution.")
    branch = f"worktree/{worktree_id}"
    if (record.worktree_id != worktree_id or record.repo.lower() != config.repo_name.lower() or record.branch != branch
            or Path(record.worktree_path).resolve() != Path(cwd).resolve()
            or git_ops._get_current_branch_safe(cwd) != branch):
        raise ValueError("--rewrite-pr requires the owning worktree on its recorded worktree branch.")
    if pr.repo and pr.repo.lower() != (git_ops.remote_slug(repo.remote, cwd=cwd) or "").lower():
        raise ValueError("Tracked PR belongs to another repository; nothing was pushed.")
    if not recovery and (pr.rewrite_owner != f"{worktree_id}:{pr.branch}" or not pr.rewrite_identity):
        if (pr.rewrite_owner or pr.rewrite_identity
                or not legacy_identity(pr, cwd) or pr.number is None or not pr.url):
            raise ValueError("Missing publication ownership or compatible legacy evidence; inspect the tracked PR.")
    from .pr_custom_head import saved_attestation
    if (pr.branch in {repo.default_branch, "main", "master", "dev", branch, *hooks._protected_branches(cwd)}
            or (not recovery and not pr.branch.endswith("-" + git_ops.worktree_suffix(worktree_id))
                and not saved_attestation(record, repo, pr, pr.head_sha))
            or "/" not in pr.branch
            or pr.branch.startswith(("worktree/", "release/"))):
        raise ValueError("--rewrite-pr only rewrites a worktree-scoped private PR head.")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", pr.head_sha):
        raise ValueError("--rewrite-pr requires the previously recorded full expected-head SHA.")
    if recovery and (pr.number is None or not pr.repo):
        raise ValueError("Break-glass recovery requires an existing numbered PR, never new-PR creation.")
    if any(p is not pr and p.branch == pr.branch and not tracking._pr_is_terminal(p)
           and not (recovery and (p.number, p.repo, p.provider) == (pr.number, pr.repo, pr.provider))
           for p in record.prs):
        raise ValueError("Another live PR in this worktree shares the head; it cannot be rewritten.")
    if not recovery:
        pr_authority.assert_exclusive(config, record, pr, identity=pr.rewrite_identity or legacy_identity(pr, cwd))
    validate_effective_head(
        pr.branch, worktree_id=worktree_id, machine=(config.machine, record.machine),
        source_attribution=repo.pr.source_attribution,
    )
    return pr


def push_changes(worktree_id: str, config: Config, record: tracking.WorktreeRecord | None,
                 *, dry_run: bool = False, recovery_reason: str | None = None,
                 confirmation: str | None = None) -> bool:
    """Publish as-is; never rebase, squash, or refresh the saved lease."""
    from .finalize import FinalizeLock

    repo = config.default_repo
    cwd = record.worktree_path if record else str(Path(repo.worktree_root) / worktree_id)
    if not Path(cwd).is_dir():
        output.err(f"Worktree path not found: {cwd}")
        return False
    budget = publication_deadline.lifecycle_budget(repo.pr)
    lock = FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock", timeout=budget, stale_after=budget,
    )
    audit: pr_break_glass.RecoveryAudit | None = None
    remote_published = False
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock.")
        return False
    try:
        wait = publication_deadline.lock_wait(repo.pr.push_timeout_seconds)
        with pr_authority.guard(timeout=wait), pr_publish.publish_lock(
            cwd, push_timeout_seconds=repo.pr.push_timeout_seconds,
        ), pr_publish.metadata_lock(worktree_id, project=config.repo_name, timeout=wait):
            # Re-read after admission so a concurrent set-pr or publication cannot
            # change which PR the request authorizes.
            path = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
            fresh = tracking.load_record(path) if path.exists() else None
            if (record is None or fresh is None or record.active_pr() is None
                    or not pr_publication_state.matches(fresh.active_pr(), record.active_pr())):
                raise ValueError("Tracked PR changed while preparing the rewrite; nothing was pushed.")
            recovery = recovery_reason is not None
            pr = _validate(worktree_id, config, fresh, cwd, recovery=recovery)
            legacy_revalidation = not pr.rewrite_owner and pr.pr_revision > 0
            target = pr_publish.push_target(repo, pr, cwd)
            if target is None:
                raise ValueError(pr_publish.UNREADABLE_REMOTE)
            identity = pr_publish.push_identity(target.remote, cwd=cwd)
            slug = pr_publish.push_slug(target.remote, cwd=cwd)
            if not identity or not slug:
                raise ValueError(pr_publish.REPOINTED)
            if ((not recovery or pr.rewrite_identity)
                    and identity != (pr.rewrite_identity or legacy_identity(pr, cwd))):
                raise ValueError("PR publication destination changed; nothing was pushed.")
            observed = None
            if pr.number:
                observed = observe_pr(config, pr)
                if observed.merged or observed.state.lower() != "open" or observed.head_sha != pr.head_sha:
                    raise ValueError("Provider PR is not open at the recorded head; inspect and reconcile it.")
                if (recovery or legacy_revalidation) and (
                    observed.number != pr.number or observed.head_ref != pr.branch
                    or not observed.head_url
                    or not same_repository(
                        pr_publish._identity(observed.head_url, cwd), identity,
                        pr.provider or repo.pr.provider,
                    )
                ):
                    raise ValueError("Provider did not confirm this exact PR head and destination; nothing was pushed.")
            remote_tip = pr_publish._tip(target.remote, pr.branch, cwd)
            if remote_tip != pr.head_sha:
                raise ValueError("PR remote head no longer matches the recorded lease; inspect and reconcile it.")
            if not recovery and not pr.branch.endswith("-" + git_ops.worktree_suffix(worktree_id)):
                from .pr_custom_head import verify_attested
                if not verify_attested(fresh, repo, pr, target.remote, pr.head_sha, cwd=cwd,
                                       config=config, observed=observed, remote_tip=remote_tip):
                    raise ValueError("Custom PR head lacks exact saved and live publication ownership.")
            if not git_ops.is_clean(cwd=cwd):
                raise ValueError("Working tree has uncommitted changes; commit them before --rewrite-pr.")
            head_sha = git_ops.git(
                "--no-replace-objects", "rev-parse", "--verify", f"refs/heads/{fresh.branch}",
                cwd=cwd, isolated_repository=True,
            ).stdout.strip()
            approved = None
            if recovery_reason is not None:
                if pr.number is None:
                    raise ValueError("Recovery requires a numbered existing PR.")
                waived = pr_authority.assert_exclusive(
                    config, fresh, pr, recovery=True, destination_identity=identity,
                )
                operation = pr_break_glass.RecoveryPreview(
                    provider=pr.provider or repo.pr.provider, repo=pr.repo, number=pr.number,
                    branch=pr.branch, destination_fingerprint=hashlib.sha256(identity.encode()).hexdigest(),
                    source_head=head_sha, expected_head=pr.head_sha,
                    overrides=("historical ownership attestation", "private-head suffix", "replay journal", *waived),
                )
                approved = pr_break_glass.confirm(operation, recovery_reason, confirmation)
            if dry_run:
                print(f"[dry-run] Would rewrite {target.remote}/{pr.branch} with exact lease {pr.head_sha}.")
                return True
            if approved is not None:
                git_dir = Path(git_ops.git(
                    "rev-parse", "--absolute-git-dir", cwd=cwd, isolated_repository=True,
                ).stdout.strip())
                audit = pr_break_glass.begin_audit(
                    git_dir, approved, pr_break_glass.invocation_attribution(),
                )
                if not git_ops.ref_exists(pr.head_sha, cwd=cwd):
                    try:
                        git_ops.git(
                            "--no-replace-objects", "fetch", "--no-tags", target.remote,
                            f"refs/heads/{pr.branch}", cwd=cwd, isolated_repository=True,
                            timeout=git_ops.DEFAULT_FETCH_TIMEOUT, kill_tree=True,
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise TimeoutError("Recovery published-head fetch timed out; nothing was pushed.") from exc
                point = pr_recovery.retain_rewrite(
                    worktree_id, fresh.branch, head_sha, pr, cwd=cwd,
                )
                audit.retained(point.local_ref, point.published_ref)
                output.warn("Operator-confirmed recovery overrides historical ownership proof; normal merge gates remain.")
                print(f"Recovery commits retained: source={point.local_ref}, published={point.published_ref}")
            if not recovery and not pr.rewrite_owner:
                error = pr_publish.record_rewrite_ownership(config, fresh, pr, pr.branch, pr.head_sha, identity)
                if error:
                    raise ValueError(error)
            if repo.pr.head_scheme == "snapshot":
                git_ops.git("branch", "-f", pr.branch, head_sha, cwd=cwd)
            # Pin the source object as well as the destination: even a concurrent
            # local checkout cannot substitute another branch during hook execution.
            with hooks.allow_pr_push():
                result = pr_publish.push_checked(
                    fresh, target.remote, f"{head_sha}:refs/heads/{pr.branch}", cwd=cwd,
                    expected_head_repo=slug, expected_head_identity=identity,
                    force_with_lease_expect=pr.head_sha, allow_history_rewrite=True,
                    timeout=publication_deadline.configured(repo.pr),
                )
            if not result:
                if audit is None:
                    output.err(f"PR rewrite failed.{result.failure_detail}")
                    return False
                observed_tip = pr_publish._tip(target.remote, pr.branch, cwd)
                if observed_tip != head_sha:
                    definite_failure = observed_tip == pr.head_sha
                    audit.finish("failed" if definite_failure else "unknown")
                    output.err(
                        f"PR rewrite failed.{result.failure_detail}" if definite_failure else
                        "Recovery transport outcome is unknown; remote head needs reconciliation. "
                        "Do not repeat the push."
                    )
                    return False
                output.warn("Transport reported failure, but the exact remote ref confirms the approved commit.")
            remote_published = True
            pr_publish.record_pushed_head(
                config, fresh, worktree_id, pr, head_sha,
                remote=target.remote, head_repo=slug, head_identity=identity,
            )
            activity.log_event("pr_history_rewritten", worktree_id=worktree_id, branch=pr.branch)
            if audit is not None:
                audit.finish("succeeded")
            output.ok(f"Explicitly rewrote PR head '{pr.branch}' using its exact recorded lease.")
            return True
    except pr_break_glass.ConfirmationRequired:
        raise
    except (ValueError, BranchLeakError, TimeoutError, git_ops.GitError, ProviderError, OSError) as exc:
        if audit is not None and audit.outcome == "attempted":
            try:
                audit.finish("succeeded" if remote_published else "failed")
            except OSError as audit_error:
                output.err(f"Recovery audit completion failed: {audit_error}")
        prefix = "Remote head was published; persistence/audit needs reconciliation: " if remote_published else ""
        output.err(prefix + str(exc))
        return False
    finally:
        lock.release()
