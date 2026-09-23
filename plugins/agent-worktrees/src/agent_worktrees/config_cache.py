"""Opt-in memoization for :func:`agent_worktrees.config.load_config`.

Split out of ``config.py`` to keep that module under its module-size baseline
(dotfiles/check-module-size) -- this is a small, self-contained concern, not
part of the config-loading logic itself.

``load_config()``'s control-plane related-PR discovery
(``_control_plane_related_pr_map``) is expensive and uncached -- profiled at
several real seconds per call on a fleet with many registered repos. Most
callers invoke it once per process and that is a reasonable one-time cost,
but some call chains -- notably the Worktree Manager's live picker, which
independently asks for the roster, the profiles-matrix axes, and the
REPO/BRANCH topbar fields from more than one thread and on more than one
occasion in a session (initial live setup, the background pivot prewarm, and
every manual 'r' reload) -- invoke ``load_config()`` many times over a
session, redundantly repeating that discovery every time (observed: 11 calls
in one pass, ~40s total, in a fleet with 15+ registered repos).

Two ways to opt in, same underlying memoization:

- :func:`cached_load_config_scope` -- a quick, single-thread, single-pass
  window for a caller that just wants one point-in-time answer shared across
  a few calls it makes itself, right here, right now.
- :class:`ConfigCacheSession` -- an explicit, caller-owned, TTL-bounded cache
  a caller can hold for as long as it likes (e.g. one Picker launch) and
  enter from *multiple threads*, since it is a plain object reference the
  caller passes around rather than something threaded implicitly. This is
  the "centralized cache" seam for a long-lived, multi-thread consumer: every
  thread that calls ``session.scope()`` shares the same entries, so the
  Picker's async roster-fill thread and its independent pivot-prewarm thread
  (and a later manual reload) all draw on one warm cache instead of each
  paying the discovery cost separately.

Every caller that does not opt in -- the overwhelming majority of
``load_config()`` callers across the CLI -- sees its ordinary always-fresh
behavior, unchanged; this cannot affect existing tests, invalidation
semantics, or a write-then-read call sequence anywhere else in the codebase.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

# The "current cache" a memoized call resolves against, if any. Holds a
# ConfigCacheSession (never a bare dict) so cached_load_config_scope() and
# ConfigCacheSession.scope() share one lookup path in memoize_in_scope().
# NOT thread-propagated: a threading.Thread started from inside a scope does
# NOT inherit it (Python's contextvars give each new thread a fresh top-level
# context) -- a caller that fans work out to worker threads must explicitly
# call session.scope() again on each thread that wants to share the cache
# (that is exactly what makes ConfigCacheSession safe to hold and enter from
# more than one thread: nothing propagates by accident).
_current_session: contextvars.ContextVar["ConfigCacheSession | None"] = (
    contextvars.ContextVar("_agent_worktrees_config_cache_session", default=None)
)


class ConfigCacheSession:
    """A caller-owned, thread-safe, TTL-bounded memoization cache.

    Create one and hold it for as long as its entries should stay valid (a
    single CLI invocation's one-off use is :func:`cached_load_config_scope`
    below; a long-lived consumer -- e.g. one Picker launch -- holds its own
    session and enters ``.scope()`` on every thread that should share it).
    An entry older than ``ttl`` seconds is treated as a miss and recomputed;
    ``ttl=None`` never expires an entry for this session's lifetime.
    """

    def __init__(self, ttl: float | None = None) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[Any, tuple[float, Any]] = {}

    @contextlib.contextmanager
    def scope(self):
        """Make this session the active memoization target on this thread."""
        token = _current_session.set(self)
        try:
            yield self
        finally:
            _current_session.reset(token)

    def get_or_compute(self, key: Any, compute: Callable[[], _T]) -> _T:
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                stamp, value = hit
                if self._ttl is None or (now - stamp) < self._ttl:
                    return value
        value = compute()
        with self._lock:
            self._entries[key] = (now, value)
        return value

    def invalidate(self) -> None:
        """Drop every cached entry (e.g. after a config file write)."""
        with self._lock:
            self._entries.clear()


@contextlib.contextmanager
def cached_load_config_scope():
    """A one-off :class:`ConfigCacheSession` scoped to this ``with`` block.

    Memoizes every :func:`memoize_in_scope` call made on THIS thread while
    the block is open (entries never expire -- the block itself bounds their
    lifetime). For a longer-lived or multi-thread cache, use
    :class:`ConfigCacheSession` directly instead.
    """
    with ConfigCacheSession(ttl=None).scope() as session:
        yield session


def memoize_in_scope(
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Call ``fn(*args, **kwargs)``, memoized against the active session.

    With no active session on this thread (the ordinary case for every
    caller that hasn't opted in), always calls ``fn`` fresh. With one active
    (via :func:`cached_load_config_scope` or an entered
    :class:`ConfigCacheSession`), repeats of the exact same
    ``(fn, args, kwargs)`` signature reuse that session's already-computed
    result instead of re-running ``fn``, subject to its TTL.
    """
    session = _current_session.get()
    if session is None:
        return fn(*args, **kwargs)
    key = (fn, args, tuple(sorted(kwargs.items())))
    return session.get_or_compute(key, lambda: fn(*args, **kwargs))
