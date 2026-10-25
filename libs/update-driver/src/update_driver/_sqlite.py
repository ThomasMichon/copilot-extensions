"""SQLite file URIs without Windows filesystem namespace authorities."""

from __future__ import annotations

from pathlib import PurePath, PureWindowsPath
from typing import Literal


def database_uri(path: PurePath, *, mode: Literal["ro", "rw", "rwc"]) -> str:
    """Preserve the selected database and access mode across path spellings."""
    if isinstance(path, PureWindowsPath) and str(path).startswith("\\\\?\\"):
        value = str(path)
        path = PureWindowsPath(
            "\\\\" + value[8:] if value[:8].lower() == "\\\\?\\unc\\" else value[4:],
        )
    return f"{path.as_uri()}?mode={mode}"
