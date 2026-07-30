"""Shared JSON helpers for the agent-index query surface."""

from __future__ import annotations

from typing import Any


def format_error(exc: BaseException) -> str:
    """Return a short, traceback-free error string for CLI/API responses."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def hit_to_dict(hit: Any) -> dict[str, Any]:
    """Convert a SearchResult-like object or mapping to the public hit shape."""
    chunk_id = _field(hit, "chunk_id", _field(hit, "id", ""))
    return {
        "id": chunk_id,
        "chunk_id": chunk_id,
        "score": _field(hit, "score", 0.0),
        "file_path": _field(hit, "file_path", ""),
        "line_start": _field(hit, "line_start", None),
        "line_end": _field(hit, "line_end", None),
        "source": _field(hit, "source", ""),
        "chunk_type": _field(hit, "chunk_type", ""),
        "language": _field(hit, "language", ""),
        "content": _field(hit, "content", ""),
    }


def _field(hit: Any, name: str, default: Any) -> Any:
    if isinstance(hit, dict):
        return hit.get(name, default)
    return getattr(hit, name, default)
