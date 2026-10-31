"""Explicit deployment policy, independent of a harness or private inventory."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from fleet_manager.gateway_ssh_policy import ENVIRONMENTS, GatewaySSHPolicy
from fleet_manager.gateway_ssh_provider import public_key
from fleet_manager.planning import canonical, decode, identifier, integer, record

MAX_MACHINES = 32


class SSHServiceError(ValueError):
    """Value-free service diagnostic, safe for an authenticated response."""


def check(condition: bool, code: str) -> None:
    if not condition:
        raise SSHServiceError(code)


def absolute(value: object) -> Path:
    check(isinstance(value, str) and bool(value), "invalid-path-reference")
    path = Path(value)
    check(path.is_absolute(), "absolute-path-required")
    for parent in (path, *path.parents):
        if os.path.lexists(parent):
            info = parent.lstat()
            check(not parent.is_symlink()
                  and not getattr(info, "st_file_attributes", 0) & 0x400,
                  "linked-path-refused")
    return path


@dataclass(frozen=True)
class ServiceConfig:
    encoded: bytes

    @classmethod
    def from_dict(cls, value: dict) -> ServiceConfig:
        value = decode(canonical(value))
        record(value, "agent-ssh.service", {
            "revision", "data_dir", "fleet", "gateway", "slot", "controller",
            "gateway_origin", "gateway_ca", "proof_key_file", "access_token_file",
            "policy", "bindings",
        }, optional={"relay", "enrollment_policy"})
        integer(value["revision"], "SSH configuration revision", minimum=1)
        for name in ("fleet", "gateway", "slot", "controller"):
            identifier(value[name], "SSH service identity")
        for name in ("data_dir", "proof_key_file", "access_token_file"):
            absolute(value[name])
        root = absolute(value["data_dir"])
        check(root != root.parent, "dedicated-service-data-directory-required")
        if value["gateway_ca"] is not None:
            absolute(value["gateway_ca"])
        from fleet_manager.gateway_service_channel import origin

        origin(value["gateway_origin"])
        policy = GatewaySSHPolicy.from_dict(value["policy"])
        check(value["controller"] in policy.to_dict()["controllers"], "unknown-controller")
        if "relay" in value:
            relay = value["relay"]
            check(isinstance(relay, dict) and set(relay) == {
                "sshd", "ssh_keygen", "host_key_file", "host_public_key",
                "listen_host", "port", "user",
            }, "invalid-native-relay")
            for name in ("sshd", "ssh_keygen", "host_key_file"):
                path = absolute(relay[name])
                check(not any(char in str(path) for char in '"%\r\n'),
                      "invalid-native-relay-path")
            public_key(relay["host_public_key"])
            integer(relay["port"], "SSH relay port", minimum=1024, maximum=65535)
            check(relay["listen_host"] in ("127.0.0.1", "0.0.0.0"),
                  "invalid-native-relay-address")
            check(isinstance(relay["user"], str)
                  and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", relay["user"]) is not None,
                  "invalid-native-relay-user")
        rows = value["bindings"]
        check(isinstance(rows, list) and 1 <= len(rows) <= MAX_MACHINES, "invalid-bindings")
        targets, principals, aliases, ports = set(), set(), set(), set()
        for row in rows:
            check(isinstance(row, dict) and set(row) == {
                "machine", "environment", "principal", "generation", "alias", "user", "reverse_port",
            }, "invalid-machine-binding")
            for field in ("machine", "principal", "alias"):
                identifier(row[field], "SSH machine binding")
            integer(row["generation"], "SSH enrollment generation", minimum=1)
            check(row["environment"] in ENVIRONMENTS, "invalid-environment")
            check(isinstance(row["user"], str)
                  and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", row["user"]) is not None,
                  "invalid-login-user")
            target = (row["machine"], row["environment"])
            decision = policy.resolve(*target).to_dict()
            check(decision["backend"] == "gateway"
                  and decision["controller"] == value["controller"], "controller-mismatch")
            if "relay" in value and decision["state"] == "configured":
                check(not decision["host_path"], "native-relay-host-mediation-unsupported")
                check(decision["effective_inbound"] and decision["effective_outbound"],
                      "native-relay-requires-bidirectional-policy")
            port = row["reverse_port"]
            if port is not None:
                integer(port, "SSH reverse port", minimum=1, maximum=65535)
                check(port not in ports, "duplicate-reverse-port")
                ports.add(port)
                if "relay" in value:
                    check(port != value["relay"]["port"], "relay-port-collision")
            check(not decision["effective_inbound"] or port is not None,
                  "inbound-reverse-port-required")
            check(target not in targets and row["principal"] not in principals
                  and row["alias"].casefold() not in aliases, "duplicate-machine-binding")
            targets.add(target)
            principals.add(row["principal"])
            aliases.add(row["alias"].casefold())
        if "enrollment_policy" in value:
            admission = value["enrollment_policy"]
            check(isinstance(admission, dict) and set(admission) == {
                "mode", "recovery_principals",
            } and admission["mode"] == "peer-approval", "invalid-enrollment-policy")
            recovery = admission["recovery_principals"]
            check(isinstance(recovery, list) and len(recovery) <= MAX_MACHINES,
                  "invalid-recovery-principals")
            for principal in recovery:
                identifier(principal, "SSH recovery principal")
            check(len(set(recovery)) == len(recovery)
                  and not principals.intersection(recovery), "recovery-principal-not-distinct")
        return cls(canonical(value))

    @classmethod
    def load(cls, path: Path) -> ServiceConfig:
        path = absolute(str(path))
        with path.open("rb") as stream:
            return cls.from_dict(decode(stream.read(1024 * 1024 + 1)))

    def to_dict(self) -> dict:
        return decode(self.encoded)

    def binding(self, principal: str) -> dict:
        matches = [row for row in self.to_dict()["bindings"] if row["principal"] == principal]
        check(len(matches) == 1, "machine-principal-not-admitted")
        return matches[0]

    @property
    def enrollment_mode(self) -> str:
        return self.to_dict().get("enrollment_policy", {}).get("mode", "configured-bindings")

    @property
    def recovery_principals(self) -> list[str]:
        return self.to_dict().get("enrollment_policy", {}).get("recovery_principals", [])

    def authorize_caller(self, principal: str) -> None:
        if principal not in self.recovery_principals:
            self.binding(principal)

    @property
    def authority(self) -> dict:
        value = self.to_dict()
        fields = (
            "revision", "fleet", "gateway", "slot", "controller", "policy", "bindings",
        ) + tuple(name for name in ("relay", "enrollment_policy") if name in value)
        return {name: value[name] for name in fields}
