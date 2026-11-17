"""Read configured entry/field pairs without resolving or fetching credentials."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .cache_limits import WorkBudget


def iter_cache_manifest(
    path: Path, *, budget: WorkBudget | None = None,
) -> Iterator[tuple[str, str]]:
    """Parse ``entry | field`` lines (``#`` comments) from a manifest file."""
    if budget is not None and not budget.available():
        return
    with path.open(encoding="utf-8") as stream:
        while budget is None or budget.available():
            raw = stream.readline()
            if not raw:
                return
            if budget is not None and not budget.consume():
                return
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            entry, sep, field = line.partition("|")
            entry = entry.strip()
            field = field.strip() if sep else "password"
            if not entry:
                raise ValueError("cache manifest row has no entry")
            yield entry, field or "password"


def read_cache_manifest(path: Path) -> list[tuple[str, str]]:
    """Return all parsed manifest references."""
    return list(iter_cache_manifest(path))
