"""Installer/readiness contract adapter for agent-mcp."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .config import ConfigError, normalize_bridge_name
from .configuration import load_readiness_config

MODULE_ID = "agent-mcp/runtime"


def configured_candidates() -> list[tuple[str, Path]]:
    from . import config
    from .provider_selection import provider_filter

    candidates = []
    if config.BRIDGES_DIR.is_dir():
        candidates.extend(
            (normalize_bridge_name(path.name), path)
            for path in sorted(config.BRIDGES_DIR.iterdir())
            if path.suffix.lower() in {".yaml", ".yml", ".json"}
        )
    candidates.extend(config.discover_plugin_bridge_candidates(include=provider_filter()))
    return candidates


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def configured_readiness() -> dict[str, Any]:
    try:
        return evaluate(configured_candidates())
    except ConfigError as error:
        return _result("failed", str(error))


def evaluate(
    candidates: Sequence[tuple[str, Path]],
    *,
    loader: Callable[[str], object] = load_readiness_config,
) -> dict[str, Any]:
    """Validate native bridges without requiring a repository or starting an upstream."""
    unique_candidates = tuple(
        dict.fromkeys(
            (normalize_bridge_name(name), path.resolve())
            for name, path in candidates
        )
    )
    if not unique_candidates:
        return _result(
            "configuration-empty",
            "The agent-mcp runtime is healthy, but no MCP bridge is configured. "
            "Add a bridge configuration only when one is needed.",
        )
    paths_by_name: dict[str, list[Path]] = defaultdict(list)
    for name, path in unique_candidates:
        paths_by_name[name].append(path)
    collisions = {
        name: paths
        for name, paths in paths_by_name.items()
        if len(set(paths)) > 1
    }
    if collisions:
        detail = "; ".join(
            f"{name}: {', '.join(str(path) for path in paths)}"
            for name, paths in sorted(collisions.items())
        )
        return _result(
            "failed",
            "MCP bridge names are ambiguous: "
            f"{detail}. Rename one candidate before validating bridge content.",
        )

    failures: list[str] = []
    for _name, path in unique_candidates:
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
        f"The runtime loaded and {len(unique_candidates)} MCP bridge configuration(s) "
        "validated without starting an upstream server.",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
