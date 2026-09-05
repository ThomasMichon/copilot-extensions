#!/usr/bin/env python3
"""Drive the durable embedding engine through dispatch-selected authority only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from companion_context import installation_mode

_PROVIDER_ENVIRONMENT = {
    "AGENT_INDEX_EFFECTIVE_CONFIG",
    "AGENT_INDEX_MACHINE",
    "AGENT_INDEX_NO_SELFPROVISION",
    "AGENT_INDEX_REPO",
    "AGENT_INDEX_ENGINE_GENERATION",
    "AGENT_INDEX_ENGINE_HOST",
    "AGENT_INDEX_ENGINE_MANAGED_PYTHON",
    "AGENT_INDEX_ENGINE_PORT",
}
_SESSION_CONTEXT_ENVIRONMENT = {
    "CLAUDE_PLUGIN_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT",
    "COPILOT_PLUGIN_ROOT",
    "PLUGIN_ROOT",
}


def _runtime_gate(action: str) -> list[str]:
    value = os.environ.get("AGENT_INDEX_ENGINE_MANAGED_PYTHON", "")
    python = Path(value)
    if not value or not python.is_absolute() or not python.is_file():
        raise RuntimeError("agent-index engine requires a dispatch-selected host interpreter")
    mapping = {
        "start": "__managed-engine-start",
        "health": "__managed-engine-health",
    }
    if action not in mapping:
        raise RuntimeError(f"unknown engine companion action: {action}")
    return [
        str(python),
        "-I",
        "-B",
        "-X",
        "utf8",
        "-m",
        "agent_index",
        mapping[action],
    ]


def _runtime_environment() -> dict[str, str]:
    approved = {
        key: value for key in _PROVIDER_ENVIRONMENT if (value := os.environ.get(key)) is not None
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("AGENT_INDEX_", "PIP_", "UV_", "PYTHON"))
        and key.upper() not in _SESSION_CONTEXT_ENVIRONMENT
    }
    environment.update(approved)
    environment["AGENT_INDEX_NO_SELFPROVISION"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run(action: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _runtime_gate(action),
        env=_runtime_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=(action == "health"),
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _companion_mode_supported() -> bool:
    mode = installation_mode()
    if mode.get("schema_version") != 1 or mode.get("supported") is not True:
        print(
            "agent-index engine companion lifecycle is unavailable for installation "
            f"mode {mode.get('mode', 'unknown')}",
            file=sys.stderr,
        )
        return False
    return True


def _start() -> int:
    _runtime_gate("start")
    if not _companion_mode_supported():
        return 1
    return _run("start").returncode


def _health() -> int:
    if not _companion_mode_supported():
        return 1
    result = _run("health")
    if result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    if result.stdout:
        print(result.stdout.strip())
    return result.returncode


def _mode() -> int:
    print(json.dumps(installation_mode(), separators=(",", ":"), sort_keys=True))
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"start", "health", "mode"}:
        print("usage: companion-engine.py {start|health|mode}", file=sys.stderr)
        return 2
    action = sys.argv[1]
    try:
        if action == "start":
            return _start()
        if action == "health":
            return _health()
        return _mode()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"agent-index engine companion {action} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
