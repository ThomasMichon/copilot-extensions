"""Explicit local scope for Fleet's foreground SSH directory consumer."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .gateway_ssh_policy import ENVIRONMENTS, GatewaySSHPolicy
from .planning import PlanError, canonical, decode, identifier, record

FIELDS = {
    "state_dir", "gateway_origin", "gateway_ca", "namespace", "proof_key_file",
    "access_token_file", "policy_path", "machine", "environment", "gateway",
    "controller", "identity_file", "public_key_file", "authorization_dir",
    "config_d", "ssh_config",
}
PATHS = {
    "state_dir", "proof_key_file", "access_token_file", "policy_path",
    "identity_file", "public_key_file", "config_d", "ssh_config",
}


def absolute(value: object) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise PlanError("SSH client requires absolute path references")
    path = Path(value)
    for candidate in (path, *path.parents):
        if os.path.lexists(candidate):
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PlanError("SSH client refuses linked path references")
    return path


@dataclass(frozen=True)
class SSHMachineConfig:
    encoded: bytes

    @classmethod
    def from_dict(cls, value: dict) -> SSHMachineConfig:
        from .gateway_service_channel import origin, target

        value = decode(canonical(value))
        record(value, "fleet-manager.ssh-client", FIELDS)
        for name in ("machine", "gateway", "controller"):
            identifier(value[name], "SSH client identity")
        if not isinstance(value["environment"], str) or value["environment"] not in ENVIRONMENTS:
            raise PlanError("SSH client environment is unsupported")
        for name in PATHS:
            absolute(value[name])
        for name in ("gateway_ca", "authorization_dir"):
            if value[name] is not None:
                absolute(value[name])
        root = absolute(value["state_dir"]).resolve()
        if root == root.parent or any(
            absolute(value[name]).resolve().is_relative_to(root)
            for name in ("config_d", "ssh_config", "authorization_dir")
            if value[name] is not None
        ):
            raise PlanError("SSH client requires a dedicated state directory")
        origin(value["gateway_origin"])
        path, query = target(value["namespace"])
        if query or path in ("", "/") or path.endswith("/"):
            raise PlanError("SSH client namespace is invalid")
        return cls(canonical(value))

    @classmethod
    def load(cls, path: Path) -> SSHMachineConfig:
        with absolute(str(path)).open("rb") as stream:
            return cls.from_dict(decode(stream.read(1024 * 1024 + 1)))

    def to_dict(self) -> dict:
        return decode(self.encoded)

    def local_scope(self) -> dict:
        value = self.to_dict()
        with absolute(value["policy_path"]).open("rb") as stream:
            policy = GatewaySSHPolicy.from_dict(decode(stream.read(1024 * 1024 + 1)))
        decision = policy.resolve(value["machine"], value["environment"]).to_dict()
        if (
            decision["backend"] != "gateway" or decision["state"] != "configured"
            or decision["controller"] != value["controller"]
            or not decision["gateway_egress"] or decision["host_path"]
        ):
            raise PlanError("SSH client requires a configured direct Gateway policy")
        if decision["effective_inbound"] and value["authorization_dir"] is None:
            raise PlanError("Inbound SSH requires its selected local authorization owner")
        return {
            name: absolute(value[name]) if name in {
                "policy_path", "identity_file", "public_key_file", "authorization_dir",
                "config_d", "ssh_config",
            } and value[name] is not None else value[name]
            for name in (
                "policy_path", "machine", "environment", "gateway", "controller",
                "identity_file", "public_key_file", "authorization_dir", "config_d", "ssh_config",
            )
        }
