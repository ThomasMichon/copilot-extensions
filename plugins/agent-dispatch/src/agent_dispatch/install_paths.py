"""Shared install-root helpers for agent-dispatch runtime state."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

INSTALL_DIR_ENV = "AGENT_DISPATCH_INSTALL_DIR"
LEGACY_INSTALL_DIRNAME = ".agent-dispatch"
SCOPED_SERVICE_HASH_LENGTH = 12


def normalized_path(path: Path) -> str:
    raw = os.path.abspath(os.fspath(path.expanduser()))
    if os.name == "nt":
        return os.path.normcase(raw).replace("/", "\\")
    return os.path.normpath(raw)


def legacy_install_dir() -> Path:
    """The historic machine-global agent-dispatch install root."""
    return Path.home() / LEGACY_INSTALL_DIRNAME


def install_dir() -> Path:
    """The active agent-dispatch install root for this process."""
    override = os.environ.get(INSTALL_DIR_ENV)
    return Path(override).expanduser() if override else legacy_install_dir()


def uses_legacy_install_dir(path: Path | None = None) -> bool:
    """Whether ``path`` resolves to the historic machine-global install root."""
    candidate = install_dir() if path is None else path
    return normalized_path(candidate) == normalized_path(legacy_install_dir())


def installation_suffix(path: Path | None = None) -> str:
    """Stable short suffix for non-legacy install roots, empty for legacy."""
    candidate = install_dir() if path is None else path
    if uses_legacy_install_dir(candidate):
        return ""
    return hashlib.sha256(normalized_path(candidate).encode("utf-8")).hexdigest()[
        :SCOPED_SERVICE_HASH_LENGTH
    ]
