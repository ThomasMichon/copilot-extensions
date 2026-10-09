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


def resolve_context(name: str, config: ContainersConfig) -> TrustedContext:
    from .lifecycle import get_container, inspect_container

    info = get_container(config, name)
    if info is None:
        raise RuntimeError(f"Container '{name}' is not a discovered fleet member")
    if info.state != "running":
        raise RuntimeError(f"Container '{name}' is not running (state={info.state!r})")
    fleet = config.fleets.get(info.fleet or "")
    if fleet is None:
        raise RuntimeError(f"Container '{name}' has no matching fleet configuration")
    inspection = inspect_container(name)
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
