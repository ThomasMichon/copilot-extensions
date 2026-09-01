"""Installer/readiness contract adapter for agent-containers."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable, Sequence
from typing import Any

from .config import ContainersConfig
from .lifecycle import DockerContainerInfo

MODULE_ID = "agent-containers/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def inspect_toolchain(
    config: ContainersConfig,
    *,
    command_finder: Callable[[str], str | None] = shutil.which,
) -> tuple[str, ...]:
    """Validate only tools required by the configured fleet backends."""
    failures: list[str] = []
    if config.fleets and command_finder("docker") is None:
        failures.append("docker CLI is not installed")
    if any(fleet.devcontainer_path for fleet in config.fleets.values()):
        if command_finder("devcontainer") is None:
            failures.append(
                "devcontainer CLI is required by a configured devcontainer fleet"
            )
    if any(not fleet.restricted for fleet in config.fleets.values()):
        if command_finder("ssh") is None:
            failures.append("OpenSSH is required by a configured trusted fleet")
    for name, fleet in sorted(config.fleets.items()):
        if not fleet.devcontainer_path and not fleet.image:
            failures.append(
                f"fleet {name!r} needs either devcontainer_path or image"
            )
    return tuple(failures)


def evaluate(
    config: ContainersConfig | None,
    containers: Sequence[DockerContainerInfo],
    failures: Sequence[str],
) -> dict[str, Any]:
    """Map validated config and the existing Docker inventory without mutation."""
    if failures or config is None:
        detail = "; ".join(failures) or "configuration could not be loaded"
        return _result(
            "failed",
            "The agent-containers runtime, Docker service, configuration, or "
            f"required toolchain is unavailable: {detail}. Run "
            "`agent-containers fleet --json` for the owning runtime diagnostics.",
        )
    if not config.fleets and not containers:
        return _result(
            "configuration-empty",
            "The runtime is healthy and no fleet is configured. Docker is not "
            "required until a fleet is configured; readiness did not create a "
            "container or pull an image.",
        )
    if not containers:
        return _result(
            "configuration-empty",
            f"{len(config.fleets)} fleet(s) are configured and valid, but none is "
            "provisioned. Run `agent-containers up <fleet>` explicitly when needed.",
        )
    return _result(
        "ready",
        f"The runtime and Docker service are healthy with {len(config.fleets)} "
        f"configured fleet(s) and {len(containers)} discovered fleet container(s).",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
