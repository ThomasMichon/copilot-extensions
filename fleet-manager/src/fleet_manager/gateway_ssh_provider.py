"""Project selected public Gateway bindings through the existing directional policy."""

from __future__ import annotations

import base64
import binascii
import re
import struct

from .gateway_ssh_policy import ENVIRONMENTS, GatewaySSHPolicy
from .gateway_ssh_runtime import MAX_PROFILES
from .planning import PlanError, canonical, decode, digest, identifier, integer, record

_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")


def public_key(value):
    if (
        not isinstance(value, str) or len(value) > 256
        or any(not 32 <= ord(char) <= 126 for char in value)
    ):
        raise PlanError("Gateway public key must be bounded single-line ed25519")
    fields = value.split()
    if len(fields) != 2 or fields[0] != "ssh-ed25519":
        raise PlanError("Gateway public key must be canonical ed25519")
    try:
        wire = base64.b64decode(fields[1], validate=True)
    except (ValueError, binascii.Error):
        raise PlanError("Gateway public key encoding is invalid") from None
    prefix = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32)
    if len(wire) != 51 or wire[:19] != prefix or base64.b64encode(wire).decode("ascii") != fields[1]:
        raise PlanError("Gateway public key wire format is invalid")
    if " ".join(fields) != value:
        raise PlanError("Gateway public key must be canonical ed25519")
    return value


def directory_snapshot(value, policy: GatewaySSHPolicy):
    """Validate an owner-selected public directory; parsing does not admit it."""
    if not isinstance(policy, GatewaySSHPolicy):
        raise PlanError("typed Gateway SSH policy required")
    value = decode(canonical(value))
    record(value, "fleet-manager.gateway-ssh-directory", {
        "controller", "revision", "policy_digest", "machines",
    })
    identifier(value["controller"], "Gateway directory controller")
    integer(value["revision"], "Gateway directory revision", minimum=1)
    if value["policy_digest"] != policy.policy_digest:
        raise PlanError("Gateway directory does not match current selected policy")
    if value["controller"] not in policy.to_dict()["controllers"]:
        raise PlanError("Gateway directory controller is not declared")
    rows = value["machines"]
    if not isinstance(rows, list):
        raise PlanError("Gateway directory machines must be a bounded list")
    names, targets, ports, keys = set(), set(), set(), set()
    normalized = []
    for row in rows:
        fields = {"machine", "environment", "alias", "user", "public_key", "host_key", "reverse_port"}
        if not isinstance(row, dict) or set(row) != fields:
            raise PlanError("Gateway directory machine fields are invalid")
        identifier(row["machine"], "Gateway directory machine")
        identifier(row["alias"], "Gateway directory alias")
        if row["environment"] not in ENVIRONMENTS:
            raise PlanError("Gateway directory environment is invalid")
        if not isinstance(row["user"], str) or not _USER.fullmatch(row["user"]):
            raise PlanError("Gateway directory login user is invalid")
        target = (row["machine"], row["environment"])
        if target in targets or row["alias"].casefold() in names:
            raise PlanError("Gateway directory has duplicate targets or aliases")
        targets.add(target)
        names.add(row["alias"].casefold())
        key = public_key(row["public_key"])
        if key in keys:
            raise PlanError("Gateway directory machines must have distinct authentication keys")
        keys.add(key)
        decision = policy.resolve(*target).to_dict()
        if decision["backend"] != "gateway" or decision["controller"] != value["controller"]:
            raise PlanError("Gateway directory target differs from selected controller policy")
        if row["reverse_port"] is not None:
            integer(row["reverse_port"], "Gateway reverse port", minimum=1, maximum=65535)
            if row["reverse_port"] in ports:
                raise PlanError("Gateway directory reverse ports must be distinct")
            ports.add(row["reverse_port"])
        if row["host_key"] is not None:
            public_key(row["host_key"])
        if decision["effective_inbound"] and (row["host_key"] is None or row["reverse_port"] is None):
            raise PlanError("Admitted inbound Gateway target requires its host pin and reverse port")
        normalized.append(dict(row))
    value["machines"] = sorted(normalized, key=lambda row: (row["machine"], row["environment"]))
    return value


def project_gateway_profiles(
    policy: GatewaySSHPolicy, directory, *, machine: str, environment: str,
    gateway: str, identity_file: str, local_public_key: str,
):
    """Compile the owner-selected snapshot, never credentials or execution grants.

    The setup owner supplies current policy and an authenticated/admitted public
    directory. This projection does not authenticate their source or fetch it.
    The agent-ssh consumer validates and publishes the resulting normal profile
    configuration; the native authorization and connector owners remain separate.
    """
    selected = directory_snapshot(directory, policy)
    identifier(gateway, "Gateway SSH alias")
    if not isinstance(identity_file, str) or not identity_file or len(identity_file) > 4096:
        raise PlanError("Gateway local identity reference is invalid")
    decision = policy.resolve(machine, environment).to_dict()
    if decision["backend"] != "gateway" or decision["controller"] != selected["controller"]:
        raise PlanError("Gateway source differs from selected controller policy")
    local = next((
        row for row in selected["machines"]
        if (row["machine"], row["environment"]) == (machine, environment)
    ), None)
    if local is None or public_key(local_public_key) != local["public_key"]:
        raise PlanError("Gateway local identity is not the selected admitted directory key")
    if decision["host_path"]:
        raise PlanError("Gateway host-mediated source requires its owning mediation adapter")
    machines = []
    excluded = []
    for row in selected["machines"]:
        peer = policy.resolve(row["machine"], row["environment"]).to_dict()
        reason = None
        if decision["state"] != "configured" or not decision["effective_outbound"]:
            reason = "source-outbound-disabled"
        elif peer["state"] != "configured" or not peer["effective_inbound"]:
            reason = "peer-inbound-disabled"
        if reason:
            excluded.append({"machine": row["machine"], "environment": row["environment"], "reason": reason})
            continue
        if peer["host_path"]:
            raise PlanError("Gateway host-mediated peer requires its owning mediation adapter")
        machines.append({
            "name": row["alias"], "user": row["user"],
            "reverse_port": row["reverse_port"], "host_key": row["host_key"],
        })
    if len(machines) > MAX_PROFILES:
        raise PlanError("Gateway profile projection exceeds the existing reconciliation bound")
    config = {
        "schema": "agent-ssh.gateway-profile", "version": 1,
        "gateway": gateway, "identity_file": identity_file,
        "machines": sorted(machines, key=lambda row: row["name"]),
    }
    return {
        "schema": "fleet-manager.gateway-ssh-provider", "version": 1,
        "target": {"machine": machine, "environment": environment},
        "controller": selected["controller"], "directory_revision": selected["revision"],
        "directory_digest": digest(selected), "policy_digest": policy.policy_digest,
        "decision_digest": digest(decision), "state": decision["state"],
        "config": config, "excluded": excluded,
        "execution_authorized": False, "live_readiness_verified": False,
    }


def project_gateway_authorization(
    policy: GatewaySSHPolicy, directory, *, machine: str, environment: str,
    local_public_key: str,
):
    """Select peer login keys, not Gateway forwarding or setup authority.

    An explicitly selected target-local sshd consumes the dedicated output.
    Gateway relay enrollment and host-mediated guest access are separate owners.
    """
    selected = directory_snapshot(directory, policy)
    decision = policy.resolve(machine, environment).to_dict()
    if decision["backend"] != "gateway" or decision["controller"] != selected["controller"]:
        raise PlanError("Gateway authorization target differs from selected controller policy")
    local = next((
        row for row in selected["machines"]
        if (row["machine"], row["environment"]) == (machine, environment)
    ), None)
    if local is None or public_key(local_public_key) != local["public_key"]:
        raise PlanError("Gateway local identity is not the selected admitted directory key")
    if decision["host_path"]:
        raise PlanError("Gateway host-mediated target requires its owning mediation adapter")
    peers = []
    if decision["state"] == "configured" and decision["effective_inbound"]:
        for row in selected["machines"]:
            source = policy.resolve(row["machine"], row["environment"]).to_dict()
            if source["state"] != "configured" or not source["effective_outbound"]:
                continue
            if source["host_path"]:
                raise PlanError("Gateway host-mediated source requires its owning mediation adapter")
            peers.append({
                "machine": row["machine"], "environment": row["environment"],
                "public_key": row["public_key"],
            })
    if len(peers) > MAX_PROFILES:
        raise PlanError("Gateway authorization exceeds the existing reconciliation bound")
    return {
        "schema": "fleet-manager.gateway-ssh-authorization", "version": 1,
        "target": {"machine": machine, "environment": environment},
        "controller": selected["controller"], "directory_revision": selected["revision"],
        "directory_digest": digest(selected), "policy_digest": policy.policy_digest,
        "decision_digest": digest(decision), "state": decision["state"],
        "user": local["user"], "host_key": local["host_key"], "peers": peers,
        "execution_authorized": False, "live_readiness_verified": False,
    }
