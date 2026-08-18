"""On-device cold-session compaction (session-sync WS2).

Selects *cold* sessions in the local Copilot state dir -- at least
``min_age_days`` old and not tied to an active worktree -- compresses each into
the agent-logger archive store (``<home>/archived-sessions/`` by default, kept
**outside** ``~/.copilot`` so it survives CLI session rotation), and reclaims
the live ``session-state/<id>/`` directory once the archive is verified.

This is the "second copy source" in the two-pair sync model: compaction moves a
session from the uncompressed source (``~/.copilot/session-state``) into the
compressed source (the archive store), which sync then carries to the hub.

Console: ``session-sync compact`` (see :mod:`agent_logger.sync.engine`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_logger import sessions
from agent_logger.config import Config
from agent_logger.segmenter.platform import detect_machine
from agent_logger.sessions import SessionRef

# Reuse the sync-lock so compaction never races the scheduled push (both touch
# ``~/.copilot/session-state``).
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.origin import classify_for_sync, effective_harness

# On Windows, shelling out from a windowless parent (pythonw under a Scheduled
# Task) flashes a console; suppress it. No-op on POSIX.
_NO_WINDOW_KWARGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if sys.platform == "win32"
    else {}
)


@dataclass
class CompactResult:
    """Outcome of one compaction pass."""

    scanned: int = 0
    compacted: int = 0
    reclaimed_bytes: int = 0
    skipped_recent: int = 0
    skipped_tracked: int = 0
    skipped_out_of_scope: int = 0
    skipped_unclassified: int = 0
    failed: list[str] = field(default_factory=list)


def _normalize_path(path: str) -> str:
    if not path or not path.strip():
        return ""
    return os.path.normcase(os.path.normpath(path.strip()))


def tracked_worktree_paths() -> set[str] | None:
    """Return normalized paths of worktrees the picker renders, or ``None``.

    "Tracked" means exactly what ``agent-worktrees list --json`` shows -- every
    worktree with a tracking record and a live directory, regardless of status
    (active / finalized / pushed). That is the picker's visible set: a session
    whose worktree is *not* in it is one the picker never renders, and is safe
    to archive. (Pruning a worktree deletes its directory and its ``.<repo>``
    registry entry together, so a pruned worktree drops out of this set.)
    Returns ``None`` when agent-worktrees is unavailable, so callers fall back
    to an on-disk existence check -- reliable for the same reason.
    """
    exe = shutil.which("agent-worktrees")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            **_NO_WINDOW_KWARGS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    paths: set[str] = set()
    for wt in data.get("worktrees", []):
        p = wt.get("path")
        if p:
            paths.add(_normalize_path(str(p)))
    return paths


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``)."""
    if not ts:
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_age_days(ref: SessionRef, ws: dict[str, str], now: datetime) -> float | None:
    """Age of a session in days from ``workspace.yaml`` timestamps.

    Prefers ``updated_at``, falls back to ``created_at``. Filesystem mtime is
    deliberately not used (it is unreliable, especially on OneDrive online-only
    placeholders). Returns ``None`` when no usable timestamp exists.
    """
    for key in ("updated_at", "created_at"):
        dt = _parse_iso(ws.get(key, ""))
        if dt is not None:
            return (now - dt).total_seconds() / 86400.0
    return None


def _worktree_tracked(
    ws: dict[str, str], tracked_paths: set[str] | None
) -> bool | None:
    """Whether a session belongs to a *tracked* worktree (picker-visible).

    ``True``/``False`` when classifiable, ``None`` when it cannot be decided.
    With the agent-worktrees tracked set: tracked iff the session cwd/git_root
    is in it. Without it (agent-worktrees absent): fall back to on-disk
    existence of the cwd -- reliable because pruning a worktree deletes its
    directory, so a path that still exists is still tracked and a path that is
    gone has been pruned. Errs toward "tracked" (keep) for any path that still
    exists.
    """
    cwd = _normalize_path(ws.get("cwd") or ws.get("git_root") or "")
    if not cwd:
        return None
    if tracked_paths is not None:
        return cwd in tracked_paths
    try:
        return Path(cwd).is_dir()
    except OSError:
        return None


def _dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def select_compactable(
    cfg: Config, *, now: datetime | None = None
) -> tuple[list[SessionRef], CompactResult]:
    """Select cold live sessions eligible for compaction.

    Eligible = at least ``min_age_days`` old AND (when
    ``require_untracked_worktree``) not belonging to a tracked worktree -- i.e.
    a worktree the picker renders. Since the picker only renders tracked
    worktrees and we only archive non-tracked ones, the two sets never overlap,
    so an archived session is never one the picker needs.

    The selection also honors the **sync repo scope** (``repo_allowlist`` /
    ``repo_denylist``): only sessions that sync itself would publish are
    compacted. This is a hard requirement, not a nicety -- the archive store is
    pushed to the hub wholesale by ``push_archives`` (Pair B), so compacting an
    out-of-scope session would leak it to the hub past the allowlist that
    excludes it from the uncompressed push.

    Sessions that cannot be classified (no timestamp, or an undecidable worktree
    state under fail-closed) are skipped, never compacted.
    """
    now = now or datetime.now(timezone.utc)
    opts = cfg.sync_compact
    min_age = opts["min_age_days"]
    require_untracked = opts["require_untracked_worktree"]
    state_root = cfg.sync_source / sessions.SESSION_STATE_SUBDIR

    tracked_paths = tracked_worktree_paths() if require_untracked else None

    # Same repo-scope gate as run_sync: None => no filter (sync everything).
    allowlist = cfg.sync_repo_allowlist
    denylist = cfg.sync_repo_denylist
    in_scope = _in_scope_ids(cfg, state_root, allowlist, denylist)

    result = CompactResult()
    selected: list[SessionRef] = []
    for ref in sessions.iter_session_refs(state_root):
        if ref.kind != "live":
            continue
        result.scanned += 1

        if in_scope is not None and ref.id not in in_scope:
            result.skipped_out_of_scope += 1
            continue

        ws = sessions.read_workspace(ref)

        age = session_age_days(ref, ws, now)
        if age is None:
            # No usable workspace.yaml timestamp -> cannot classify age.
            result.skipped_unclassified += 1
            continue
        if age < min_age:
            result.skipped_recent += 1
            continue

        if require_untracked:
            tracked = _worktree_tracked(ws, tracked_paths)
            if tracked is None:
                result.skipped_unclassified += 1
                continue
            if tracked:
                result.skipped_tracked += 1
                continue

        selected.append(ref)
    return selected, result


def _in_scope_ids(
    cfg: Config, state_root: Path, allowlist: list[str], denylist: list[str]
) -> set[str] | None:
    """Session ids the sync repo policy would publish, or ``None`` for "all".

    Mirrors ``engine._included_sessions`` so compaction never archives a session
    that sync would not publish (which Pair B would then leak to the hub).
    """
    if not allowlist and not denylist:
        return None
    if not state_root.is_dir():
        return set()
    machine = cfg.machine_name or detect_machine()
    effective = effective_harness(allowlist, cfg.sync_harness_repos, denylist)
    fail_closed = cfg.sync_repo_allowlist_fail_closed
    included: set[str] = set()
    for d in state_root.iterdir():
        if not d.is_dir():
            continue
        include, _ = classify_for_sync(
            d, machine, allowlist, effective, fail_closed=fail_closed,
            denylist=denylist,
        )
        if include:
            included.add(d.name)
    return included


def compact_session(
    ref: SessionRef, archive_root: Path, *, codec: str, reclaim: bool = True
) -> int:
    """Archive one live session and (by default) reclaim its live directory.

    Returns the bytes reclaimed from the live tree (0 if not reclaimed). The
    archive is verified before the live directory is removed; the local
    ``.tar.gz`` is always kept.
    """
    size = _dir_size(ref.path)
    archived = sessions.archive_session(ref.path, archive_root, codec=codec)
    if not sessions.verify_archive(archived):
        # Leave the live dir intact; drop the half-written archive.
        sessions.remove_archive(archived)
        raise RuntimeError(f"archive verification failed for {ref.id}")
    if reclaim:
        return size if sessions.force_rmtree(ref.path) else 0
    return 0


def run_compact(
    cfg: Config, *, dry_run: bool = False, verbose: bool = False
) -> CompactResult:
    """Execute one on-device compaction pass under the sync lock."""
    opts = cfg.sync_compact
    if not opts["enabled"]:
        if verbose:
            print("session-sync compact: disabled (sync.compact.enabled=false)")
        return CompactResult()

    codec = opts["codec"]
    archive_root = cfg.compact_archive_root

    selected, result = select_compactable(cfg)
    if verbose:
        print(f"compact: scanned {result.scanned} live session(s)")
        print(
            f"compact: {len(selected)} eligible; skipped "
            f"{result.skipped_recent} recent, {result.skipped_tracked} tracked, "
            f"{result.skipped_out_of_scope} out-of-scope, "
            f"{result.skipped_unclassified} unclassified"
        )

    if dry_run:
        for ref in selected:
            print(f"compact: would archive {ref.id} -> {archive_root}")
        return result

    if not selected:
        return result

    lock_file = cfg.home / "session-sync.lock"
    with sync_lock(lock_file, timeout=cfg.sync_lock_timeout) as acquired:
        if not acquired:
            print(
                "session-sync compact: another sync holds the lock; skipping",
                file=sys.stderr,
            )
            return result
        _compact_selected(cfg, selected, result, codec=codec,
                          archive_root=archive_root, verbose=verbose)
    return result


def _compact_selected(
    cfg: Config,
    selected: list[SessionRef],
    result: CompactResult,
    *,
    codec: str,
    archive_root: Path,
    verbose: bool = False,
) -> CompactResult:
    """Archive each selected session (lock-free core; caller holds the lock)."""
    for ref in selected:
        try:
            reclaimed = compact_session(ref, archive_root, codec=codec, reclaim=True)
        except (OSError, RuntimeError, ValueError) as exc:
            result.failed.append(f"{ref.id}: {exc}")
            continue
        result.compacted += 1
        result.reclaimed_bytes += reclaimed
        if verbose:
            mb = reclaimed / (1024 * 1024)
            print(f"compact: archived {ref.id} (reclaimed {mb:.1f} MB)")
    return result


def compact_local(cfg: Config, *, verbose: bool = False) -> CompactResult:
    """On-device compaction core for callers that already hold the sync lock.

    Selects cold in-scope untracked sessions and archives them (reclaiming the
    live dirs). Used by ``run_sync`` to fold compaction into the scheduled
    ``session-sync run`` when ``sync.compact.enabled``. Returns the result;
    ``enabled`` is the caller's gate.
    """
    codec = cfg.sync_compact["codec"]
    archive_root = cfg.compact_archive_root
    selected, result = select_compactable(cfg)
    if verbose:
        print(
            f"compact: {len(selected)} eligible of {result.scanned}; skipped "
            f"{result.skipped_recent} recent, {result.skipped_tracked} tracked, "
            f"{result.skipped_out_of_scope} out-of-scope, "
            f"{result.skipped_unclassified} unclassified"
        )
    if not selected:
        return result
    return _compact_selected(cfg, selected, result, codec=codec,
                            archive_root=archive_root, verbose=verbose)
