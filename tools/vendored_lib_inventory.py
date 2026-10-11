"""Distinguish vendored source trees from retired build/cache remnants."""

from __future__ import annotations

from pathlib import Path

_ARTIFACT_DIRS = {
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
    ".venv",
    "build",
    "dist",
}


def is_artifact_only(path: Path) -> bool:
    """Return true only when no source, manifest, or unknown entry survives."""
    if path.is_symlink():
        return False
    for entry in path.iterdir():
        if entry.is_symlink():
            return False
        if entry.is_dir():
            if entry.name in _ARTIFACT_DIRS or entry.name.endswith(".egg-info"):
                continue
            if is_artifact_only(entry):
                continue
        return False
    return True
