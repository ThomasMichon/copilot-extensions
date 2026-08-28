"""Installer/readiness contract adapter for agent-worktrees."""

from __future__ import annotations

import json
import sys
from typing import Any

MODULE_ID = "agent-worktrees/runtime"


def evaluate() -> dict[str, Any]:
    """Return runtime readiness without requiring project configuration."""
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": "ready",
        "detail": (
            "The agent-worktrees runtime is loaded through its payload-owned "
            "command; no project registration is required for setup-foundation "
            "readiness."
        ),
    }


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0


def main() -> int:
    """Emit agent-worktrees installer readiness."""
    return emit(evaluate())
