"""Per-process memoization for append-only JSONL log reads.

Mirrors :mod:`record_cache`'s ``(mtime_ns, size)``-keyed invalidation --
unchanged since the last read -> reuse the cached parse instead of
re-reading + re-parsing the whole file -- but scoped to a plain parsed
``list[dict]`` (an activity/trace log line-stream) rather than a
``WorktreeRecord``, so it carries no dataclass-specific copy/attribute
handling.

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

import threading
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_cache_lock = threading.Lock()
_cache: dict[str, tuple[int, int, object]] = {}


def cached_parse(path: Path, parser: Callable[[Path], T]) -> T:
    """``parser(path)``, memoized on the file's own ``(mtime_ns, size)``.

    Returns the cache's own object directly, not a copy: every current
    caller (``activity.read_events``, ``handoff_trace.read_trace``) only
    reads/filters the parsed list and its dicts, builds a NEW output list,
    and never mutates an individual parsed record in place -- unlike
    ``record_cache.cached_load``'s ``WorktreeRecord`` callers, which
    routinely do. If a future caller needs to mutate what it gets back,
    it must copy first; this function does not guess at that cost on every
    hit for callers that don't need it.

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
            return cached[2]  # type: ignore[return-value]
    parsed = parser(path)
    with _cache_lock:
        _cache[key] = (stamp[0], stamp[1], parsed)
    return parsed


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
