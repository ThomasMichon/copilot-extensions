"""Physical checkout removal and guarded tracking-record retirement."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from collections.abc import Callable

from . import activity, config as cfg, disposition_history, git_ops, handoff_trace
from . import permissions, procs, tracking, venv_reclaim, venv_reclaim_hooks
from .checkout_data import preservation_reason
from .retirement_safety import retirement_blocker, same_pair


def reap_worktree(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    repo: cfg.RepoConfig,
    tracking_path: Path,
) -> tuple[int, list[str]]:
    """Reload and fence responsibility under the caller's finalization lock."""
    path = tracking_path / f"{rec.worktree_id}.yaml"
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            latest = tracking.load_record(path)
            if (
                latest.worktree_path != rec.worktree_path
                or latest.branch != rec.branch
                or latest.checkout_managed != rec.checkout_managed
                or not same_pair(rec, latest)
            ):
                return 1, [f"Cannot retire {rec.worktree_id}: checkout identity changed; retry cleanup"]
            return _reap_locked(latest, info, repo, tracking_path)
    except (OSError, ValueError) as exc:
        return 1, [f"Cannot retire {rec.worktree_id}: {exc}"]


def _reap_locked(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    repo: cfg.RepoConfig,
    tracking_path: Path,
    *, before_remove: Callable[[], None] | None = None,
) -> tuple[int, list[str]]:
    """Keep the responsibility fence through removal and record retirement."""
    warnings: list[str] = []
    failures = 0
    from . import session_lease
    from . import paired_cleanup

    try:
        paired_cleanup.require_joint(rec)
    except ValueError as exc:
        return 1, [str(exc)]

    if session_lease.release_expired_finalized(rec):
        tracking.save_record(rec, tracking_path / f"{rec.worktree_id}.yaml")
    blocker = retirement_blocker(rec)
    if blocker is not None:
        return 1, [f"Cannot retire {rec.worktree_id}: {blocker.reason}"]

    if not rec.checkout_managed:
        try:
            retired = tracking.retire_record(rec, tracking_path)
        except ValueError as exc:
            return 1, [str(exc)]
        if not retired:
            warnings.append(
                f"Tracking record for {rec.worktree_id} changed or is busy "
                "-- retirement deferred to a later pass."
            )
            return 0, warnings
        disposition_history.remove(rec.worktree_id)
        handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
        activity.log_event(
            "external_worktree_tracking_retired",
            worktree_id=rec.worktree_id,
            path=rec.worktree_path,
        )
        return 0, warnings

    if rec.worktree_path and (reason := preservation_reason(rec.worktree_path) or venv_reclaim_hooks.data_blocker(rec.worktree_path)):
        return 1, [f"Cannot remove {rec.worktree_id}: {reason}"]
    if rec.worktree_path and Path(rec.worktree_path).exists():
        process_blocker = procs.checkout_retirement_blocker(rec.worktree_path)
        if process_blocker:
            return 1, [
                f"Cannot retire {rec.worktree_id}: {process_blocker}. "
                "Cleanup deferred; settle process use with its owner before retrying."
            ]
        # Remove test venvs first so Windows locks and deep paths in them cannot
        # leave a partially removed checkout; a live venv interpreter holds it.
        if venv_blocker := venv_reclaim_hooks.removal_blocker(rec.worktree_path):
            return 1, [f"Cannot retire {rec.worktree_id}: {venv_blocker}. Cleanup deferred."]

        if before_remove is not None:
            before_remove()
        if not git_ops.remove_worktree(repo.anchor, rec.worktree_path):
            warnings.append("Could not remove worktree via git -- forcing directory removal.")
        if reason := preservation_reason(rec.worktree_path):
            return 1, [f"Cannot remove {rec.worktree_id}: {reason}"]
        wt_dir = Path(rec.worktree_path)
        if wt_dir.exists():
            for attempt in range(4):
                if reason := preservation_reason(wt_dir):
                    return 1, [f"Cannot remove {rec.worktree_id}: {reason}"]
                if before_remove is not None:
                    before_remove()
                shutil.rmtree(venv_reclaim.native_path(wt_dir), onerror=venv_reclaim.clear_readonly_and_retry)
                if not wt_dir.exists():
                    break
                time.sleep(0.25 * (attempt + 1))
            if wt_dir.exists():
                warnings.append(f"Directory still present: {wt_dir}")
                failures += 1

    if rec.branch and rec.branch != repo.default_branch and git_ops.local_branch_exists(rec.branch, cwd=repo.anchor):
        if not git_ops.delete_branch(rec.branch, cwd=repo.anchor, force=True):
            warnings.append(f"Could not delete branch {rec.branch}")
            failures += 1

    if rec.worktree_path and not Path(rec.worktree_path).exists():
        permissions.merge_permissions(repo.anchor, rec.worktree_path)
        permissions.remove_trusted_folder(rec.worktree_path)

    if failures or paired_cleanup.admitted(rec):
        return failures, warnings

    # Preserve the record on deferral or a newly observed responsibility hold.
    try:
        retired = tracking.retire_record(rec, tracking_path)
    except ValueError as exc:
        return failures + 1, [*warnings, str(exc)]
    if retired:
        disposition_history.remove(rec.worktree_id)
        handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
    else:
        warnings.append(
            f"Tracking record for {rec.worktree_id} changed or is busy "
            "-- worktree/branch removed; record will retire on a later pass."
        )

    activity.log_event(
        "worktree_reaped",
        worktree_id=rec.worktree_id,
        branch=rec.branch,
        state=info.state.value,
    )
    return failures, warnings
