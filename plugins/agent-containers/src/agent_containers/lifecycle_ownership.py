"""Keep external managers as the sole container lifecycle writer."""

from __future__ import annotations

import re

from .config import (
    EXTERNAL_LIFECYCLE_OWNER,
    FLEET_LABEL,
    INTERNAL_LIFECYCLE_OWNER,
    LIFECYCLE_OWNER_LABEL,
    ContainersConfig,
    FleetConfig,
)

EXTERNAL_REASON = "external lifecycle owner; use the selected deployment manager"
DRIVER_REASON = "member of a runtime-driver fleet; only its driver may manage it"


def require_internal_fleet(fleet: FleetConfig) -> None:
    fleet.validate_restricted()
    if fleet.lifecycle_owner == EXTERNAL_LIFECYCLE_OWNER:
        raise RuntimeError(EXTERNAL_REASON)


def _exact_driver_route(name: str, instance_id: str | None) -> bool:
    """True only when the driver reported exactly this name AND incarnation."""
    from .runtime_drivers import owner_of

    if not instance_id:
        return False
    try:
        route = owner_of(f"{name}.{instance_id}")
    except RuntimeError:  # ambiguous handle
        return False
    return route is not None and route.name == name and route.instance_id == instance_id


def _name_held_by_driver(name: str) -> bool:
    """True when a driver member (or several runtimes) answers to ``name``."""
    from .runtime_drivers import owner_of

    try:
        return owner_of(name) is not None
    except RuntimeError:  # ambiguous: at least one runtime driver claims it
        return True


def lifecycle_blocker(
    config: ContainersConfig, name: str, labels: dict[str, str],
    instance_id: str | None = None,
) -> str | None:
    """Why agent-containers must not act on this container, or ``None``.

    ``instance_id`` is the exact incarnation about to be acted on. A
    container that claims a driver fleet (by label or deterministic name), or
    whose name a driver member holds, is admitted only when its driver
    reported exactly that name and incarnation. A same-name driver member is
    never sufficient: the Docker fallback is refused for a different
    incarnation, so a name collision is never Docker-managed as if it were
    driver-owned.
    """
    owner = labels.get(LIFECYCLE_OWNER_LABEL, INTERNAL_LIFECYCLE_OWNER)
    if owner != INTERNAL_LIFECYCLE_OWNER:
        return EXTERNAL_REASON
    claims_driver_fleet = any(
        getattr(fleet, "driver", None) and (
            labels.get(FLEET_LABEL) == key
            or (not labels.get(FLEET_LABEL) and name.startswith(fleet.prefix(key) + "-"))
        )
        for key, fleet in config.fleets.items()
    )
    if (claims_driver_fleet or _name_held_by_driver(name)) and not _exact_driver_route(
        name, instance_id,
    ):
        return DRIVER_REASON
    selected = config.fleets.get(labels.get(FLEET_LABEL, ""))
    if selected and selected.lifecycle_owner == EXTERNAL_LIFECYCLE_OWNER:
        return EXTERNAL_REASON
    for key, fleet in config.fleets.items():
        if (fleet.lifecycle_owner == EXTERNAL_LIFECYCLE_OWNER
                and name.startswith(fleet.prefix(key) + "-")
                and (not labels.get(FLEET_LABEL)
                     or name[len(fleet.prefix(key)) + 1:].isdigit())):
            return EXTERNAL_REASON
    return None


def pin_lifecycle_command(args: list[str], config: ContainersConfig, inspect) -> list[str]:
    if (len(args) < 2 or args[0] not in {"start", "stop", "unpause", "rm"}
            or (args[1:-1] not in ([], ["-f"]) if args[0] == "rm" else len(args) != 2)
            or args[-1].startswith("-")):
        raise RuntimeError("unsupported container lifecycle command")
    target = args[-1]
    record = inspect(target)
    identity = record.get("Id")
    name = record.get("Name")
    labels = (record.get("Config") or {}).get("Labels") or {}
    if (not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity)
            or not isinstance(name, str) or not name.startswith("/")
            or not isinstance(labels, dict)):
        raise RuntimeError("container lifecycle identity could not be verified")
    reason = lifecycle_blocker(config, name.removeprefix("/"), labels, identity)
    if reason:
        raise RuntimeError(reason)
    return [*args[:-1], identity]


def validate_session_owner(fleet: FleetConfig, labels: dict[str, str]) -> None:
    fleet.validate_restricted()
    declared = labels.get(LIFECYCLE_OWNER_LABEL, INTERNAL_LIFECYCLE_OWNER)
    if declared != fleet.lifecycle_owner:
        raise RuntimeError("container lifecycle owner does not match its fleet")
