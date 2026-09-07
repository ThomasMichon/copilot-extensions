#!/usr/bin/env python3
"""Resolve dispatch-supervised dtssh host launcher activation."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

_CONFIG_VERSION = 1


def _request() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuntimeError("unsupported companion request")
    machine = value.get("machine")
    if not isinstance(machine, str) or not machine.strip():
        raise RuntimeError("companion request is incomplete")
    return value


def _default_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return (
        Path(local_app_data).expanduser().resolve()
        / "agent-ssh-dtssh"
        / "dispatch-companion.json"
    )


def _validate_config(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"dtssh companion config is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("dtssh companion config must be a JSON object")
    if payload.get("schema_version") != _CONFIG_VERSION:
        raise RuntimeError(
            f"dtssh companion config needs schema_version {_CONFIG_VERSION}"
        )
    alias = payload.get("alias")
    port = payload.get("port")
    if not isinstance(alias, str) or not alias.strip():
        raise RuntimeError("dtssh companion config needs a non-empty alias")
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
        raise RuntimeError("dtssh companion config needs a positive port")
    for key in ("tunnel", "user", "host_key_backup_root"):
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise RuntimeError(f"dtssh companion config field {key!r} must be a string")


def _active_environment(_request: dict[str, Any]) -> dict[str, str] | None:
    if platform.system() != "Windows":
        return None
    if shutil.which("pwsh") is None:
        raise RuntimeError("pwsh is required for the dtssh companion")
    config = _default_config_path()
    if not config.is_file():
        return None
    _validate_config(config)
    install_root = config.parent
    for name in ("install-host.ps1", "dtssh-host-launcher.ps1"):
        candidate = install_root / name
        if not candidate.is_file():
            raise RuntimeError(f"dtssh companion install root is incomplete: {candidate}")
    return {"AGENT_SSH_DTSSH_COMPANION_CONFIG": str(config)}


def main() -> int:
    try:
        environment = _active_environment(_request())
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"agent-ssh dtssh companion activation is indeterminate: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "schema_version": 1,
                "active": environment is not None,
                **({"environment": environment} if environment is not None else {}),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
