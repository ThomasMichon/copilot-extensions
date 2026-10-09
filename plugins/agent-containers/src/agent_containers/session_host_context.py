"""Resolve an exact trusted execution venue before preparing its Host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from .config import SECURITY_PROFILE_LABEL, TRUSTED_PROFILE, ContainersConfig, FleetConfig


@dataclass
class TrustedContext:
    config: ContainersConfig
    fleet: FleetConfig
    user: str
    workspace: str
    instance_id: str

    def __iter__(self) -> Iterator[Any]:
        return iter((self.config, self.fleet, self.user, self.workspace))


def resolve_context(
    name: str, config: ContainersConfig, expected_instance: str | None = None,
) -> TrustedContext:
    from .lifecycle import get_container, inspect_container

    info = get_container(config, name)
    if info is None:
        raise RuntimeError(f"Container '{name}' is not a discovered fleet member")
    if info.state != "running":
        raise RuntimeError(f"Container '{name}' is not running (state={info.state!r})")
    if expected_instance and info.container_id != expected_instance:
        raise RuntimeError("selected container instance changed before preparation")
    fleet = config.fleets.get(info.fleet or "")
    if fleet is None:
        raise RuntimeError(f"Container '{name}' has no matching fleet configuration")
    inspection = inspect_container(expected_instance or name)
    if expected_instance and inspection.get("Id") != expected_instance:
        raise RuntimeError("selected container instance could not be confirmed")
    actual_profile = (
        ((inspection.get("Config") or {}).get("Labels") or {})
        .get(SECURITY_PROFILE_LABEL)
    )
    if fleet.security_profile != TRUSTED_PROFILE or actual_profile != TRUSTED_PROFILE:
        raise RuntimeError(
            f"Container '{name}' is not exact trusted/trusted posture "
            f"(configured={fleet.security_profile!r}, live={actual_profile!r}); "
            "Session Host projection is trusted-fleet only"
        )
    return TrustedContext(
        config, fleet, fleet.exec_user or config.exec_user,
        fleet.workspace_folder or config.workspace_folder,
        str(inspection.get("Id") or ""),
    )


def cleanup_user(instance_id: str, bound_user: str | None) -> str:
    from .lifecycle import inspect_container

    if not bound_user:
        raise RuntimeError("immutable cleanup requires its bound execution user")
    inspection = inspect_container(instance_id)
    profile = ((inspection.get("Config") or {}).get("Labels") or {}).get(SECURITY_PROFILE_LABEL)
    if inspection.get("Id") != instance_id or profile != TRUSTED_PROFILE:
        raise RuntimeError("immutable cleanup target is not the prepared trusted instance")
    return bound_user
