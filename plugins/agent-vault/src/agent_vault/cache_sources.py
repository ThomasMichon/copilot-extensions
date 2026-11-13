"""Collect configured credential targets without scanning a credential backend."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import logging
import time

log = logging.getLogger(__name__)
CacheSource = Callable[[str | None], Iterable[object] | None]


@dataclass
class CacheTargets:
    entries: list[tuple[str, str]] = field(default_factory=list)
    failed_sources: int = 0
    truncated: bool = False


def collect(
    sources: Iterable[CacheSource],
    machine: str | None,
    *,
    max_entries: int | None = None,
    max_items: int | None = None,
    max_sources: int | None = None,
    deadline: float | None = None,
) -> CacheTargets:
    result = CacheTargets()
    if any(bound is not None and bound <= 0 for bound in (max_entries, max_items, max_sources)):
        result.truncated = True
        return result
    seen: set[tuple[str, str]] = set()
    inspected = 0
    for index, source in enumerate(sources):
        if (
            max_sources is not None
            and index >= max_sources
            or deadline is not None
            and time.monotonic() >= deadline
        ):
            result.truncated = True
            break
        try:
            items = iter(source(machine) or ())
            while True:
                if (
                    max_items is not None
                    and inspected >= max_items
                    or deadline is not None
                    and time.monotonic() >= deadline
                ):
                    result.truncated = True
                    return result
                try:
                    item = next(items)
                except StopIteration:
                    break
                inspected += 1
                if isinstance(item, str):
                    pair = (item, "password")
                elif isinstance(item, (tuple, list)) and item:
                    pair = (
                        str(item[0]),
                        str(item[1]) if len(item) >= 2 and item[1] else "password",
                    )
                else:
                    continue
                if pair[0] and pair not in seen:
                    seen.add(pair)
                    result.entries.append(pair)
                    if max_entries is not None and len(result.entries) >= max_entries:
                        result.truncated = True
                        return result
        except Exception as exc:
            result.failed_sources += 1
            log.warning("Configured cache source failed (%s)", type(exc).__name__)
    return result
