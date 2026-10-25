"""Fixed local consumer for an authenticated Fleet SSH directory update."""

from __future__ import annotations

from pathlib import Path

from . import gateway_authorization, gateway_provider


def validate_consumer_state(
    state_dir: Path, *, config_d: Path, ssh_config: Path, authorization_dir: Path | None,
) -> None:
    """Keep a consumer's private state outside every directory owned by these writers."""
    from . import gateway_profile, ssh_profile

    state = state_dir.resolve()
    destinations = {
        config_d, ssh_config,
        config_d.parent / gateway_profile._SOURCE_DIR,
        config_d.parent / ssh_profile._LOCK_DIR_NAME,
        ssh_config.parent / ssh_profile._LOCK_DIR_NAME,
    }
    if authorization_dir is not None:
        destinations.add(authorization_dir)
    for destination in destinations:
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
