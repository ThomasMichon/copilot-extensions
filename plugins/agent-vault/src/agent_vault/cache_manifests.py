"""Read configured entry/field pairs without resolving or fetching credentials."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


def iter_cache_manifest(path: Path) -> Iterator[tuple[str, str]]:
    """Parse ``entry | field`` lines (``#`` comments) from a manifest file."""
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            entry, sep, field = line.partition("|")
            entry = entry.strip()
            field = field.strip() if sep else "password"
            if entry:
                yield entry, field or "password"


def read_cache_manifest(path: Path) -> list[tuple[str, str]]:
    """Return all parsed manifest references."""
    return list(iter_cache_manifest(path))
