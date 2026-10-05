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
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .tracking import WorktreeRecord

_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, int, "WorktreeRecord"]] = {}


_TRANSIENT_PROJECTION_ATTRS = (
    "_session_projection_dirty",
    "_session_projection_initial_registration",
    "_controller_projection_dirty",
)


def cached_load(
    path: Path,
    loader: Callable[[Path], "WorktreeRecord"],
    *,
    copy_result: bool = True,
) -> "WorktreeRecord":
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


def store(path: Path, record: "WorktreeRecord") -> None:
    """Seed/refresh the cache with ``record``, a value THIS process just
    wrote to ``path`` (``agent-worktrees-authoritative-daemon`` effort:
    "whenever a write is posted, ensure subsequent readers see that result
    without another direct read").

    Stamped on ``path``'s CURRENT ``(mtime_ns, size)`` -- call this only
    once the write is durably on disk (e.g. from ``tracking.save_record``,
    still inside its own record lock, right after the atomic write
    completes), so the stamp really reflects what ``record`` contains and
    a concurrent writer can't sneak a stamp mismatch in between. Stores an
    independent ``copy.deepcopy`` (mirrors :func:`cached_load`'s own
    contract): the caller's own ``record`` object is very often mutated
    further after this call returns (e.g. ``record._loaded_from =
    path`` in ``save_record`` itself), and the cache must never be a
    shared-mutable-state hazard.

    The stored copy's own ``_loaded_from`` is stamped to ``path`` here,
    not left as whatever the caller's in-memory ``record`` happened to
    carry: ``tracking.save_record`` calls this BEFORE it sets that
    attribute on its own ``record`` (still inside the lock, to avoid a
    write/stat race -- see the docstring above), so a bare passthrough of
    ``record`` would cache a copy with a stale/absent ``_loaded_from``. A
    later ``load_record`` cache hit handing that copy back to a caller
    that then does ``tracking.save_record(record)`` with no explicit
    ``path`` -- resolving one via ``record.yaml_path`` -- would silently
    resolve to the WRONG file (or raise), not merely carry a cosmetic
    staleness.

    Best-effort: if ``path`` can't be stat'd (the file vanished between the
    write and this call -- exceedingly unlikely under the same lock),
    silently no-ops rather than raising -- a write that already succeeded
    must never be reported as failed merely because this accelerator
    couldn't warm itself afterward; the next :func:`cached_load` call
    simply misses and re-reads, exactly as if this function didn't exist.

    Also strips the transient, never-serialized session/controller
    projection-dirty markers (``_session_projection_dirty`` and friends)
    from the cached copy: ``_save_record_unlocked`` can populate these
    while serializing, and they're cleared on the caller's OWN object only
    AFTER this cache write returns (``_flush_session_projections``, once
    the lock is released) -- so a bare passthrough would cache a copy that
    still looks dirty, and a later cache-hit ``load_record()`` would hand
    that stale dirty state back out, unlike a fresh uncached parse (which
    never carries these attributes at all).
    """
    try:
        st = path.stat()
    except OSError:
        return
    cached_record = copy.deepcopy(record)
    cached_record._loaded_from = path
    for attr in _TRANSIENT_PROJECTION_ATTRS:
        if hasattr(cached_record, attr):
            delattr(cached_record, attr)
    with _cache_lock:
        _cache[str(path)] = (st.st_mtime_ns, st.st_size, cached_record)


def clear() -> None:
    """Drop every cached entry (tests only)."""
    with _cache_lock:
        _cache.clear()
