"""Record fencing, rollback and side-effect-free finalization previews."""

from __future__ import annotations

from pathlib import Path

from . import output, tracking
from .config import Config


def rollback_finalizing_freeze(
    yaml_path, prior_status: str | None, worktree_id: str, config: Config | None = None,
) -> None:
    """Restore only a still-frozen, owned record; never undo a terminal save."""
    if prior_status is None or not yaml_path.exists():
        return
    try:
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            record = tracking.load_record(yaml_path)
            if config is not None:
                from .execution_spaces import ExecutionSpaceError, require_record_mutation
                try:
                    require_record_mutation(record, config)
                except ExecutionSpaceError as exc:
                    output.err(f"Cannot restore {worktree_id}'s status: {exc}; record unchanged.")
                    return
            if record.status != "finalizing":
                return
            record.status = prior_status
            try:
                tracking.save_record(record, yaml_path)
            except ValueError:
                from . import paired_claims
                if prior_status != "finalized" or not paired_claims.paired_ref(record):
                    raise
                record = tracking.load_record(yaml_path)
                if record.status != "finalizing":
                    return
                if config is not None:
                    require_record_mutation(record, config)
                if record.completed_at:
                    record.last_finalized_at = record.completed_at
                tracking.update_status(record, "active", save=False)
                tracking.save_record(record, yaml_path)
                try:
                    paired_claims.ensure_claim(record, require_active=True)
                except Exception as exc:
                    output.warn(
                        f"Restored {worktree_id} to active; paired claim "
                        f"reconciliation deferred ({type(exc).__name__})."
                    )
            restored_status = record.status
        output.warn(
            f"Restored {worktree_id}'s tracking status to {restored_status!r} "
            "after a finalize failure (was wedged at 'finalizing')."
        )
    except Exception as exc:
        output.err(
            f"Could not roll back {worktree_id}'s 'finalizing' freeze after a "
            f"finalize failure ({exc}); run 'agent-worktrees doctor' or inspect "
            f"the record manually -- creator ownership is at risk of being stuck."
        )


def authority_fenced_write(
    yaml_path: Path, config: Config, mutate,
) -> tracking.WorktreeRecord | None:
    """Reload fresh, re-verify authority, then mutate under one sidecar lock.

    Nested saves remain reentrant. Return None on rejection, contention or an
    unreadable record so callers cannot write from an earlier stale snapshot.
    """
    from .execution_spaces import require_record_mutation

    try:
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            fresh = tracking.load_record(yaml_path)
            require_record_mutation(fresh, config)
            mutate(fresh)
            return fresh
    except Exception:
        return None


def dry_run_finalize_preview(
    worktree_id: str,
    config: Config,
    worktree_path: str,
    branch: str,
    upstream: str,
) -> None:
    """Show what finalize would do without side effects."""
    repo = config.default_repo

    print()
    print(f"Finalization plan for worktree {worktree_id}:")
    output.dry_run(f"Would fetch from {repo.remote}")
    output.dry_run(f"Would validate that {branch} content is on {upstream}")
    output.dry_run(
        f"Would remove worktree directory and branch ONLY if idle "
        f"(no live session / not inside it): {worktree_path}"
    )
    output.dry_run("Would merge worktree permissions back to anchor")
    output.dry_run("Would remove worktree path from trustedFolders")
    output.dry_run("Would update worktree YAML status: finalized")
    print()
    output.ok("Dry run complete -- no changes made")
