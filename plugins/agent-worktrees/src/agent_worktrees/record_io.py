"""Shared single-record YAML I/O, without cache or mutation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


def read_record_mapping(path: Path) -> dict:
    """Read once, retaining the canonical in-memory control-character repair."""
    from . import tracking

    raw = tracking._read_text_with_retry(path)
    try:
        data = tracking._yaml_safe_load(raw)
    except yaml.reader.ReaderError:
        # Repair only illegal control characters, not unrelated malformed YAML.
        repaired = tracking._strip_control_chars(raw)
        if repaired == raw:
            raise
        data = tracking._yaml_safe_load(repaired)
    if not isinstance(data, dict):
        raise yaml.YAMLError("worktree tracking record must be a YAML mapping")
    return data


def load_record_uncached(path: Path) -> WorktreeRecord:
    """The uncached loader used by tracking.load_record's record cache."""
    from . import tracking

    return tracking.parse_record_data(read_record_mapping(path), path=path)
