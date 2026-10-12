"""Tiny per-call memoization helper.

picker-performance-and-responsiveness Phase 4: ``resolve_active_plugins``
calls ``_local_root`` once per *enabled plugin source*, not once per
*marketplace* -- a marketplace shared by N enabled plugins otherwise paid N
redundant re-reads/re-parses of the same manifest file per resolution pass
(measured: ~56 duplicate reads in one cold Picker boot). :func:`memo_get`
lets a caller share one computed value across calls within a single
short-lived process invocation; :class:`MarketplaceMemo` bundles the two
caches `_local_root` needs. Never shared across processes or calls -- a
fresh instance per `resolve_active_plugins()` call, so there is no
staleness window to reason about.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from plugin_resolve import Marketplace

K = TypeVar("K")
V = TypeVar("V")


def memo_get(cache: dict[K, V] | None, key: K, loader: Callable[[], V]) -> V:
    """``loader()``, memoized in ``cache`` by ``key``. ``cache=None`` is a
    pure passthrough (always calls ``loader()``) -- the no-caching default
    every other/test caller gets."""
    if cache is not None and key in cache:
        return cache[key]
    value = loader()
    if cache is not None:
        cache[key] = value
    return value


@dataclass
class MarketplaceMemo:
    """Caches ``_local_root`` shares across one `resolve_active_plugins()`
    pass, keyed on canonical marketplace root."""

    manifests: dict[Path, tuple[Path | None, dict | None, str | None, bool]] = field(
        default_factory=dict
    )
    marketplaces: dict[Path, Marketplace | None] = field(default_factory=dict)
