"""Installer/readiness contract adapter for agent-mcp."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .config import ConfigError, load_config

MODULE_ID = "agent-mcp/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(
    paths: Sequence[Path],
    *,
    loader: Callable[[str], object] = load_config,
) -> dict[str, Any]:
    """Validate configured bridges without starting an upstream process."""
    unique_paths = tuple(dict.fromkeys(path.resolve() for path in paths))
    if not unique_paths:
        return _result(
            "configuration-empty",
            "The agent-mcp runtime is healthy, but no MCP bridge is configured. "
            "Add a bridge configuration only when one is needed.",
        )
    failures: list[str] = []
    for path in unique_paths:
        try:
            loader(str(path))
        except (ConfigError, OSError, ValueError) as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        return _result(
            "failed",
            "MCP bridge configuration is invalid: "
            + "; ".join(failures)
            + ". Run `agent-mcp validate <path>` for each failing bridge.",
        )
    return _result(
        "ready",
        f"The runtime loaded and {len(unique_paths)} MCP bridge configuration(s) "
        "validated without starting an upstream server.",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
