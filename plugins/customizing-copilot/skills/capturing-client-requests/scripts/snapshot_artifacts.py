"""Private, exclusive local output files shared by capture and offline analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path


def local_output(path: Path) -> Path:
    expanded = path.expanduser()
    if os.name == "nt" and str(expanded).startswith(("\\\\", "//")):
        raise ValueError("output must be on a local drive, not a UNC share")
    parent = expanded.parent.resolve(strict=True)
    if os.name == "nt":
        import ctypes

        if ctypes.windll.kernel32.GetDriveTypeW(str(parent.anchor)) == 4:
            raise ValueError("output must not be on a mapped network drive")
    if not parent.is_dir():
        raise ValueError("output parent must be an existing directory")
    return parent / expanded.name


def reserve_output(path: Path) -> int:
    return os.open(local_output(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)


def write_report(descriptor: int, report: dict) -> None:
    encoded = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    with os.fdopen(descriptor, "wb", closefd=False) as stream:
        stream.write(encoded)
        stream.flush()
