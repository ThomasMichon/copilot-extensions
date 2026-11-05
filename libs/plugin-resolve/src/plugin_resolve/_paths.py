"""Use native Windows paths for filesystem access without changing identities."""
from __future__ import annotations

import os
from pathlib import Path


def native_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = os.path.abspath(path)
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\.\\"):
        raise ValueError("Device paths are not marketplace evidence")
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def logical_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = str(path)
    if value.casefold().startswith("\\\\?\\unc\\"):
        return Path("\\\\" + value[8:])
    if (
        value.startswith("\\\\?\\") and len(value) >= 7
        and value[4].isalpha() and value[5:7] == ":\\"
    ):
        return Path(value[4:])
    return path
