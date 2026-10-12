"""Installer/readiness contract adapter for agent-dispatch."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from .client import DispatchError

MODULE_ID = "agent-dispatch/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(probe: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
    """Map the existing coordinator health probe without starting a service."""
    try:
        health = probe()
    except (DispatchError, httpx.HTTPError, OSError, ValueError) as exc:
        return _result(
            "failed",
            "The configured coordinator is unavailable: "
            f"{exc}. Re-run the agent-dispatch installer; if service.env changed, "
            "restart the coordinator service before retrying.",
        )
    status = health.get("status")
    if status != "ok":
        return _result(
            "failed",
            "The configured coordinator reported "
            f"status {status!r}. Wait for graceful cutover to finish or restart "
            "the coordinator service.",
        )
    version = health.get("version")
    suffix = f" (version {version})" if version else ""
    return _result(
        "ready",
        "The configured coordinator health endpoint is ready"
        f"{suffix}; installer-driven updates reload the service in place.",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
