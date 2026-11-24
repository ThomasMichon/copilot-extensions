"""Distinguish vendored source trees from retired build/cache remnants."""

from __future__ import annotations

from pathlib import Path

_ARTIFACT_DIRS = {
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "__pycache__",
}
_ROOT_ARTIFACT_DIRS = {".venv", "build", "dist"}


def is_artifact_only(path: Path) -> bool:
    """Return true only when no source, manifest, or unknown entry survives."""
    return _is_artifact_only(path, library_root=True)


def _is_artifact_only(path: Path, *, library_root: bool) -> bool:
    if path.is_symlink():
        return False
    for entry in path.iterdir():
        if entry.is_symlink():
            return False
        if entry.name.endswith(".egg-info"):
            continue
        if entry.is_dir():
            if entry.name in _ARTIFACT_DIRS:
                continue
            if library_root and entry.name in _ROOT_ARTIFACT_DIRS:
                continue
            if _is_artifact_only(entry, library_root=False):
                continue
        return False
    return True
