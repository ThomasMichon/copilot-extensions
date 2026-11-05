"""Standalone native prerequisite owner; no Worktrees runtime or configuration."""

from __future__ import annotations

from collections.abc import Callable
import os
import platform
import shutil
import subprocess


_TMUX_INSTALL_CANDIDATES: tuple[tuple[str, list[str]], ...] = (
    ("apt-get", ["apt-get", "install", "-y", "tmux"]),
    ("dnf", ["dnf", "install", "-y", "tmux"]),
    ("yum", ["yum", "install", "-y", "tmux"]),
    ("apk", ["apk", "add", "tmux"]),
)


def ensure_mux_available(
    mux: str | None = None, *, available: Callable[[str | None], bool] | None = None,
) -> bool:
    """Best-effort existing POSIX TMux setup, never interactive privilege escalation."""
    if available is None:
        def available(selected):
            return shutil.which(selected or ("psmux" if platform.system() == "Windows" else "tmux")) is not None
    if available(mux):
        return True
    if platform.system() == "Windows":
        return False
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    sudo = None if is_root else shutil.which("sudo")
    if not is_root and not sudo:
        return False
    for tool, install_argv in _TMUX_INSTALL_CANDIDATES:
        if not shutil.which(tool):
            continue
        argv = list(install_argv) if is_root else [sudo, "-n", *install_argv]
        try:
            subprocess.run(argv, capture_output=True, timeout=180, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if available(mux):
            return True
    return False
