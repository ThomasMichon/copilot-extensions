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
from .observation_progress import observation_boundary


def apply_tracking_override(
    record: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
) -> git_ops.WorktreeStateInfo:
    """Honor finalized tracking, except for missing, live, dirty, or moved checkouts.

    Squash-merged commits and zero-commit finalizations can look WIP/UNUSED
    to git alone. New uncommitted work must never be masked as prune-able:
    retain both the DIRTY label check (cached callers may lack counts) and
    the dirty-count check (ORPHAN can still contain uncommitted changes).
    """
    if record.status in ("finalized", "complete", "completed"):
        if _reopened_past_finalized_head(record, info):
            return _unlanded(info)
        if git_ops._windows_observation() and info.state == git_ops.WorktreeState.UNKNOWN:
            return info
        if (
            info.state != git_ops.WorktreeState.DIRTY
            and info.dirty == 0
            and info.state not in (git_ops.WorktreeState.GONE, git_ops.WorktreeState.ACTIVE)
        ):
            return dataclasses.replace(info, state=git_ops.WorktreeState.COMPLETED)
    return info


def _unlanded(info: git_ops.WorktreeStateInfo) -> git_ops.WorktreeStateInfo:
    """Keep a reopened checkout non-cleanable even when git cannot prove why.

    ``finalized`` status alone is a cleanup shortcut, so an ORPHAN, UNKNOWN or
    UNUSED observation of a checkout that left its latch is reported as WIP
    (content not proven on the default branch) rather than left to it.
    """
    S = git_ops.WorktreeState
    if info.state in (S.WIP, S.DIRTY, S.GONE, S.ACTIVE) or info.dirty:
        return info
    return dataclasses.replace(info, state=S.WIP)


def _reopened_past_finalized_head(
    record: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
) -> bool:
    """Finalize latches at its recorded HEAD; any other unproven HEAD reopens it.

    Only a ``finalized`` record carrying a ``finalized_checkout`` stamp is
    judged (legacy records keep the old masking). HEAD equal to the stamp stays
    latched. A different HEAD stays latched only when git proved it contained
    in the base: a branch checkout (HEAD names the very branch git counted)
    classified COMPLETED or UNUSED, i.e. nothing unlanded ahead of upstream.
    A missing/unresolvable base (ORPHAN/UNKNOWN), a detached HEAD, or an
    unreadable/empty HEAD is unknown and fails closed. HEAD is read from the
    checkout's git metadata files, so the probe never starts a Git child after
    a caller's bounded observation budget is spent.
    """
    stamp = record.finalized_checkout
    if record.status != "finalized" or not isinstance(stamp, dict):
        return False
    recorded = stamp.get("head")
    if not isinstance(recorded, str) or not recorded:
        return False
    try:
        head, ref = _read_head_ref(record.worktree_path)
    except Exception:
        return True
    if not head:
        return True
    if head == recorded:
        return False
    S = git_ops.WorktreeState
    counted = info.current_branch
    attached = bool(counted) and ref == f"refs/heads/{counted}"
    landed = info.state == S.COMPLETED or (info.state == S.UNUSED and info.ahead == 0)
    return not (attached and landed)


def _read_head(worktree_path: str | None) -> str | None:
    """Resolve a checkout's HEAD object id from files alone, or ``None``."""
    return _read_head_ref(worktree_path)[0]


def _read_head_ref(worktree_path: str | None) -> tuple[str | None, str | None]:
    """HEAD object id and symbolic ref (``None`` when detached) from files alone."""
    if not worktree_path:
        return None, None
    root = Path(worktree_path)
    dot_git = root / ".git"
    if dot_git.is_dir():
        git_dir = dot_git
    else:
        try:
            pointer = dot_git.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None, None
        if not pointer.startswith("gitdir:"):
            return None, None
        git_dir = Path(pointer[len("gitdir:"):].strip())
        if not git_dir.is_absolute():
            git_dir = root / git_dir
    try:
        content = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not content.startswith("ref:"):
        return (content if _is_object_id(content) else None), None
    ref = content[len("ref:"):].strip()
    if not ref.startswith("refs/") or ".." in ref.split("/"):
        return None, None
    common = git_dir
    try:
        shared = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        shared = ""
    except (OSError, UnicodeDecodeError):
        return None, None
    if shared:
        common = Path(shared) if Path(shared).is_absolute() else git_dir / shared
    for base in dict.fromkeys((git_dir, common)):
        try:
            value = (base / ref).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            return None, None
        return (value if _is_object_id(value) else None), ref
    try:
        packed = (common / "packed-refs").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    for line in packed.splitlines():
        oid, _, name = line.partition(" ")
        if name.strip() == ref:
            return (oid if _is_object_id(oid) else None), ref
    return None, None


def _is_object_id(value: str) -> bool:
    return len(value) in (40, 64) and all(c in "0123456789abcdef" for c in value)


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
    observation_boundary()
    if record.worktree_path and Path(record.worktree_path).exists():
        deadline_args = (
            {"observation_deadline": git_ops._observation_deadline()}
            if git_ops._windows_observation() else {}
        )
        info = git_ops.classify_worktree(
            record.worktree_path,
            record.branch,
            fetch=fetch,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
            **deadline_args,
        )
        if record.status == "finalized" and info.state == git_ops.WorktreeState.ACTIVE:
            # An attached concluding shell preserves FINAL, but ACTIVE's early
            # classifier return cannot conceal new dirtiness or checkout drift.
            content = git_ops.classify_worktree(
                record.worktree_path, record.branch, fetch=False,
                remote=repo.remote, default_branch=repo.default_branch,
                active_paths=None,
                **deadline_args,
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
