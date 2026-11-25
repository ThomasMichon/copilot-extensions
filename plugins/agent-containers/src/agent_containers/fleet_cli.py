"""``fleet`` subcommand: list fleet members, leases and sizing."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from .config import (
    RESTRICTED_PROFILE,
    SECURITY_PROFILE_LABEL,
    ContainersConfig,
)
from .lifecycle import DockerContainerInfo


def fleet_sizing(config: ContainersConfig, fleet_name: str | None) -> dict[str, int | None]:
    """The member's configured warm ``size`` and on-demand ``max_size`` bound."""
    fleet = config.fleets.get(fleet_name or "")
    return {
        "fleet_size": fleet.size if fleet else None,
        "fleet_max_size": fleet.max_size if fleet else None,
    }


def _print_sizing(config: ContainersConfig, containers: list[DockerContainerInfo]) -> None:
    counts = Counter(c.fleet for c in containers if c.fleet in config.fleets)
    for name in sorted(counts):
        fleet = config.fleets[name]
        bound = f", max_size {fleet.max_size}" if fleet.max_size is not None else ""
        print(f"Fleet {name}: {counts[name]} member(s), size {fleet.size}{bound}")


def cmd_fleet(args: argparse.Namespace, config: ContainersConfig) -> int:
    """List fleet members and leases (``fleet [--json]``)."""
    from . import picker, runtime_drivers
    from .lease import deploy_hold_status, get_lease
    from .lifecycle import inspect_container, list_containers, restricted_policy_errors
    from .rescue import latest_rescue_status
    containers = list_containers(config)
    if getattr(args, "json", False):
        # Bare JSON array, one object per container, so the picker's Containers
        # pivot (and any other machine-readable consumer) can render the fleet.
        out: list[dict[str, Any]] = []
        for c in containers:
            lease = get_lease(c.name)
            hold_status = deploy_hold_status(c.name)
            fleet = config.fleets.get(c.fleet or "")
            posture_errors = []
            actual_profile: str | None = c.security_profile
            actual_network = None
            inspected = None
            if (fleet and fleet.restricted) or actual_profile == RESTRICTED_PROFILE:
                try:
                    inspected = inspect_container(c.name)
                    actual_profile = (
                        (inspected.get("Config") or {}).get("Labels") or {}
                    ).get(SECURITY_PROFILE_LABEL)
                    actual_network = (inspected.get("HostConfig") or {}).get(
                        "NetworkMode"
                    )
                except RuntimeError as exc:
                    actual_profile = "unknown"
                    posture_errors = [str(exc)]
            forward, relay = (
                config.credentials_for(fleet) if fleet else (False, False)
            )
            workspace = (
                fleet.workspace_folder if fleet and fleet.workspace_folder else None
            ) or config.workspace_folder
            exec_user = (
                fleet.exec_user if fleet and fleet.exec_user else None
            ) or config.exec_user
            if fleet and fleet.restricted:
                try:
                    posture_errors.extend(restricted_policy_errors(
                        c,
                        fleet,
                        workspace_folder=workspace,
                        exec_user=exec_user,
                        inspected=inspected,
                    ))
                except RuntimeError as exc:
                    posture_errors.append(str(exc))
            elif actual_profile == RESTRICTED_PROFILE:
                posture_errors.append(
                    "restricted container has no matching fleet configuration"
                )
            if actual_profile == RESTRICTED_PROFILE:
                forward, relay = False, False
            runtime_identity = None
            if c.driver:
                try:
                    identity = runtime_drivers.runtime_identity(runtime_drivers.route_handle(c))
                    if identity.startswith("driver:"):
                        runtime_identity = identity
                except RuntimeError:
                    # Ambiguous routes must not supply a staging selector.
                    pass
            out.append({
                "name": c.name,
                "container_id": c.container_id,
                "image": c.image,
                "state": c.state,
                "status": c.status,
                "fleet": c.fleet,
                "fleet_source": config.fleet_source(c.fleet),
                "driver": c.driver,
                "runtime_identity": runtime_identity,
                "local_folder": c.local_folder,
                "lease": lease.effort if lease else None,
                "lifecycle_hold": hold_status,
                "security_profile": actual_profile,
                "configured_security_profile": (
                    fleet.security_profile if fleet else None
                ),
                "security_policy_current": not posture_errors,
                "security_policy_errors": posture_errors,
                "network": actual_network,
                "environment_names": sorted(fleet.environment) if fleet else [],
                "host_credentials": {
                    "github_token": forward,
                    "relay": relay,
                },
                "rescue": latest_rescue_status(c.name),
            })
            out[-1].update(picker.picker_fields(c.name, out[-1]["lease"]))
            out[-1].update(fleet_sizing(config, c.fleet))
        print(json.dumps(out, indent=2, default=str))
        return 0
    if not containers:
        print("No fleet containers found. Run `agent-containers up <fleet>`.")
        return 0
    print(f"{'CONTAINER':<28} {'STATE':<10} {'FLEET':<12} {'LEASE'}")
    for c in containers:
        lease = get_lease(c.name)
        holder = lease.effort if lease else "-"
        print(f"{c.name:<28} {c.state:<10} {(c.fleet or '-'):<12} {holder}")
    _print_sizing(config, containers)
    return 0
