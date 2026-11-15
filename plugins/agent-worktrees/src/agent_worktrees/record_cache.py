"""Per-process memoization for ``tracking``'s record reads and writes.

Split out of ``tracking.py`` to respect that module's shrink-only line-count
baseline (``tools/module-size-baseline.json``) rather than growing it.

copilot-extensions#3751: the resident status-monitor's ``worktree-status``
compute path (``sessions.verify_worktree_active`` -> ``reclaim.
resolve_bound_copilots`` -> ``tracking.find_worktree_id_by_cwd``) calls
``tracking.list_records`` once per *live session* it scans while resolving a
single worktree's status, and that whole resolve runs once per tracked
worktree per refresh -- with N tracked worktrees and M live sessions this is
O(N*M) full reparses of the *same* on-disk records every sweep (~85%
sustained CPU observed on a fleet of 9 live worktrees / 75 tracked records),
even with the already-landed ``CSafeLoader`` fix (#2615) making each
individual parse fast. This is a volume problem, not a parse-speed one.

A file's ``(mtime_ns, size)`` pair is the invalidation key: unchanged since
the last read -> reuse the cached record instead of re-reading + re-parsing.
Deliberately NOT a TTL/blackout cache -- a change lands in the cache on its
very next read, no staleness window is ever tolerated. Scoped to one
process's lifetime (a fresh CLI invocation always starts cold); only a
long-lived caller (the status-monitor daemon) actually accumulates hits.

``agent-worktrees-authoritative-daemon`` effort (2026-09-27): :func:`store`
lets a WRITER (``tracking.save_record``, this module's single write
chokepoint) push the record it just wrote straight into this same cache,
so a subsequent :func:`cached_load` in the SAME process is a hit against
the fresh value already in memory -- no redundant re-stat-and-reparse of a
file this process just produced itself. Read-side coverage was widened to
match: ``tracking.load_record`` (single-record reads, not just
``list_records``'s bulk sweep) now routes through :func:`cached_load` too,
so this benefit reaches every reader, not only the fleet-wide one.
"""

from __future__ import annotations

import copy
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import tracking_save_capture as save_capture
from . import tracking_save_notifications as notifications

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, int, WorktreeRecord]] = {}
logger = logging.getLogger(__name__)


def observe_writes(callback: Callable) -> Callable:
    """Observe detached committed snapshots after all record/pair holds release."""
    return notifications.observe_writes(callback)


_TRANSIENT_PROJECTION_ATTRS = (
    "_session_projection_dirty",
    "_session_projection_initial_registration",
    "_controller_projection_dirty",
)


def cached_load(
    path: Path,
    loader: Callable[[Path], WorktreeRecord],
    *,
    copy_result: bool = True,
) -> WorktreeRecord:
    """``loader(path)``, memoized on the file's own ``(mtime_ns, size)``.

    Returns an independent copy each time -- ``WorktreeRecord`` is a plain
    (non-frozen) ``@dataclass``, and callers throughout ``tracking`` and its
    consumers routinely mutate a record in place after reading it (e.g. to
    stage a write). Sharing one cached instance across callers would let one
    caller's in-place mutation silently corrupt what a later cache hit hands
    back to a different caller -- returning a fresh ``copy.deepcopy`` per hit
    keeps the cache purely a read-parse accelerator, never a shared-mutable-
    state hazard.

    ``copy_result=False`` (picker-performance-and-responsiveness Phase 3)
    opts OUT of that copy and hands back the cache's own object directly --
    a real, measured CPU cost at scale: live `py-spy` profiling of the
    resident status-monitor daemon caught ``MainThread`` sampled inside this
    function's ``copy.deepcopy`` call, on a machine with 100+ tracked
    records where a sweep calls this path ``O(tracked records x live
    sessions)`` times. Safe **only** for a caller that never mutates the
    record it reads and never retains it past the current call (e.g. a
    pure comparison/lookup scan) -- every other caller MUST keep the
    default. This is an explicit, narrow, opt-in escape hatch, not a
    general policy change: as of this writing exactly one caller
    (``tracking.find_worktree_id_by_cwd``, via ``list_records``) uses it,
    and it was audited to confirm it only reads ``worktree_path``/
    ``worktree_id`` and never assigns to the record it receives.
    """
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        with _cache_lock:
            _cache.pop(key, None)
        raise
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and (cached[0], cached[1]) == stamp:
            return cached[2] if not copy_result else copy.deepcopy(cached[2])
    rec = loader(path)
    with _cache_lock:
        _cache[key] = (stamp[0], stamp[1], rec)
    return rec if not copy_result else copy.deepcopy(rec)


def _saved_content(path: Path, content: str | None) -> str | None:
    try:
        if content is None:
            with path.open("rb") as handle:
                raw = handle.read(save_capture.MAX_CONTENT_BYTES + 1)
            if len(raw) > save_capture.MAX_CONTENT_BYTES:
                raise save_capture.CaptureOmitted("saved-record-byte-bound")
            content = raw.decode("utf-8")
        save_capture.content_bytes(content)
        return content
    except save_capture.CaptureOmitted as error:
        logger.warning("Committed record cache/snapshot omitted: %s", error)
    except Exception:
        # Optional snapshot acquisition only: canonical persistence is outside this boundary.
        logger.exception("Committed record snapshot unavailable after successful persistence")
    return None


def _warm(path: Path, snapshot: save_capture.FrozenValue) -> None:
    stamp = path.stat()
    cached_record = save_capture.thaw_legacy(snapshot)
    cached_record._loaded_from = path
    for attr in _TRANSIENT_PROJECTION_ATTRS:
        if hasattr(cached_record, attr):
            delattr(cached_record, attr)
    with _cache_lock:
        _cache[str(path)] = (stamp.st_mtime_ns, stamp.st_size, cached_record)


def store(
    path: Path,
    record: WorktreeRecord,
    *,
    content: str | None = None,
    capture_before: notifications.Prepared | None = None,
) -> None:
    """Warm a bounded committed snapshot; cache failure never decides observer delivery.

    The writer supplies exact persisted text for receipt evidence. A bounded
    immutable freeze retains exact committed caller values for cache and legacy
    compatibility, without YAML normalization or an unbounded deepcopy.
    Legacy notifications keep their signature and thaw independent records
    after the last record/pair notification hold is released.
    """
    saved = _saved_content(path, content)
    snapshot = None
    size = 0
    try:
        snapshot, size = save_capture.freeze_legacy(record, path=path)
    except Exception:
        logger.exception("Bounded committed record snapshot unavailable")
    try:
        if snapshot is None:
            raise save_capture.CaptureOmitted("bounded-cache-snapshot-unavailable")
        _warm(path, snapshot)
    except Exception:
        # Only the optional accelerator is quarantined, never a canonical write.
        logger.exception("Record cache warming failed after canonical persistence")
        with _cache_lock:
            _cache.pop(str(path), None)
    notifications.committed(
        path,
        saved,
        capture_before,
        legacy_snapshot=snapshot,
        legacy_bytes=size,
        reporting_context=getattr(record, "_reporting_context", None),
    )


def clear() -> None:
    """Drop every cached entry (tests only)."""
    with _cache_lock:
        _cache.clear()
