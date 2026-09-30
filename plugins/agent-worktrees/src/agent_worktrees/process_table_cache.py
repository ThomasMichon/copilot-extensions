"""Short-TTL memoization for read-only process-table resolution.

Split out of ``reclaim.py`` to respect that module's shrink-only line-count
baseline (``tools/module-size-baseline.json``) rather than growing it --
mirrors ``record_cache.py``'s own split out of ``tracking.py`` for the same
reason.

copilot-extensions#4716 (a #3751 follow-up): the resident status-monitor's
background sweep thread (``worktree_status_daemon.start_sweep_thread``, 10s
cadence) calls ``sessions.verify_worktree_active`` -> ``reclaim.
resolve_bound_copilots`` once per *demanded* (actively-watched) worktree,
back-to-back, within the same sweep pass -- and neither passes a shared
``table``, so each call rebuilds the full process snapshot
(``reclaim.build_process_table()``) from scratch. #3751's own fix (#3755)
addressed the analogous ``list_records()`` reparse volume but explicitly
flagged this process-table rebuild as a separate, unaddressed cost. With N
demanded worktrees this is N full process enumerations per sweep pass
instead of one.

A short TTL (not an mtime/size key like ``record_cache`` uses for files --
there is no single file whose change stamp identifies "the process table
changed") coalesces every read-only resolve within one sweep pass into a
single real snapshot, while staying far more real-time than the 10s sweep
cadence itself governs anyway.

Deliberately NOT used by ``reclaim.build_process_table()`` itself, and not
applied to every one of its callers: several (``reclaim.reap_bound_
copilots``, ``reclaim.teardown_detached_mux``) resolve targets and then act
on them (terminate a pid) in the same call, where a stale snapshot risks a
double-kill or a missed pid whose process already exited. Only
``reclaim.resolve_bound_copilots``'s own default -- read-only by contract,
never a kill-adjacent caller -- goes through :func:`cached` here.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

_TTL_S = 2.0
_lock = threading.Lock()
_cache: tuple[float, dict[int, dict]] | None = None


def cached(
    build: Callable[[], dict[int, dict]], *, ttl: float = _TTL_S,
) -> dict[int, dict]:
    """``build()``, short-TTL-memoized across repeated read-only calls.

    ``build`` is only invoked on a cache miss (nothing cached yet, or the
    cached snapshot is older than ``ttl``); a hit returns the exact same
    dict a prior call already produced.
    """
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache is not None and now - _cache[0] < ttl:
            return _cache[1]
    table = build()
    with _lock:
        _cache = (now, table)
    return table


def clear() -> None:
    """Drop the cached snapshot (tests only)."""
    global _cache
    with _lock:
        _cache = None
