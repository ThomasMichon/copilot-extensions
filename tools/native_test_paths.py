"""Native filesystem spelling for absolute, caller-owned test paths."""

from __future__ import annotations

import os
from pathlib import Path


def native_test_path(path: Path) -> Path:
    if os.name != "nt" or str(path).startswith("\\\\?\\"):
        return path
    value = str(path)
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)
