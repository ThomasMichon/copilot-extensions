"""Shared install-root helpers for agent-bridge runtime state."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

INSTALL_DIR_ENV = "AGENT_BRIDGE_INSTALL_DIR"
CONFIG_DIR_ENV = "AGENT_BRIDGE_CONFIG_DIR"
LEGACY_INSTALL_DIRNAME = ".agent-bridge"
ELEVATED_SUBDIR = "elevated"
SCOPED_SERVICE_HASH_LENGTH = 12


def normalized_path(path: Path) -> str:
    raw = os.path.abspath(os.fspath(path.expanduser()))
    if os.name == "nt":
        return os.path.normcase(raw).replace("/", "\\")
    return os.path.normpath(raw)


def legacy_install_dir() -> Path:
    """The historic machine-global agent-bridge install root."""
    return Path.home() / LEGACY_INSTALL_DIRNAME


def install_dir() -> Path:
    """The primary agent-bridge install root for this process."""
    override = os.environ.get(INSTALL_DIR_ENV)
    if override:
        return Path(override).expanduser()

    config_override = os.environ.get(CONFIG_DIR_ENV)
    if config_override:
        candidate = Path(config_override).expanduser()
        if candidate.name.casefold() == ELEVATED_SUBDIR:
            return candidate.parent

    return legacy_install_dir()


def effective_config_dir() -> Path:
    """The active config/state root for this process."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return install_dir()


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


def scheduled_task_name(path: Path | None = None) -> str:
    """Windows scheduled-task identity for the primary daemon."""
    suffix = installation_suffix(path)
    return "Agent Bridge" if not suffix else f"AgentBridge-{suffix}"


def systemd_unit_name(path: Path | None = None) -> str:
    """POSIX systemd user-unit identity for the primary daemon."""
    suffix = installation_suffix(path)
    return "agent-bridge.service" if not suffix else f"agent-bridge-{suffix}.service"


def elevated_task_name(path: Path | None = None) -> str:
    """Windows scheduled-task identity for the elevated sub-daemon."""
    suffix = installation_suffix(path)
    return "agent-bridge-elevated" if not suffix else f"agent-bridge-elevated-{suffix}"
