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
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_procutil import no_window_kwargs

from agent_logger import _peer_launch, sessions
from agent_logger.config import Config, home_dir
from agent_logger.segmenter.platform import detect_machine
from agent_logger.sessions import SessionRef

# Reuse the sync-lock so compaction never races the scheduled push (both touch
# ``~/.copilot/session-state``).
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.origin import classify_for_sync, effective_harness

log = logging.getLogger("agent-logger.compact")

# On Windows, shelling out from a windowless parent (pythonw under a Scheduled
# Task) flashes a console; suppress it. No-op on POSIX.
_NO_WINDOW_KWARGS: dict = no_window_kwargs()


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
    Returns ``None`` when agent-worktrees is unavailable (legacy mode) or, under
    an explicit installation context, when a valid owner genuinely has no
    same-cell worktrees peer installed at all -- callers fall back to an
    on-disk existence check, reliable for the same reason.

    Under an explicit installation-cell context, resolution uses only the
    validated same-cell peer boundary (never an ambient ``PATH`` command),
    scoped to that cell. A genuine transient FAILURE (owner-validation error,
    blocked governance, a probe error, or a malformed peer response) is
    reported by raising :class:`_peer_launch.ContextRefused` rather than
    quietly returning ``None`` -- ``None`` here means "confirmed nothing to
    protect", and a caller that only guards ``tracked_paths is not None``
    (rather than performing its own on-disk fallback) would otherwise treat a
    transient failure as if it had confirmed there was nothing to protect.
    Callers must catch :class:`_peer_launch.ContextRefused` explicitly and
    choose their own safe behavior for an unresolved (not confirmed-absent)
    protective set.
    """
    explicit_context = os.environ.get(_peer_launch.CONTEXT_ENV, "")
    if explicit_context:
        return _tracked_worktree_paths_same_cell(explicit_context)
    exe = shutil.which("agent-worktrees")  # marketplace-isolation: allow legacy-compatibility
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
    return _paths_from_list_response(data)


def _paths_from_list_response(data: object) -> set[str] | None:
    """Parse a ``list --json`` response, rejecting the WHOLE response on any
    malformed row rather than silently omitting it. A response with a
    malformed row (e.g. ``{"worktrees":[{}]}``) is not evidence that nothing
    is tracked -- a partial/empty result here must not be trusted as
    authoritative, since compaction treats an absent path as safe to archive.
    """
    if not isinstance(data, dict):
        return None
    worktrees = data.get("worktrees")
    if not isinstance(worktrees, list):
        return None
    paths: set[str] = set()
    for wt in worktrees:
        if not isinstance(wt, dict):
            return None
        p = wt.get("path")
        if not isinstance(p, str) or not p.strip() or "\x00" in p:
            return None
        paths.add(_normalize_path(p))
    return paths


def _tracked_worktree_paths_same_cell(raw_context: str) -> set[str] | None:
    """Same-cell resolution.

    Genuine absence (valid owner, no same-cell peer installed) returns
    ``None``. Every other failure -- invalid/foreign owner context, blocked
    governance, a probe error, or a malformed peer response -- raises
    :class:`_peer_launch.ContextRefused` instead, so a caller cannot mistake
    an unresolved result for a confirmed-empty one.
    """
    own = _validate_owner_or_refuse(raw_context)
    peer_root = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    if not peer_root.exists() and not peer_root.is_symlink():
        return None
    try:
        prefix = _peer_launch.launch_prefix(
            "agent-logger", Path(own["pluginRoot"]), raw_context, "agent-worktrees",
        )
        proc = subprocess.run(
            [*prefix, "list", "--json"],
            capture_output=True, encoding="utf-8", timeout=30,
            **_peer_launch.no_window_kwargs(),
        )
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as error:
        raise _peer_launch.ContextRefused(
            f"Same-cell worktrees list failed: {error}"
        ) from error
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise _peer_launch.ContextRefused(
            proc.stderr.strip() or "Same-cell worktrees list failed"
        )
    try:
        data = json.loads(proc.stdout)
    except ValueError as error:
        raise _peer_launch.ContextRefused(
            f"Invalid same-cell worktrees list response: {error}"
        ) from error
    paths = _paths_from_list_response(data)
    if paths is None:
        raise _peer_launch.ContextRefused(
            "Same-cell worktrees list response is malformed"
        )
    return paths


def _validate_owner_or_refuse(raw_context: str) -> dict[str, object]:
    try:
        return _peer_launch.validate_owner("agent-logger", home_dir(), raw_context)
    except (OSError, ValueError, ImportError) as error:
        raise _peer_launch.ContextRefused(
            f"agent-logger installation context refused: {error}"
        ) from error


def _resolve_tracked_paths_or_none(require_untracked: bool) -> set[str] | None:
    """Local-session-safe resolution: an unresolved lookup degrades to ``None``.

    Used by :func:`select_compactable`, whose per-session ``_worktree_tracked``
    already applies an on-disk existence fallback (erring toward "tracked")
    whenever ``tracked_paths`` is ``None``. A same-cell
    :class:`_peer_launch.ContextRefused` is therefore safe to fold into that
    same ``None`` fallback here -- unlike :func:`resolve_hub_tracked_paths`,
    used by hub compaction, which has no reliable per-session on-disk fallback
    for foreign-machine hub sessions.
    """
    if not require_untracked:
        return None
    try:
        return tracked_worktree_paths()
    except _peer_launch.ContextRefused as error:
        log.debug("agent-logger tracked-worktree lookup unresolved: %s", error)
        return None


def resolve_hub_tracked_paths(require_untracked: bool) -> tuple[set[str] | None, bool]:
    """Hub-safe resolution: any non-affirmative result must fail closed.

    Returns ``(tracked_paths, unresolved)``. Hub sessions may belong to a
    foreign machine, so there is no reliable per-session on-disk fallback the
    way :func:`select_compactable` has for its own live sessions -- and
    genuine peer *absence* is no more informative than a *failure* here: "no
    same-cell worktrees peer in this cell" is not evidence that nothing,
    anywhere, is tracked. Every ``None`` from :func:`tracked_worktree_paths`
    (absence, a legacy lookup miss, or a :class:`_peer_launch.ContextRefused`
    failure) is therefore treated identically as ``unresolved``, and only a
    genuinely resolved, non-``None`` set is passed through. The caller must
    skip this compaction pass on ``unresolved`` rather than proceed with
    ``tracked_paths=None`` -- ``FilesystemTarget.compact_backlog`` treats
    ``None`` as "nothing to protect", not "protection unavailable".
    """
    if not require_untracked:
        return None, False
    try:
        tracked = tracked_worktree_paths()
    except _peer_launch.ContextRefused as error:
        log.warning("agent-logger tracked-worktree lookup unresolved: %s", error)
        return None, True
    if tracked is None:
        log.warning(
            "agent-logger tracked-worktree lookup returned no result "
            "(peer absent or legacy lookup unavailable); treating as unresolved"
        )
        return None, True
    return tracked, False


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

    tracked_paths = _resolve_tracked_paths_or_none(require_untracked)

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

    lock_file = cfg.home / cfg.sync_lock_name
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
