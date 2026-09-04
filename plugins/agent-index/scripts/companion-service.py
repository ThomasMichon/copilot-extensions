#!/usr/bin/env python3
"""Drive an already-installed agent-index runtime without provisioning it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_PROVIDER_ENVIRONMENT = {
    "AGENT_INDEX_EFFECTIVE_CONFIG",
    "AGENT_INDEX_MACHINE",
    "AGENT_INDEX_NO_SELFPROVISION",
    "AGENT_INDEX_REPO",
}
_SESSION_CONTEXT_ENVIRONMENT = {
    "CLAUDE_PLUGIN_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT",
    "COPILOT_PLUGIN_ROOT",
    "PLUGIN_ROOT",
}


def _runtime_gate(action: str) -> list[str]:
    scripts = Path(__file__).resolve().parent
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise RuntimeError("PowerShell is unavailable")
        return [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "runtime-gate.ps1"),
            action,
        ]
    shell = shutil.which("bash")
    if shell is None:
        raise RuntimeError("bash is unavailable")
    return [shell, str(scripts / "runtime-gate.sh"), action]


def _runtime_environment() -> dict[str, str]:
    approved = {
        key: value for key in _PROVIDER_ENVIRONMENT if (value := os.environ.get(key)) is not None
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("AGENT_INDEX_") and key not in _SESSION_CONTEXT_ENVIRONMENT
    }
    environment.update(approved)
    environment["AGENT_INDEX_NO_SELFPROVISION"] = "1"
    return environment


def _run(action: str, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _runtime_gate(action),
        env=_runtime_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _health() -> int:
    result = _run("status", capture=True)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return result.returncode
    try:
        status = json.loads(result.stdout)
        running = status["running"]
        if not isinstance(running, bool):
            raise ValueError("running is not boolean")
    except (KeyError, TypeError, ValueError) as exc:
        print(f"agent-index status is malformed: {exc}", file=sys.stderr)
        return 1
    detail = (
        "agent-index service is reachable"
        if running
        else f"agent-index service state is {status.get('state', 'stopped')}"
    )
    print(
        json.dumps(
            {"schema_version": 1, "healthy": running, "detail": detail},
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _companion_mode_supported() -> bool:
    readiness = _run("__dispatch-companion-mode", capture=True)
    if readiness.returncode != 0:
        if readiness.stderr:
            print(readiness.stderr.strip(), file=sys.stderr)
        return False
    try:
        mode = json.loads(readiness.stdout)
    except (TypeError, ValueError) as exc:
        print(f"agent-index companion mode is malformed: {exc}", file=sys.stderr)
        return False
    if mode.get("schema_version") != 1 or mode.get("supported") is not True:
        print(
            "agent-index companion lifecycle is unavailable for installation "
            f"mode {mode.get('mode', 'unknown')}",
            file=sys.stderr,
        )
        return False
    return True


def _mode() -> int:
    result = _run("__dispatch-companion-mode", capture=True)
    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr:
        print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def _start() -> int:
    if not _companion_mode_supported():
        return 1

    stopped = _run("stop", capture=True)
    if stopped.returncode != 0:
        if stopped.stderr:
            print(stopped.stderr.strip(), file=sys.stderr)
        return stopped.returncode
    try:
        result = json.loads(stopped.stdout)
    except (TypeError, ValueError) as exc:
        print(f"agent-index stop result is malformed: {exc}", file=sys.stderr)
        return 1
    if result.get("stopped") is not True and result.get("reason") != "not-running":
        print(
            "agent-index refused companion takeover: "
            f"{result.get('reason', 'unknown stop result')}",
            file=sys.stderr,
        )
        return 1
    return _run("start").returncode


def _stop() -> int:
    if not _companion_mode_supported():
        return 1
    return _run("stop").returncode


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {
        "start",
        "stop",
        "health",
        "mode",
    }:
        print("usage: companion-service.py {start|stop|health|mode}", file=sys.stderr)
        return 2
    action = sys.argv[1]
    if action == "health":
        return _health()
    if action == "mode":
        return _mode()
    if action == "start":
        try:
            return _start()
        except (OSError, RuntimeError) as exc:
            print(f"agent-index companion start failed: {exc}", file=sys.stderr)
            return 1
    try:
        return _stop()
    except (OSError, RuntimeError) as exc:
        print(f"agent-index companion {action} failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
