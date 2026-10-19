"""Resolve an exact trusted execution venue before preparing its Host."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
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


def guard_cleanup_path(target: str, user: str, remote_env: str) -> str:
    """Validate the launch path and its actual directory before cleanup."""
    from . import ssh_transport
    from .launch_storage import directory_guard, launch_directory

    path = PurePosixPath(remote_env)
    canonical_ram = str(path.parent) == launch_directory(user)
    legacy_home = path.parts[-3:-1] == (".agent-containers", "launch")
    if (
        not path.is_absolute()
        or len(path.parts) < 4
        or ".." in path.parts
        or not (canonical_ram or legacy_home)
        or path.suffix != ".env"
        or not re.fullmatch(r"[0-9a-f]{32}", path.stem)
    ):
        raise RuntimeError(
            f"Refusing unsafe Session Host env cleanup path: {remote_env!r}"
        )
    ssh_transport._validate_target(target, user)
    # missing_ok=True: a missing RAM root (e.g. after a container restart
    # clears /dev/shm) must not block the idempotent cleanup contract that
    # still has to remove surviving /tmp wrappers and receipts;
    # directory_guard still refuses a symlinked ancestor either way.
    checked = ssh_transport._run([
        "docker", "exec", "-u", user, target,
        "sh", "-c", directory_guard(str(path.parent), require_ram=canonical_ram, missing_ok=True),
    ])
    if checked.returncode:
        raise RuntimeError("Refusing unsafe Session Host cleanup directory")
    return str(path)
