"""Stdlib-only execution platform classification shared with payload callers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def detect_platform() -> str:
    """Classify execution roots, not a container's shared host kernel or env."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        if any(Path(marker).is_file() for marker in ("/.dockerenv", "/run/.containerenv")):
            return "linux"
        try:
            cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="replace")
        except OSError:
            cgroup = ""
        if any(tag in cgroup for tag in ("/docker", "/kubepods", "containerd", "/lxc", "libpod")):
            return "linux"
        if "microsoft" in os.uname().release.casefold():
            return "wsl"
        try:
            with open(Path("/proc/version"), encoding="utf-8") as stream:
                if "microsoft" in stream.read().casefold():
                    return "wsl"
        except OSError:
            pass
    return "linux"
