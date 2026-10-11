"""Shared git-disposition computation for batch lists and status bundles.

Callers select fetch intent, optional liveness precedence, and their own
session-turn observation. Bundle-only facts and transport/cache policy stay
outside this module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

from . import config as cfg
from . import git_ops, tracking


def apply_tracking_override(
    record: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
) -> git_ops.WorktreeStateInfo:
    """Honor finalized tracking, except for missing, live, or dirty checkouts.

    Squash-merged commits and zero-commit finalizations can look WIP/UNUSED
    to git alone. New uncommitted work must never be masked as prune-able:
    retain both the DIRTY label check (cached callers may lack counts) and
    the dirty-count check (ORPHAN can still contain uncommitted changes).
    """
    if record.status in ("finalized", "complete", "completed"):
        if (
            info.state != git_ops.WorktreeState.DIRTY
            and info.dirty == 0
            and info.state not in (git_ops.WorktreeState.GONE, git_ops.WorktreeState.ACTIVE)
        ):
            return dataclasses.replace(info, state=git_ops.WorktreeState.COMPLETED)
    return info


def compute(
    record: tracking.WorktreeRecord,
    *,
    repo: cfg.RepoConfig,
    fetch: bool,
    active_paths: set[str] | None,
    session_turns: int = 0,
    tracking_override: Callable[
        [tracking.WorktreeRecord, git_ops.WorktreeStateInfo], git_ops.WorktreeStateInfo
    ] = apply_tracking_override,
) -> git_ops.WorktreeStateInfo:
    """Classify and refine one record, preserving the leaf's freshness facts.

    Lists opt into ACTIVE precedence and never fetch. Status bundles request
    a fetch with ``active_paths=None``: their separate liveness fact must not
    short-circuit git disposition before that fetch. A missing finalized
    checkout retains the list contract's COMPLETED state.
    """
    if record.worktree_path and Path(record.worktree_path).exists():
        info = git_ops.classify_worktree(
            record.worktree_path,
            record.branch,
            fetch=fetch,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        if record.status == "finalized" and info.state == git_ops.WorktreeState.ACTIVE:
            # An attached concluding shell preserves FINAL, but ACTIVE's early
            # classifier return cannot conceal new dirtiness or checkout drift.
            content = git_ops.classify_worktree(
                record.worktree_path, record.branch, fetch=False,
                remote=repo.remote, default_branch=repo.default_branch,
                active_paths=None,
            )
            info = dataclasses.replace(content, state=git_ops.WorktreeState.ACTIVE)
        info = tracking_override(record, info)
    elif record.status == "finalized":
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    else:
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)
    if session_turns:
        info = dataclasses.replace(
            info, state=git_ops.refine_state_with_session(info.state, session_turns)
        )
    return info
