"""Opted-in maintenance through the selected existing protected-cache service."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from .command_transport import CommandRoute

CONTRACT = "agent-vault.cache-maintenance"
SUPPORTED_VERSION = 1


def _failure(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def _supported(response: object, operation: str) -> bool:
    if not isinstance(response, dict) or response.get("ok") is not True:
        return False
    metadata = response.get("cache_maintenance")
    return bool(
        response.get("accepting_work") is True
        and isinstance(metadata, dict)
        and metadata.get("contract") == CONTRACT
        and type(metadata.get("version")) is int and metadata["version"] == SUPPORTED_VERSION
        and metadata.get("owner") == "selected-service"
        and metadata.get("returns_credentials") is False
        and isinstance(metadata.get("operations"), list)
        and operation in metadata["operations"]
    )


def _result(response: object) -> bool:
    return bool(
        isinstance(response, dict) and response.get("ok") is True
        and response.get("contract") == CONTRACT
        and type(response.get("version")) is int and response["version"] == SUPPORTED_VERSION
        and response.get("owner") == "selected-service"
        and type(response.get("present")) is bool
    )


def command(args: Any, entries: list[tuple[str, str]], operation: str) -> int:
    from . import cli, config

    if operation == "populate" and (
        getattr(args, "prompt", False) or getattr(args, "verify", False)
    ):
        return _failure(
            "selected-service maintenance does not unlock or check backend existence; "
            "unlock separately, or use cache-verify to inspect protected records",
        )
    request_operation = "populate" if operation == "populate" else "verify"
    try:
        context = config.resolve_context()
        route = CommandRoute()
        provider = cli.send_command(
            {"action": "ping"}, timeout=5.0, strict_endpoint=True, require_endpoint=True,
            _context=context, _route=route,
        )
        if not _supported(provider, request_operation):
            return _failure(
                "selected running service does not advertise the required cache-maintenance contract",
            )
        if route.sender is None:
            return _failure("selected service qualification did not retain a transport")
        present = 0
        missing: list[str] = []
        for entry, field in entries:
            response = cli.send_command({
                "action": "cache-maintenance", "operation": request_operation,
                "entry": entry, "field": field,
            }, timeout=15.0, strict_endpoint=True, require_endpoint=True, _context=context, _route=route)
            if not _result(response):
                if isinstance(response, dict) and response.get("needs_unlock") is True:
                    message = "selected service requires an existing operator unlock"
                else:
                    message = "selected service cache maintenance was not confirmed"
                return _failure(f"{message}; verified {present}/{len(entries)} protected records")
            assert response is not None
            if response["present"]:
                present += 1
            elif operation == "populate":
                return _failure("selected service did not verify its protected cache write")
            else:
                missing.append(f"{entry} [{field}]")
    except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError):
        return _failure("selected service is unavailable; no local cache fallback was attempted")

    if operation == "status":
        payload = {
            "owner": "selected-service", "configured_target_count": len(entries),
            "present": present, "missing": missing,
            "state": "inspected" if entries else "no-targets",
        }
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"owner: selected-service\nconfigured targets: {len(entries)}")
            print(f"protected present: {present}/{len(entries)}")
        return 0
    if operation == "verify":
        if getattr(args, "json", False):
            print(json.dumps({"present": present, "total": len(entries), "missing": missing}, indent=2))
        else:
            print(f"Cached: {present}/{len(entries)}")
            for entry in missing:
                print(f"  missing: {entry}", file=sys.stderr)
        return 2 if missing else 0
    print(f"Cached {present} credential(s), 0 failed")
    return 0
