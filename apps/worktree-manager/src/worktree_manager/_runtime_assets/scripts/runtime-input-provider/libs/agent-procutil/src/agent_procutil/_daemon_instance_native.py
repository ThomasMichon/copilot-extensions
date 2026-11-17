"""Fail explicitly for callers still requesting the retired compiled binding."""

from __future__ import annotations

from pathlib import Path


def native_helper_path() -> Path:
    raise OSError("compiled daemon helpers are retired; use the shared Python lifecycle entrypoint")
