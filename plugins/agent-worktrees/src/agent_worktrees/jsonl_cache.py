"""Per-process memoization for append-only JSONL log reads.

Mirrors :mod:`record_cache`'s ``(mtime_ns, size)``-keyed invalidation --
unchanged since the last read -> reuse the cached parse instead of
re-reading + re-parsing the whole file -- but scoped to a plain parsed
``list[dict]`` (an activity/trace log line-stream) rather than a
``WorktreeRecord``.

copilot-extensions#3751 added this pattern for ``tracking.list_records()``'s
fleet-wide YAML reparse; the resident status-monitor's handoff-retire sweep
(``_pending_handoff_retire_requests``) turned out to have the same shape of
bug in a different call path: ``activity.read_events()`` (the machine-global
``activity.jsonl`` log -- tens of MB / 90k+ lines after a few days of multi-
session use) and ``handoff_trace.read_trace()`` (a per-worktree durable
trace file) each re-read and re-``json.loads`` their *entire* file from
scratch on every call, with no caching, every sweep tick, for every worktree
carrying a pending handoff -- not merely a parse-speed problem, a volume
one, exactly like #3751's own diagnosis.

Deliberately NOT a TTL/staleness cache: a file changed since the cached
stamp is reflected on its very next read, no staleness window is ever
tolerated. A log is append-only in practice, but this cache makes no such
assumption -- any content change invalidates it the same way.

**Cross-process safety.** This cache is per-process memory -- each process
(the resident status-monitor, an ordinary CLI invocation, a detached
background worker) holds its own independent ``_cache`` dict, with no
shared memory or IPC between them. ``invalidate()`` therefore cannot, by
itself, make a REPLACEMENT visible to a different process's cache: the
real `activity._prune()` call runs inside a detached
``activity-prune-worker`` subprocess, not the resident monitor's own
process, so an ``invalidate()`` call there only clears that worker's own
cache. The stamp itself closes this gap instead: it includes
``(st_dev, st_ino)`` alongside ``(mtime_ns, size)``, and both
``activity._prune()``'s atomic ``tmp.replace(path)`` and a worktree-id
reuse recreating a deleted trace file allocate a NEW inode at that path --
so ANY process's next ``cached_parse`` call naturally sees a stamp
mismatch and re-parses, with no cross-process signal required.
``invalidate()`` remains a same-process optimization (an immediate miss
instead of waiting for that process's own next stat-based check) and a
defense-in-depth backstop should two unrelated files ever coincidentally
share one path's stamp, but the stamp's own device+inode component is the
primary guarantee, not `invalidate()`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

_cache_lock = threading.Lock()
_Stamp = tuple[int, int, int, int]  # (st_dev, st_ino, st_mtime_ns, st_size)
_cache: dict[str, tuple[_Stamp, list[dict]]] = {}


def _stamp(path: Path) -> _Stamp:
    st = path.stat()
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def cached_parse(path: Path, parser: Callable[[Path], list[dict]]) -> list[dict]:
    """``parser(path)``, memoized on the file's own ``(st_dev, st_ino,
    mtime_ns, size)`` -- see this module's "Cross-process safety" note
    above for why device+inode, not just mtime+size, is load-bearing here.

    Returns the cache's own list, by reference -- see this module's
    "Mutation isolation invariant" below. The caller must copy whatever
    bounded subset it actually hands onward to ITS OWN caller, not this
    whole list.

    A missing file parses (and caches) as whatever ``parser`` returns for a
    nonexistent path (typically an empty list) -- callers are expected to
    handle that the same way they always have, cache or not.

    **Mutation isolation invariant.** Deep-copying the full parsed log on
    every hit would reintroduce an O(log size) cost on every cache hit,
    defeating the point for a 90k-line log, so this function does not --
    a caller must never mutate a dict it receives in place. This module's
    own callers (``activity.read_events``, ``handoff_trace.read_trace``)
    each copy only the small, bounded subset they actually return (the
    matched/filtered/tail-limited result), never the full cached list, to
    give their own callers an independent result without paying the
    full-log copy cost.
    """
    key = str(path)
    try:
        stamp = _stamp(path)
    except OSError:
        with _cache_lock:
            _cache.pop(key, None)
        return parser(path)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    parsed = parser(path)
    with _cache_lock:
        _cache[key] = (stamp, parsed)
    return parsed


def clear() -> None:
    """Drop every cached entry (tests only)."""
    with _cache_lock:
        _cache.clear()


def invalidate(path: Path) -> None:
    """Drop one path's cached entry in THIS process, if present.

    A same-process optimization and defense-in-depth backstop, not the
    primary safety guarantee against a same-path replace/recreate -- see
    this module's "Cross-process safety" note above: the stamp's own
    device+inode component is what makes a replacement visible to every
    process's cache (including ones this call can never reach), not this
    function. Best-effort / idempotent: no-ops if the path was never
    cached in this process.
    """
    with _cache_lock:
        _cache.pop(str(path), None)
