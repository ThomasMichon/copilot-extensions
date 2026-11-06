"""Explicit normal setup inputs for the existing installed SSH application."""

from __future__ import annotations

import os
from pathlib import Path
import re

from . import continuity_bootstrap as continuity
from .gateway_host import CONFIG, prepare
from .gateway_service_channel import check, origin
from .planning import PlanError, canonical, decode, digest, identifier, record
from .private_state import create_private_file, private_directory, read_private_file
from .ssh_machine import _consumer
from .ssh_machine_config import SSHMachineConfig, absolute

CAPABILITY = "continuity-ssh-setup-v1"
PIN_CAPABILITY = "continuity-ssh-setup-pin-v1"
SCHEMA = "fleet-manager.continuity-ssh-setup"


def describe(
    request: Path, *, source: Path, harness: str, state_root: Path,
    expected_request_digest: str | None = None,
) -> tuple[dict, SSHMachineConfig]:
    """Read public setup bindings, not the retained private machine identity."""
    value = decode(read_private_file(absolute(str(request)), 16384))
    record(value, SCHEMA, {
        "snapshot", "root", "selection", "authority", "installation", "ssh",
    })
    request_digest = digest(value)
    if expected_request_digest is not None and (
        not isinstance(expected_request_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_request_digest) is None
        or expected_request_digest != request_digest
    ):
        raise PlanError("SSH setup request differs from the inspected inputs")
    for name in ("snapshot", "root"):
        if ".." in absolute(value[name]).parts:
            raise PlanError("SSH setup requires normalized absolute installation references")
    for name in ("authority", "installation"):
        identifier(value[name], "SSH setup owner")
    selected = value["selection"]
    if not isinstance(selected, dict) or set(selected) != {
        "source_commit", "source_tree", "runtime_version", "payload_hash",
    } or any(not isinstance(item, str) for item in selected.values()):
        raise PlanError("SSH setup requires an exact Source/profile selection")
    if (
        any(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", selected[name]) is None
            for name in ("source_commit", "source_tree"))
        or re.fullmatch(r"[0-9][A-Za-z0-9._+-]{0,255}", selected["runtime_version"]) is None
        or re.fullmatch(r"[0-9a-f]{64}", selected["payload_hash"]) is None
    ):
        raise PlanError("SSH setup Source/profile selection is invalid")
    ssh = value["ssh"]
    if not isinstance(ssh, dict) or "credential_source" in ssh or ssh.get("version") != 3:
        raise PlanError("SSH setup requires v3 public inputs without a credential override")
    config = SSHMachineConfig.from_dict(ssh | {"credential_source": {
        "provider": continuity.PROVIDER, "source": str(source),
        "harness": harness, "state_root": str(state_root),
    }})
    if len(config.encoded) > 16384:
        raise PlanError("SSH setup configuration exceeds its retained input bound")
    continuity._locations(source, state_root)
    projection = continuity._projection(source, harness)
    scope, authentication = projection["delegation"]["scope"], ssh["authentication"]
    check(
        scope["machine"] == ssh["machine"]
        and origin(scope["destination"]) == origin(ssh["gateway_origin"])
        and scope["principal"] == authentication["principal"]
        and projection["audience"] == authentication["audience"]
        and authentication["resource"] in scope["slots"]
        and projection["gateway_ca"] == ssh["gateway_ca"],
        "continuity-ssh-setup-scope-mismatch",
    )
    root = Path(value["root"])
    snapshot = Path(value["snapshot"])
    lease = Path(ssh["state_dir"])
    for other in (snapshot, state_root, lease):
        if root.resolve().is_relative_to(other.resolve()) or other.resolve().is_relative_to(root.resolve()):
            raise PlanError("SSH setup installation must be separate from source, identity and lease state")
    if any(path.resolve().is_relative_to(root.resolve()) for path in (request, *config.input_paths())):
        raise PlanError("SSH setup inputs must remain outside the installation's write scope")
    for path in (root, *config.input_paths()):
        if "session-state" in path.parts or any((parent / ".git").exists() for parent in (path, *path.parents)):
            raise PlanError("SSH setup cannot retain runtime inputs in a checkout or session folder")
    if not root.parent.is_dir():
        raise PlanError("SSH setup installation parent must already exist")
    if os.path.lexists(root) and not root.is_dir():
        raise PlanError("SSH setup installation root must be an ordinary directory")
    from .gateway_host_start import pending_launch

    if pending_launch(root) is not None:
        raise PlanError("SSH setup preserves an unresolved host launch; reconcile its original owner")
    _consumer(config)
    from agent_ssh.gateway_directory import validate_consumer_state
    from .gateway_service_listener import read_bounded
    from .gateway_ssh_provider import public_key

    raw = read_bounded(Path(ssh["public_key_file"]))
    if len(raw) > 256:
        raise PlanError("SSH setup public-key reference exceeds its bound")
    public_key(raw.decode("ascii").strip())
    for path in config.input_paths():
        if path not in (source, state_root) and path != Path(ssh["public_key_file"]):
            read_bounded(path)

    for path in (request, snapshot, root, root / "ssh-client.json", root / CONFIG):
        validate_consumer_state(
            path, config_d=Path(ssh["config_d"]), ssh_config=Path(ssh["ssh_config"]),
            authorization_dir=Path(ssh["authorization_dir"]) if ssh["authorization_dir"] else None,
        )
    selection = {
        "schema": "fleet-manager.gateway-host", "version": 2, "role": "ssh-client",
        "configuration": str(root / "ssh-client.json"),
        "authority": value["authority"], "installation": value["installation"],
    }
    for path, expected in ((root / "ssh-client.json", config.to_dict()), (root / CONFIG, selection)):
        if os.path.lexists(path) and decode(read_private_file(path, 16384)) != expected:
            raise PlanError("SSH setup preserves changed or foreign host configuration; reconcile through its owner")
    return {
        "schema": SCHEMA, "version": 1,
        **{name: value[name] for name in ("snapshot", "root", "selection")},
        "request_digest": request_digest, "host": selection,
    }, config


def run(
    request: Path, *, source: Path, harness: str, state_root: Path, apply: bool,
    expected_request_digest: str | None = None,
) -> dict:
    description, config = describe(
        request, source=source, harness=harness, state_root=state_root,
        expected_request_digest=expected_request_digest,
    )
    if apply:
        root = Path(description["root"])
        prepare(root, description["host"], configuration=config)
        # Configuration is only created or reused; this is not migration authority.
        private_directory(root)
        for path, expected in (
            (root / "ssh-client.json", config.to_dict()), (root / CONFIG, description["host"]),
        ):
            try:
                descriptor = create_private_file(path)
            except FileExistsError:
                if decode(read_private_file(path, 16384)) != expected:
                    raise PlanError("SSH setup configuration changed during preparation")
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(canonical(expected))
                    stream.flush()
                    os.fsync(stream.fileno())
        current, _ = describe(
            request, source=source, harness=harness, state_root=state_root,
            expected_request_digest=expected_request_digest,
        )
        if current != description:
            raise PlanError("SSH setup inputs changed during preparation")
    return description
