"""Unambiguous Git framing without inventing lossy stored path identities."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def decode_git_path(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        log.warning("Skipping unsupported non-UTF-8 Git path: %r", raw)
        return None


def nul_git_paths(raw: bytes) -> list[str]:
    if raw and not raw.endswith(b"\0"):
        raise ValueError("Incomplete Git path listing")
    paths = []
    for value in raw.split(b"\0"):
        if value:
            path = decode_git_path(value)
            if path is not None:
                paths.append(path)
    return paths


def commit_records(output: str) -> list[list[str]]:
    """Git's -z separates records; NUL also separates each of six fields."""
    if not output:
        return []
    fields = output.split("\0")
    if fields[-1] != "" or (len(fields) - 1) % 6:
        raise ValueError("Incomplete Git commit record")
    return [fields[offset:offset + 6] for offset in range(0, len(fields) - 1, 6)]
