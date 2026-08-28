"""Installer/readiness contract adapter for agent-bridge."""

from __future__ import annotations

import json
import sys
from typing import Any

MODULE_ID = "agent-bridge/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(healthy: bool) -> dict[str, Any]:
    """Map the existing service health result without starting the daemon."""
    if not healthy:
        return _result(
            "failed",
            "The agent-bridge runtime service is unavailable or unhealthy. "
            "Re-run the installer update; it performs the required service "
            "cutover, so no separate shell, session, or machine restart is needed.",
        )
    return _result(
        "ready",
        "The agent-bridge service health endpoint is ready. Installer updates "
        "perform their own service cutover, so no separate restart is required.",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
