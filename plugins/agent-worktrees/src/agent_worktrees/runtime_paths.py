"""Interpreter resolution for legacy flat and nested application slots."""

from __future__ import annotations

from pathlib import Path


def resolve_venv_python(root: Path, *, windows: bool) -> Path:
    """Resolve current, last-known-good, then newest installed interpreter."""
    sub = ("Scripts", "python.exe") if windows else ("bin", "python")

    def interpreter(version: str) -> Path:
        slot = root / "versions" / version
        nested = slot / ".venv" / sub[0] / sub[1]
        return nested if nested.exists() else slot / sub[0] / sub[1]

    try:
        ver = (root / "current-version").read_text("utf-8").strip()
    except OSError:
        ver = ""
    if ver:
        p = interpreter(ver)
        if p.exists():
            return p
    try:
        last_known_good = (root / "last-known-good").read_text("utf-8").strip()
    except OSError:
        last_known_good = ""
    if last_known_good:
        p = interpreter(last_known_good)
        if p.exists():
            return p
    versions = root / "versions"
    if versions.is_dir():
        for slot in sorted(versions.iterdir(), reverse=True):
            p = interpreter(slot.name)
            if p.exists():
                return p
    # No installed slot -- return the canonical (marker or empty) slot path.
    return interpreter(ver or last_known_good or "")
