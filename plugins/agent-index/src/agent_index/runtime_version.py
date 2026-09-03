"""Write the running-version marker for the agent-index service."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import install_dir

RUNNING_VERSION_FILE = "running-version.json"
RUNTIME_VERSION_ENV = "AGENT_INDEX_RUNTIME_VERSION"


def current_runtime_version() -> str:
    """Return the immutable slot identity, falling back to the package version."""
    return os.environ.get(RUNTIME_VERSION_ENV, "").strip() or __version__


def write_running_version(
    directory: Path | None = None,
    *,
    strict: bool = False,
) -> None:
    """Record the running service version and pid on boot."""
    root = directory or install_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": current_runtime_version(),
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        (root / RUNNING_VERSION_FILE).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        if strict:
            raise


def clear_running_version(
    directory: Path | None = None,
    *,
    owner_pid: int | None = None,
) -> None:
    """Remove the running marker only when it still names this process."""
    path = (directory or install_dir()) / RUNNING_VERSION_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    pid = os.getpid() if owner_pid is None else owner_pid
    if isinstance(payload, dict) and payload.get("pid") == pid:
        with suppress(OSError):
            path.unlink()
