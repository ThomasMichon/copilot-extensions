"""Installer/readiness contract adapter for agent-vault."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from .config import ResolvedVault

MODULE_ID = "agent-vault/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(
    context: ResolvedVault | None,
    service: Mapping[str, Any] | None,
    config_errors: Sequence[str] = (),
    service_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """Map strict config and the existing ping response without unlocking."""
    if config_errors or context is None:
        detail = "; ".join(config_errors) or "configuration could not be resolved"
        return _result(
            "failed",
            "The agent-vault configuration is invalid: "
            f"{detail}. Fix the owning configuration before starting or unlocking.",
        )
    if service_errors:
        return _result(
            "failed",
            "The agent-vault service probe failed: "
            + "; ".join(service_errors)
            + ". Fix the endpoint or runtime configuration; readiness did not "
            "start or restart the service.",
        )
    if not service or service.get("ok") is not True:
        return _result(
            "failed",
            "The agent-vault service is unavailable or unhealthy. Re-run the "
            "installer update; readiness did not start or restart the service.",
        )
    backend = service.get("cli")
    if backend == "not_found":
        return _result(
            "failed",
            "The agent-vault service is healthy, but keepassxc-cli is unavailable. "
            "Install the required KeePassXC command-line tool.",
        )
    if backend not in {"locked", "unlocked"}:
        return _result(
            "failed",
            f"The agent-vault service reported invalid backend state {backend!r}.",
        )
    if not context.kpdb:
        return _result(
            "configuration-empty",
            "The runtime and service are healthy, but no KeePass database is "
            "configured. Readiness did not create or unlock vault state.",
        )
    lock_detail = (
        "currently locked; unlock it explicitly when credential access is needed"
        if backend == "locked"
        else "currently unlocked"
    )
    return _result(
        "ready",
        "The configured agent-vault runtime and service are healthy and "
        f"{lock_detail}. Installer updates complete their own service cutover.",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
