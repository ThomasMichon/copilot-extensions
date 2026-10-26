"""Locate an already-built, package-owned Linux kernel binding."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import stat
import sys


def native_helper_path() -> Path:
    architecture = platform.machine()
    if not sys.platform.startswith("linux") or architecture not in ("x86_64", "aarch64"):
        raise OSError("unsupported daemon native platform")
    candidate = Path(__file__).resolve().parent / "native" / f"linux-{architecture}" / "daemon-instance"
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        raise OSError("packaged daemon native artifact unavailable; build explicitly") from None
    if (
        not stat.S_ISREG(info.st_mode) or candidate.is_symlink()
        or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o6022
        or not os.access(candidate, os.X_OK)
    ):
        raise OSError("packaged daemon native artifact custody unavailable")
    return candidate
