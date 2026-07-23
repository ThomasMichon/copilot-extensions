#!/usr/bin/env python3
"""On-disk orphan-directory garbage collection for the worktree fleet.

An **orphan directory** is a directory under a worktree root that is *neither* a
registered git worktree (``git worktree list``) *nor* a tracking record -- pure
filesystem cruft left behind by an interrupted or forced worktree removal (the
directory survives after its git registration + tracking YAML are gone). These
accumulate over time and are invisible to both ``git worktree prune`` (it only
drops the metadata, never the leftover directory) and ``cleanup`` (it works from
tracking records, which the orphan no longer has). See issues #66, #828, #1027.

This module finds orphans and removes the **effectively-empty** ones -- a
leftover whose tree contains no real files (only empty subdirs or known
ephemeral caches like ``__pycache__`` / ``.pytest_cache``). A leftover that
still holds real files is **reported, never auto-deleted** -- it may carry
un-rescued work and belongs to manual review. Removal uses a **locked-directory
retry/skip**: on Windows a just-finalized directory can linger under a transient
handle (#828), so a ``PermissionError`` is retried once and then skipped with a
reason rather than crashing the sweep.

The sweep is pure/inspectable: ``find_orphans`` and ``classify_orphan`` take no
side effects; only ``sweep_orphans`` mutates the filesystem (and only when not
``dry_run``).
"""
from __future__ import annotations

import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path

# Directory names whose contents are disposable build/test caches -- a leftover
# containing only these is still "effectively empty" for GC purposes.
CACHE_DIRNAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)

# A just-created directory may not be a git worktree *yet* (creation races the
# sweep). Never touch an orphan modified within this settle window.
_MIN_SETTLE_SECS = 3600  # 1 hour


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def candidate_roots(repo) -> list[Path]:
    """Every directory that could hold *repo*'s worktrees on this machine.

    Covers the configured ``worktree_root`` plus both layout conventions so a
    stray worktree created under the "other" convention is still swept:

    * sibling ``<anchor>.worktrees`` (the Copilot-native default), and
    * central ``<srcroot>/.worktrees/<repo-name>`` (the legacy central layout).

    Returns existing directories only, de-duplicated by normalized path.
    """
    anchor = Path(repo.anchor)
    candidates = [
        Path(repo.worktree_root),
        Path(str(anchor).rstrip("/").rstrip("\\") + ".worktrees"),
        anchor.parent / ".worktrees" / anchor.name,
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for c in candidates:
        key = _norm(c)
        if key in seen:
            continue
        seen.add(key)
        if c.is_dir():
            out.append(c)
    return out


def find_orphans(repo, registered_paths, tracked_paths) -> list[Path]:
    """Directories under *repo*'s worktree roots that are neither a registered
    git worktree nor a tracking record -- the orphan set."""
    reg = {_norm(p) for p in registered_paths}
    trk = {_norm(p) for p in tracked_paths}
    orphans: list[Path] = []
    for root in candidate_roots(repo):
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            key = _norm(child)
            if key in reg or key in trk:
                continue
            orphans.append(child)
    return orphans


def _has_real_files(d: Path) -> bool:
    """True when *d*'s tree holds any file outside a known ephemeral cache dir."""
    for root, dirs, files in os.walk(d):
        rel_parts = Path(root).relative_to(d).parts
        if any(part in CACHE_DIRNAMES for part in rel_parts):
            # Inside a cache dir -- its files don't count and don't descend.
            dirs[:] = []
            continue
        # Prune cache dirs from the walk so we don't count their contents.
        dirs[:] = [x for x in dirs if x not in CACHE_DIRNAMES]
        if files:
            return True
    return False


def _age_secs(d: Path) -> float:
    try:
        return max(0.0, time.time() - d.stat().st_mtime)
    except OSError:
        return 0.0


@dataclass
class OrphanVerdict:
    path: str
    action: str  # "remove" | "skip"
    reason: str


def classify_orphan(d: Path, *, min_settle_secs: float = _MIN_SETTLE_SECS) -> OrphanVerdict:
    """Decide what to do with one orphan directory, without touching it."""
    if _age_secs(d) < min_settle_secs:
        return OrphanVerdict(str(d), "skip", "too recent (may be mid-creation)")
    if _has_real_files(d):
        return OrphanVerdict(str(d), "skip", "non-empty (has files) -- manual review")
    return OrphanVerdict(str(d), "remove", "effectively empty")


# ---------------------------------------------------------------------------
# Managed (system/bridge) worktree GC
# ---------------------------------------------------------------------------

# The daemon-owned kinds that routine cleanup deliberately skips (they are torn
# down by their owning service). They still leak -- a crashed daemon, or a caller
# that finalized without tearing its bridge worktree down -- so this GC reaps the
# *provably dead* ones. Mirrors ``tracking.MANAGED_KINDS`` (kept as a literal so
# ``gc`` stays import-light / pure).
MANAGED_KINDS = ("system", "bridge")

# Git states / statuses that mean "the work here is done / never happened" -- the
# only conditions a managed worktree may be reaped from (besides plain UNUSED).
_FINAL_STATES = frozenset({"completed", "gone"})
_FINAL_STATUSES = frozenset({"finalized", "complete", "completed"})

# A dead managed worktree must be quiet this long before it's reaped, so a daemon
# that just created one (creation races the sweep) is never yanked out from under.
MANAGED_GC_GRACE_SECS = 3600  # 1 hour


@dataclass
class ManagedVerdict:
    worktree_id: str
    action: str  # "remove" | "skip"
    reason: str


def classify_managed_worktree(
    *,
    worktree_id: str,
    kind: str,
    follow_up: bool,
    status: str,
    git_state: str,
    has_live_mux: bool,
    attached: bool,
    has_live_session: bool,
    idle_secs: float | None,
    min_idle_secs: float = MANAGED_GC_GRACE_SECS,
) -> ManagedVerdict:
    """Decide whether one managed (system/bridge) worktree may be GC'd.

    Eligibility (all required): the worktree is **FINAL or UNUSED** (its work is
    done or never happened), has **no active process** (no live mux session, no
    attached terminal client, no live Copilot session), carries **no follow-up
    flag**, and has been **idle past the grace window**. Anything else -- a
    dirty/WIP tree, a live session, an attached client, a follow-up mark, or a
    still-fresh worktree -- is spared. Pure/inspectable: takes only facts, does
    no I/O.
    """
    if kind not in MANAGED_KINDS:
        return ManagedVerdict(worktree_id, "skip", "not-managed")
    if follow_up:
        return ManagedVerdict(worktree_id, "skip", "follow-up")
    if attached:
        return ManagedVerdict(worktree_id, "skip", "attached")
    if has_live_mux:
        return ManagedVerdict(worktree_id, "skip", "live-mux")
    if has_live_session:
        return ManagedVerdict(worktree_id, "skip", "live-session")

    is_final = status in _FINAL_STATUSES or git_state in _FINAL_STATES
    is_unused = git_state == "unused"
    if not (is_final or is_unused):
        return ManagedVerdict(worktree_id, "skip", "not-final-or-unused")

    # Never risk reaping something we can't prove is idle.
    if idle_secs is None:
        return ManagedVerdict(worktree_id, "skip", "activity-unknown")
    if idle_secs < min_idle_secs:
        return ManagedVerdict(worktree_id, "skip", "idle-grace")

    return ManagedVerdict(worktree_id, "remove",
                          "final" if is_final else "unused")


def _on_rm_error(func, path, _exc):
    """rmtree error hook: clear a read-only bit and retry the operation once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        raise


def _remove_tree(d: Path) -> tuple[bool, str]:
    """Remove *d*, retrying once on a lock (the Windows transient-handle case)."""
    for attempt in range(2):
        try:
            shutil.rmtree(d, onerror=_on_rm_error)
            return True, "removed"
        except PermissionError as exc:
            if attempt == 0:
                time.sleep(0.5)
                continue
            return False, f"locked ({type(exc).__name__}) -- skipped, retry later"
        except OSError as exc:
            return False, f"error: {exc}"
    return False, "locked -- skipped, retry later"


def sweep_orphans(
    repo,
    records,
    *,
    dry_run: bool = False,
    min_settle_secs: float = _MIN_SETTLE_SECS,
) -> dict:
    """Find and (unless *dry_run*) remove effectively-empty orphan directories.

    *records* are the repo's tracking records (their ``worktree_path`` values are
    excluded from the orphan set). Returns a JSON-ready report:
    ``{roots, removed: [{path, reason}], skipped: [{path, reason}], scanned}``.
    """
    from . import git_ops

    registered = git_ops.list_worktree_paths(cwd=repo.anchor)
    tracked = [r.worktree_path for r in records if getattr(r, "worktree_path", None)]
    orphans = find_orphans(repo, registered, tracked)

    removed: list[dict] = []
    skipped: list[dict] = []
    for d in orphans:
        verdict = classify_orphan(d, min_settle_secs=min_settle_secs)
        if verdict.action == "skip":
            skipped.append({"path": verdict.path, "reason": verdict.reason})
            continue
        if dry_run:
            removed.append({"path": verdict.path, "reason": "would remove (effectively empty)"})
            continue
        ok, reason = _remove_tree(d)
        (removed if ok else skipped).append({"path": str(d), "reason": reason})

    return {
        "roots": [str(r) for r in candidate_roots(repo)],
        "scanned": len(orphans),
        "removed": removed,
        "skipped": skipped,
    }


__all__ = [
    "CACHE_DIRNAMES",
    "MANAGED_GC_GRACE_SECS",
    "MANAGED_KINDS",
    "ManagedVerdict",
    "OrphanVerdict",
    "candidate_roots",
    "classify_managed_worktree",
    "classify_orphan",
    "find_orphans",
    "sweep_orphans",
]
