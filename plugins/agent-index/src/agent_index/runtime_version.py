"""Write the running-version marker for the agent-index service."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import install_dir

RUNNING_VERSION_FILE = "running-version.json"


def write_running_version(directory: Path | None = None) -> None:
    """Record the running service version and pid on boot (best effort)."""
    root = directory or install_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": __version__,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        (root / RUNNING_VERSION_FILE).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
