"""Preserve original slot identity before selecting a direct monitor interpreter."""

from __future__ import annotations

import logging
from pathlib import Path


def retained_monitor_argv(argv: list[str]) -> list[str]:
    slot = Path(argv[0]).absolute().parent.parent
    if slot.parent.name != "versions" or slot.name == "dev":
        return list(argv)
    bootstrap = slot / ".runtime-bootstrap"
    if not bootstrap.exists():
        logging.getLogger(__name__).warning(
            "Legacy monitor runtime has no process-retention bootstrap: %s", slot,
        )
        return list(argv)
    for name in ("runtime_launcher.py", "versioned_runtime.py"):
        path = bootstrap / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"Monitor process-retention bootstrap is invalid: {path}")
    if bootstrap.is_symlink():
        raise RuntimeError(f"Monitor process-retention bootstrap is linked: {bootstrap}")
    return [argv[0], "-I", "-S", "-B", str(bootstrap / "runtime_launcher.py"), *argv[1:]]
