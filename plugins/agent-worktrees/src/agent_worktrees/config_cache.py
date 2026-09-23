"""Opt-in same-pass memoization for :func:`agent_worktrees.config.load_config`.

Split out of ``config.py`` to keep that module under its module-size baseline
(dotfiles/check-module-size) -- this is a small, self-contained concern, not
part of the config-loading logic itself.

``load_config()``'s control-plane related-PR discovery
(``_control_plane_related_pr_map``) is expensive and uncached -- profiled at
several real seconds per call on a fleet with many registered repos. Most
callers invoke it once per process and that is a reasonable one-time cost,
but some call chains -- notably the Worktree Manager's live picker setup
(``_prepare_live_source``), which independently asks for the roster, the
profiles-matrix axes, and the REPO/BRANCH topbar fields -- invoke
``load_config()`` several times in a single logical pass, redundantly
repeating that discovery every time (observed: 11 calls in one pass, ~40s
total, in a fleet with 15+ registered repos).

``cached_load_config_scope()`` is an explicit, opt-in memoization window: a
caller that knows it is about to make several ``load_config()`` calls that
should agree on one point-in-time answer wraps that pass in the context
manager, and every memoized call in the SAME thread during that window is
cached by its exact argument signature. Every caller that does not opt in
sees the wrapped function's ordinary always-fresh behavior, unchanged; this
cannot affect existing tests or invalidation semantics. Not
thread-propagated: a ``threading.Thread`` started from inside the scope does
NOT inherit it (Python's ``contextvars`` give each new thread a fresh
top-level context), so a caller that fans work out to worker threads must
enter the scope on each thread that wants the memoization.
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

_scope_cache: contextvars.ContextVar[dict[Any, Any] | None] = contextvars.ContextVar(
    "_agent_worktrees_config_cache_scope", default=None
)


@contextlib.contextmanager
def cached_load_config_scope():
    """Memoize every :func:`memoize_in_scope` call (same thread) for one pass."""
    token = _scope_cache.set({})
    try:
        yield
    finally:
        _scope_cache.reset(token)


def memoize_in_scope(
    fn: Callable[..., _T],
    *args: Any,
    **kwargs: Any,
) -> _T:
    """Call ``fn(*args, **kwargs)``, memoized inside an active scope.

    Outside :func:`cached_load_config_scope`, always calls ``fn`` fresh (a
    cache miss every time). Inside one, repeats of the exact same
    ``(fn, args, kwargs)`` signature return the already-computed result
    instead of re-running ``fn``.
    """
    cache = _scope_cache.get()
    if cache is None:
        return fn(*args, **kwargs)
    key = (fn, args, tuple(sorted(kwargs.items())))
    if key not in cache:
        cache[key] = fn(*args, **kwargs)
    return cache[key]
