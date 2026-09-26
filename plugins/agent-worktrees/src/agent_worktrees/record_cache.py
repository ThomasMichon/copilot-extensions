"""Per-process memoization for ``tracking.load_record`` reads.

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


def cached_load(path: Path, loader: Callable[[Path], "WorktreeRecord"]) -> "WorktreeRecord":
    """``loader(path)``, memoized on the file's own ``(mtime_ns, size)``.

    Returns an independent copy each time -- ``WorktreeRecord`` is a plain
    (non-frozen) ``@dataclass``, and callers throughout ``tracking`` and its
    consumers routinely mutate a record in place after reading it (e.g. to
    stage a write). Sharing one cached instance across callers would let one
    caller's in-place mutation silently corrupt what a later cache hit hands
    back to a different caller -- returning a fresh ``copy.deepcopy`` per hit
    keeps the cache purely a read-parse accelerator, never a shared-mutable-
    state hazard.
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
            return copy.deepcopy(cached[2])
    rec = loader(path)
    with _cache_lock:
        _cache[key] = (stamp[0], stamp[1], rec)
    return copy.deepcopy(rec)


def clear() -> None:
    """Drop every cached entry (tests only)."""
    with _cache_lock:
        _cache.clear()
