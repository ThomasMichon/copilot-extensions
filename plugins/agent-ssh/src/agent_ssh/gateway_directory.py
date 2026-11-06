"""Fixed local consumer for an authenticated Fleet SSH directory update."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from . import gateway_authorization, gateway_provider


def _writer_directories(config_d: Path, ssh_config: Path, authorization_dir: Path | None) -> dict[str, Path]:
    from . import gateway_profile, ssh_profile

    directories = {
        "fragments": config_d,
        "sources": config_d.parent / gateway_profile._SOURCE_DIR,
        "fragment-locks": config_d.parent / ssh_profile._LOCK_DIR_NAME,
        "root-locks": ssh_config.parent / ssh_profile._LOCK_DIR_NAME,
    }
    if authorization_dir is not None:
        directories["authorization"] = authorization_dir
    return directories


def validate_writer_destinations(
    *, config_d: Path, ssh_config: Path, authorization_dir: Path | None,
    authorization_target: dict, authorization_controller: str,
    gateway: str, identity_file: Path,
) -> None:
    """Validate existing direct and derived writer kinds without creating anything."""
    from . import gateway_profile, ssh_profile
    from .file_lock import validate_lock_file

    gateway_profile._alias(gateway)
    gateway_profile._path(str(identity_file))
    ssh_profile._include_line(config_d)
    directories = _writer_directories(config_d, ssh_config, authorization_dir)
    gateway_profile._quote(directories["sources"])
    scopes = [(name, path.resolve()) for name, path in directories.items()]
    for index, (name, path) in enumerate(scopes):
        for other_name, other in scopes[index + 1:]:
            if name.endswith("-locks") and other_name.endswith("-locks") and path == other:
                continue
            if path.is_relative_to(other) or other.is_relative_to(path):
                raise ValueError("SSH writer directory scopes must be separate")
        selected = ssh_config.resolve()
        if selected.is_relative_to(path) or path.is_relative_to(selected):
            raise ValueError("SSH configuration must be separate from writer directories")
    for path in (*directories.values(), ssh_config):
        for parent in path.parents:
            if os.path.lexists(parent):
                ssh_profile._regular_config_directory(parent)
    for path in directories.values():
        if os.path.lexists(path):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("SSH writer directory must be an ordinary directory")
    if os.path.lexists(ssh_config):
        info = ssh_config.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("SSH writer configuration must be an ordinary file")
        ssh_profile._root_config_target(ssh_config).read_text(encoding="utf-8")
    for path in (
        directories["sources"] / "gateway-provider.lock",
        directories["sources"] / "gateway.lock",
        directories["fragment-locks"] / (ssh_profile.fragment_name("gateway") + ".lock"),
        directories["root-locks"] / "root-config.lock",
    ):
        validate_lock_file(path)
    gateway_provider._directory_state(authorization_target, authorization_controller, directories["sources"])
    if authorization_dir is not None:
        parent = authorization_dir.parent.resolve()
        if not parent.is_dir() and not any(
            planned.resolve().is_relative_to(parent) for planned in (config_d, ssh_config.parent)
        ):
            raise ValueError("Authorization parent must exist or be created by the profile writer")
        gateway_authorization.validate_state(
            authorization_dir, target=authorization_target, controller=authorization_controller,
        )


def validate_consumer_state(
    state_dir: Path, *, config_d: Path, ssh_config: Path, authorization_dir: Path | None,
) -> None:
    """Keep a consumer's private state outside every directory owned by these writers."""
    state = state_dir.resolve()
    for destination in set(_writer_directories(config_d, ssh_config, authorization_dir).values()) | {ssh_config}:
        selected = destination.resolve()
        if selected.is_relative_to(state) or state.is_relative_to(selected):
            raise ValueError("SSH consumer requires a dedicated state directory outside local writer scopes")


def apply_update(
    update, *, policy_path: Path, machine: str, environment: str,
    gateway: str, controller: str, identity_file: Path, public_key_file: Path,
    authorization_dir: Path | None, config_d: Path | None = None,
    ssh_config: Path | None = None,
) -> dict:
    """Rebuild through the existing profile and target-local authorization owners.

    The machine's setup/lifetime owner explicitly selects this local write scope.
    A remote service cannot widen local policy or choose paths/commands. Neither
    this adapter nor directory notification starts an sshd or restarts a service.
    """
    from fleet_manager.gateway_ssh_provider import (
        project_gateway_authorization, project_gateway_profiles,
    )
    from fleet_manager.ssh_directory import DirectoryUpdate

    if not isinstance(update, DirectoryUpdate):
        raise ValueError("An authenticated Fleet directory update is required")
    policy_type, _, decode = gateway_provider._sdk()
    local = policy_type.from_dict(decode(gateway_provider._read(policy_path)))
    selected = update.to_dict()
    remote = policy_type.from_dict(selected["policy"])
    directory = selected["directory"]
    if local.policy_digest != remote.policy_digest or directory["controller"] != controller:
        raise PermissionError("SSH directory differs from the selected local policy/controller")
    with public_key_file.open("rb") as stream:
        public = stream.read(257)
    if len(public) > 256:
        raise ValueError("Gateway local public-key file exceeds its bound")
    public = public.decode("ascii").strip()
    profiles = project_gateway_profiles(
        local, directory, machine=machine, environment=environment,
        gateway=gateway, identity_file=str(identity_file), local_public_key=public,
    )
    authorization = project_gateway_authorization(
        local, directory, machine=machine, environment=environment, local_public_key=public,
    )
    decision = local.resolve(machine, environment).to_dict()
    if decision["effective_inbound"] and authorization_dir is None:
        raise ValueError("Inbound SSH requires its explicitly selected authorization owner")
    fragment = gateway_provider.apply_projection(profiles, config_d=config_d, ssh_config=ssh_config)
    authorized, changed = (None, False)
    if authorization_dir is not None:
        authorized, changed = gateway_authorization.apply_projection(authorization, authorization_dir)
    return {
        "directory_revision": directory["revision"], "fragment": str(fragment),
        "authorization_file": str(authorized) if authorized else None,
        "authorization_changed": changed,
    }
