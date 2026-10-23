"""Fleet directional-policy/public-directory adapter for the existing SSH emitter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from dropin_registry import atomic_write_text

from . import gateway_profile, ssh_profile
from .file_lock import exclusive_file_lock


def _sdk():
    try:
        from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy
        from fleet_manager.gateway_ssh_provider import project_gateway_profiles
        from fleet_manager.planning import decode
    except ImportError as exc:
        raise RuntimeError("Gateway provider setup requires a current agent-ssh[fleet] installation") from exc
    return GatewaySSHPolicy, project_gateway_profiles, decode


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        value = stream.read(gateway_profile.MAX_INPUT_BYTES + 1)
    if len(value) > gateway_profile.MAX_INPUT_BYTES:
        raise ValueError("Gateway provider source exceeds 1 MiB")
    return value


def projection(
    policy_path: Path, directory_path: Path, *, machine: str, environment: str,
    gateway: str, identity_file: Path, public_key_file: Path,
) -> dict:
    policy_type, compile_profiles, decode = _sdk()
    policy = policy_type.from_dict(decode(_read(policy_path)))
    directory = decode(_read(directory_path))
    with public_key_file.expanduser().open("rb") as stream:
        public = stream.read(257)
    if len(public) > 256:
        raise ValueError("Gateway local public-key file exceeds its bound")
    value = compile_profiles(
        policy, directory, machine=machine, environment=environment, gateway=gateway,
        identity_file=str(identity_file.expanduser()), local_public_key=public.decode("ascii").strip(),
    )
    value["config"] = gateway_profile.validate_config(value["config"])
    return value


def _directory_floor(value: dict, sources: Path) -> tuple[Path, str]:
    scope = json.dumps({
        "target": value["target"], "controller": value["controller"],
    }, sort_keys=True).encode("utf-8")
    floor = sources / ("gateway-provider-" + hashlib.sha256(scope).hexdigest() + ".json")
    accepted = {
        "version": 1, "target": value["target"], "controller": value["controller"],
        "directory_revision": value["directory_revision"],
        "directory_digest": value["directory_digest"],
    }
    if floor.exists() or floor.is_symlink():
        prior = json.loads(gateway_profile._read_owned_file(floor))
        if (
            not isinstance(prior, dict) or set(prior) != set(accepted)
            or type(prior["version"]) is not int or prior["version"] != 1
            or prior["target"] != accepted["target"] or prior["controller"] != accepted["controller"]
            or type(prior["directory_revision"]) is not int or prior["directory_revision"] < 1
            or not isinstance(prior["directory_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", prior["directory_digest"]) is None
        ):
            raise ValueError("Gateway provider revision state is invalid; inspect it without resetting it")
        if accepted["directory_revision"] < prior["directory_revision"]:
            raise ValueError("Gateway public directory is older than the accepted revision")
        if (
            accepted["directory_revision"] == prior["directory_revision"]
            and accepted["directory_digest"] != prior["directory_digest"]
        ):
            raise ValueError("Gateway public directory changed without a new revision")
    return floor, gateway_profile._json(accepted)


def accept_directory(value: dict, sources: Path) -> None:
    """Retain the accepted input floor under the caller's directory lock."""
    floor, data = _directory_floor(value, sources)
    if not floor.exists() or gateway_profile._read_owned_file(floor) != data:
        atomic_write_text(floor, data)


def apply_projection(value: dict, *, config_d: Path | None, ssh_config: Path | None) -> Path:
    """Accept a monotonic public-directory revision, then use the sole profile writer."""
    selected = gateway_profile.validate_config(value["config"])
    identity = Path(selected["identity_file"]).resolve(strict=True)
    if not identity.is_file():
        raise ValueError("Gateway identity file is unavailable")
    managed = (config_d or ssh_profile._ssh_dir() / "config.d").expanduser().absolute()
    root_config = (ssh_config or ssh_profile._ssh_dir() / "config").expanduser().resolve()
    sources = managed.parent / gateway_profile._SOURCE_DIR
    if (
        identity == root_config or identity.is_relative_to(managed.resolve())
        or identity.is_relative_to(sources.resolve())
    ):
        raise ValueError("Gateway identity cannot be a managed configuration/source file")
    sources.mkdir(mode=0o700, parents=True, exist_ok=True)
    sources = ssh_profile._regular_config_directory(sources)
    with exclusive_file_lock(sources / "gateway-provider.lock"):
        _directory_floor(value, sources)
        data = gateway_profile._json(selected)
        config = sources / ("gateway-provider-input-" + hashlib.sha256(data.encode()).hexdigest() + ".json")
        if config.exists() or config.is_symlink():
            if gateway_profile._read_owned_file(config) != data:
                raise ValueError("Gateway retained provider input differs from its content address")
        else:
            atomic_write_text(config, data)
        # Accepted input is not applied-state proof. A failed emitter can retry
        # these exact bytes; it cannot roll back to a superseded directory.
        accept_directory(value, sources)
        return gateway_profile.emit_profile(config, config_d=managed, ssh_config=ssh_config)


def command(args) -> int:
    try:
        value = projection(
            args.policy, args.directory, machine=args.machine, environment=args.environment,
            gateway=args.gateway, identity_file=args.identity_file, public_key_file=args.public_key_file,
        )
        fragment = apply_projection(value, config_d=args.config_d, ssh_config=args.ssh_config) if args.apply else None
        print(json.dumps({
            "version": 1, "state": value["state"], "controller": value["controller"],
            "directory_revision": value["directory_revision"], "directory_digest": value["directory_digest"],
            "profiles": [row["name"] for row in value["config"]["machines"]],
            "excluded": value["excluded"], "applied": fragment is not None,
            "fragment": str(fragment) if fragment else None,
            "enrollment_performed": False, "connection_attempted": False,
            "live_readiness_verified": False,
        }, sort_keys=True))
        return 0
    except (OSError, RuntimeError, UnicodeError, ValueError, RecursionError) as exc:
        import sys

        print(
            f"[FAIL] Gateway provider: {exc}. Preserve existing keys and revision state.",
            file=sys.stderr,
        )
        return 2


def add_parser(subparsers):
    parser = subparsers.add_parser("gateway-provider", help="Project Fleet policy and admitted public bindings")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--environment", required=True, choices=("windows", "wsl", "linux"))
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--identity-file", required=True, type=Path)
    parser.add_argument("--public-key-file", required=True, type=Path)
    parser.add_argument("--config-d", type=Path)
    parser.add_argument("--ssh-config", type=Path)
    parser.add_argument("--apply", action="store_true", help="Explicitly publish the managed local profiles")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=command)
