"""Explicit resident configuration source, independent of process state cwd."""

from __future__ import annotations

import os
from pathlib import Path

REPO_CONFIG_NAME = ".agent-vault.json"
_SERVICE_CONFIGURATION_ROOT: str | None = None


def configuration_root(cwd: str | None = None) -> str:
    return configuration_source(cwd) or os.getcwd()


def configuration_source(cwd: str | None = None) -> str | None:
    return cwd if cwd is not None else _SERVICE_CONFIGURATION_ROOT


def bind_service_configuration(root: str) -> None:
    path = Path(root)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("service configuration root must be an existing absolute directory")
    global _SERVICE_CONFIGURATION_ROOT
    _SERVICE_CONFIGURATION_ROOT = str(path)
