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


def require_internal_fleet(fleet: FleetConfig) -> None:
    fleet.validate_restricted()
    if fleet.lifecycle_owner == EXTERNAL_LIFECYCLE_OWNER:
        raise RuntimeError(EXTERNAL_REASON)


def lifecycle_blocker(
    config: ContainersConfig, name: str, labels: dict[str, str],
) -> str | None:
    owner = labels.get(LIFECYCLE_OWNER_LABEL, INTERNAL_LIFECYCLE_OWNER)
    if owner != INTERNAL_LIFECYCLE_OWNER:
        return EXTERNAL_REASON
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
    reason = lifecycle_blocker(config, name.removeprefix("/"), labels)
    if reason:
        raise RuntimeError(reason)
    return [*args[:-1], identity]


def validate_session_owner(fleet: FleetConfig, labels: dict[str, str]) -> None:
    fleet.validate_restricted()
    declared = labels.get(LIFECYCLE_OWNER_LABEL, INTERNAL_LIFECYCLE_OWNER)
    if declared != fleet.lifecycle_owner:
        raise RuntimeError("container lifecycle owner does not match its fleet")
