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
``activity.jsonl`` log -- tens of MB after a few days of multi-session use)
and ``handoff_trace.read_trace()`` (a per-worktree durable trace file) each
re-read and re-``json.loads`` their *entire* file from scratch on every call,
with no caching, every sweep tick, for every worktree carrying a pending
handoff -- not merely a parse-speed problem, a volume one, exactly like
#3751's own diagnosis.

Deliberately NOT a TTL/staleness cache: a file changed since the cached
stamp is reflected on its very next read, no staleness window is ever
tolerated. A log is append-only in practice, but this cache makes no such
assumption -- any content change invalidates it the same way.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, int, object]] = {}


def cached_parse(path: Path, parser: Callable[[Path], list[dict]]) -> list[dict]:
    """``parser(path)``, memoized on the file's own ``(mtime_ns, size)``.

    Returns an independent ``copy.deepcopy`` on every call -- a cache HIT
    must look exactly like a fresh parse to its caller. Mirrors
    ``record_cache.cached_load``'s own default: a caller that mutates one
    returned dict in place (``read_trace()`` previously handed back the
    cache's own list directly; a caller mutating an event would leak that
    mutation into every later reader, never written to disk) must never be
    able to corrupt what a later cache hit hands back to a different
    caller. The copy cost is paid on every hit, not just a miss -- cheap
    here (a plain list of small dicts), unlike a full ``WorktreeRecord``.

    A missing file parses (and caches) as whatever ``parser`` returns for a
    nonexistent path (typically an empty list) -- callers are expected to
    handle that the same way they always have, cache or not.
    """
    key = str(path)
    try:
        st = path.stat()
    except OSError:
        with _cache_lock:
            _cache.pop(key, None)
        return parser(path)
    stamp = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and (cached[0], cached[1]) == stamp:
            return copy.deepcopy(cached[2])
    parsed = parser(path)
    with _cache_lock:
        _cache[key] = (stamp[0], stamp[1], parsed)
    return copy.deepcopy(parsed)


def clear() -> None:
    """Drop every cached entry (tests only)."""
    with _cache_lock:
        _cache.clear()


def invalidate(path: Path) -> None:
    """Drop one path's cached entry, if present.

    For a caller that just **replaced or recreated** the file out from
    under the passive ``(mtime_ns, size)`` stamp -- ``activity._prune()``
    rewrites ``activity.jsonl`` in place, and ``handoff_trace.
    remove_trace()`` deletes a worktree's trace file specifically so a
    later-reused worktree id can never inherit a predecessor's events.
    Both can coincidentally reproduce the exact previous ``(mtime_ns,
    size)`` pair on a filesystem with coarse mtime resolution, which the
    passive stat-based check alone cannot distinguish from "unchanged" --
    an explicit invalidation at the one call site that performed the
    replace/delete closes that gap instead of relying on the stamp to
    always differ. Best-effort / idempotent: no-ops if the path was never
    cached.
    """
    with _cache_lock:
        _cache.pop(str(path), None)
