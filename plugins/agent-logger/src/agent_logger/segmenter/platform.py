"""Platform + filesystem helpers for the segmenter.

A de-facility-ized subset of what the aperture-labs ``facility_lib`` module
provided: platform detection, machine-name detection, and NTFS-safe
filename sanitization. No NAS paths, no facility hostnames -- machine
naming is overridable via configuration.
"""

from __future__ import annotations

import os
import platform as _platform
import re
import socket
from pathlib import Path


def _detect_wsl() -> bool:
    """Detect whether running inside Windows Subsystem for Linux."""
    if _platform.system() == "Windows":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        proc_version = Path("/proc/version")
        if proc_version.exists() and "microsoft" in proc_version.read_text().lower():
            return True
    except OSError:
        pass
    return False


IS_WINDOWS: bool = _platform.system() == "Windows"
IS_WSL: bool = _detect_wsl()


def detect_machine() -> str:
    """Detect the current machine name, lowercased; ``-wsl`` suffix in WSL."""
    hostname = socket.gethostname().lower()
    return f"{hostname}-wsl" if IS_WSL else hostname


_NTFS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def sanitize_path_component(
    name: str,
    *,
    default: str = "Untitled",
    max_length: int = 80,
) -> str:
    """Sanitize a single filename component for NTFS compatibility.

    Replaces or removes characters invalid on NTFS/Windows
    (``< > : " / \\ | ? *`` and ASCII control characters) and guards
    against reserved device names. Operates on one path component, not a
    full path. Returns *default* if the result would be empty.
    """
    text = name.strip()
    text = text.replace(":", " -")
    text = text.replace("/", "-").replace("\\", "-")
    text = text.replace('"', "'")
    text = re.sub(r"[\x00-\x1f<>|?*]", "", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"-{2,}", "-", text)
    text = re.sub(r"^[\s\-'_.]+$", "", text)
    if not text:
        text = default
    stem = text.split(".")[0].upper()
    if stem in _NTFS_RESERVED_NAMES:
        text = f"_{text}"
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    return text or default
