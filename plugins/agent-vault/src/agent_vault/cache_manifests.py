"""Read configured entry/field pairs without resolving or fetching credentials."""

from __future__ import annotations

from pathlib import Path


def read_cache_manifest(path: Path) -> list[tuple[str, str]]:
    """Parse ``entry | field`` lines (``#`` comments) from a manifest file."""
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry, sep, field = line.partition("|")
        entry = entry.strip()
        field = field.strip() if sep else "password"
        if entry:
            pairs.append((entry, field or "password"))
    return pairs
