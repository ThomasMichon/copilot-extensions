"""Stdlib-only execution platform classification shared with payload callers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _read_proc(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _container_root(mountinfo: str) -> bool:
    for line in mountinfo.splitlines():
        mount, separator, filesystem = line.partition(" - ")
        fields, kind = mount.split(), filesystem.split()
        if (
            separator and len(fields) > 4 and fields[4] == "/" and kind
            and kind[0] in {
                "overlay", "fuse.overlayfs", "fuse-overlayfs",
                "fuse.fuse-overlayfs", "fuse.fuse.overlayfs",
            }
        ):
            return True
    return False


def detect_platform() -> str:
    """Classify execution roots; refuse unconfirmed private-root WSL identity."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        if any(Path(marker).is_file() for marker in ("/.dockerenv", "/run/.containerenv")):
            return "linux"
        cgroup = _read_proc("/proc/1/cgroup")
        if any(tag in cgroup for tag in ("/docker", "/kubepods", "containerd", "/lxc", "libpod")):
            return "linux"
        wsl_kernel = "microsoft" in os.uname().release.casefold()
        if not wsl_kernel:
            try:
                with open(Path("/proc/version"), encoding="utf-8") as stream:
                    wsl_kernel = "microsoft" in stream.read().casefold()
            except OSError:
                pass
        if not wsl_kernel or _container_root(_read_proc("/proc/self/mountinfo")):
            return "linux"
        private_cgroup = not cgroup.strip() or all(
            line.rsplit(":", 1)[-1] == "/" for line in cgroup.strip().splitlines()
        )
        if private_cgroup and not Path("/proc/sys/fs/binfmt_misc/WSLInterop").is_file():
            raise ValueError("execution platform is unconfirmed: private WSL root lacks interop evidence")
        return "wsl"
    return "linux"
