"""Compatibility with an existing native OpenSSH Gateway's public commands.

The embedding supplies the selected authenticated management transport. A
forwarding-only relay identity cannot execute these commands. This adapter
does not create that transport, authenticate it, or own admission policy.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any

from . import gateway_profile

CAPABILITY = "gateway-native-public-commands-v1"
MAX_BYTES = gateway_profile.MAX_INPUT_BYTES
Document = dict[str, Any]
Command = Callable[[tuple[str, ...], bytes | None], bytes]


class AdmissionUncertain(RuntimeError):
    """Admission may have committed; inspect the same identity before retrying."""


def _encode(value: object) -> bytes:
    data = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(data) > MAX_BYTES:
        raise ValueError("Native Gateway public input exceeds 1 MiB")
    return data


def _decode(data: bytes) -> Document:
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("Native Gateway response must be bounded JSON bytes")
    value = json.loads(data, object_pairs_hook=gateway_profile._object)
    if not isinstance(value, dict):
        raise ValueError("Native Gateway response must be a JSON object")
    _encode(value)
    return value


def _fields(value: object, expected: set[str]) -> Document:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Native Gateway public fields differ from the selected contract")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", value) is None:
        raise ValueError("Native Gateway requires an exact login user")
    return value


def _port(value: object) -> int:
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError("Native Gateway requires a nonprivileged relay port")
    return value


def _descriptor(value: object) -> Document:
    record = _fields(value, {
        "schema", "backend", "machine", "gateway_user", "host_key",
        "server_port", "relay_port", "peers",
    })
    if (
        type(record["schema"]) is not int or record["schema"] != 1
        or record["backend"] != "openssh-reverse"
        or type(record["server_port"]) is not int or record["server_port"] != 22
    ):
        raise ValueError("Unsupported native Gateway descriptor contract")
    machine = gateway_profile._alias(record["machine"])
    peers = record["peers"]
    if not isinstance(peers, list) or not 1 <= len(peers) <= gateway_profile.MAX_MACHINES:
        raise ValueError("Native Gateway requires a bounded nonempty topology")
    names: set[str] = set()
    ports: set[int] = set()
    topology = {}
    for peer in peers:
        row = _fields(peer, {"machine", "relay_port"})
        name, port = gateway_profile._alias(row["machine"]), _port(row["relay_port"])
        if name.casefold() in names or port in ports:
            raise ValueError("Native Gateway topology contains conflicting names or ports")
        names.add(name.casefold())
        ports.add(port)
        topology[name] = port
    if topology.get(machine) != _port(record["relay_port"]):
        raise ValueError("Native Gateway target relay differs from its topology")
    return {
        **record, "gateway_user": _user(record["gateway_user"]),
        "host_key": gateway_profile._public_key(record["host_key"]),
        "peers": [{"machine": name, "relay_port": topology[name]} for name in sorted(topology)],
    }


def _peer(value: object) -> Document:
    record = _fields(value, {"machine", "user", "public_key", "host_key"})
    public, host = (gateway_profile._public_key(record[field]) for field in ("public_key", "host_key"))
    if public == host:
        raise ValueError("Native Gateway client and host keys must be separate")
    return {
        "machine": gateway_profile._alias(record["machine"]), "user": _user(record["user"]),
        "public_key": public, "host_key": host,
    }


def _directory(value: object, descriptor: Document) -> Document:
    record = _fields(value, {"peers"})
    if not isinstance(record["peers"], list) or len(record["peers"]) > gateway_profile.MAX_MACHINES:
        raise ValueError("Native Gateway requires a bounded public directory")
    topology = {row["machine"] for row in descriptor["peers"]}
    names: set[str] = set()
    keys: set[str] = set()
    peers = []
    for value in record["peers"]:
        peer = _peer(value)
        if peer["machine"] not in topology or peer["machine"].casefold() in names:
            raise ValueError("Native Gateway directory differs from its topology")
        if peer["public_key"] in keys or peer["host_key"] in keys:
            raise ValueError("Native Gateway directory contains conflicting identities")
        names.add(peer["machine"].casefold())
        keys.update((peer["public_key"], peer["host_key"]))
        peers.append(peer)
    return {"peers": sorted(peers, key=lambda row: row["machine"])}


@dataclass(frozen=True)
class Observation:
    """Public observation, not an SSH-readiness or versioned-directory claim."""

    descriptor_json: bytes
    directory_json: bytes
    admission_submitted: bool = False

    @property
    def descriptor(self) -> Document:
        """Return independent public descriptor data."""
        return _decode(self.descriptor_json)

    @property
    def directory(self) -> Document:
        """Return independent public membership data without fabricating a revision."""
        return _decode(self.directory_json)

    def validated(self, descriptor: object) -> Observation:
        """Bind public data to the embedding's independently selected Gateway.

        This validates bindings, not the authenticity of the supplying transport.
        """
        expected = _descriptor(descriptor)
        observed = _descriptor(self.descriptor)
        stable = set(expected) - {"peers"}
        old_ports = {row["machine"]: row["relay_port"] for row in expected["peers"]}
        new_ports = {row["machine"]: row["relay_port"] for row in observed["peers"]}
        if (
            any(observed[field] != expected[field] for field in stable)
            or any(new_ports.get(name) != port for name, port in old_ports.items())
        ):
            raise PermissionError("Native Gateway descriptor differs from the selected bindings")
        return Observation(
            _encode(observed), _encode(_directory(self.directory, observed)),
            self.admission_submitted,
        )


class NativeGateway:
    """Use only the documented native baseline over a caller-owned transport.

    ``call`` receives fixed command tokens relative to the already selected
    Gateway launcher, and bounded JSON stdin or None. It must enforce its own
    deadline/output limit, check native exit status and preserve failures.
    """

    def __init__(self, call: Command, descriptor: object):
        self._call = call
        self._expected = _descriptor(deepcopy(descriptor))

    def _invoke(self, command: tuple[str, ...], payload: object = None) -> Document:
        return _decode(self._call(command, None if payload is None else _encode(payload) + b"\n"))

    def inspect(self) -> Observation:
        """Read the selected provider and its enabled public membership."""
        expected = self._expected
        descriptor = _descriptor(self._invoke(("export-client", "--machine", expected["machine"])))
        Observation(_encode(descriptor), _encode({"peers": []})).validated(expected)
        directory = _directory(self._invoke(("export-directory",)), descriptor)
        status = self._invoke(("status",))
        if (
            status.get("installed") is not True or status.get("enabled") is not True
            or status.get("enrollment_enabled") is not bool(directory["peers"])
        ):
            raise PermissionError("Native Gateway enrollment is not enabled")
        return Observation(_encode(descriptor), _encode(directory))

    def enroll(self, peer: object, *, existing_only: bool = False) -> Observation:
        """Admit one identity, or reconcile it read-only after an uncertain reply.

        Calling without existing_only is an explicit admission operation; the
        embedding must journal its intent before this method. No failed write
        is replayed here, and existing different keys never trigger rotation.
        """
        if type(existing_only) is not bool:
            raise ValueError("Select an explicit native Gateway reconciliation mode")
        selected = _peer(peer)
        if selected["machine"] != self._expected["machine"]:
            raise ValueError("Native Gateway admission differs from the selected target")
        before = self.inspect()
        current = {row["machine"]: row for row in before.directory["peers"]}
        prior = current.get(selected["machine"])
        if prior is not None:
            if prior != selected:
                raise PermissionError("Native Gateway identity differs; implicit rotation is refused")
            return before
        _directory({"peers": [*current.values(), selected]}, before.descriptor)
        if existing_only:
            raise PermissionError("Existing native Gateway enrollment is not confirmed")
        try:
            result = self._invoke(("import-peers",), {"peers": [selected]})
            if result.get("enrollment_enabled") is not True:
                raise ValueError("Native Gateway did not confirm enabled enrollment")
            after = self.inspect()
            confirmed = {row["machine"]: row for row in after.directory["peers"]}
            if confirmed.get(selected["machine"]) != selected or any(
                confirmed.get(name) != row for name, row in current.items()
            ):
                raise ValueError("Native Gateway did not retain the admitted public identities")
        except (Exception, KeyboardInterrupt) as exc:
            raise AdmissionUncertain(
                "Native Gateway admission outcome is uncertain; preserve the request and "
                "reconcile this identity with existing_only before another admission"
            ) from exc
        return Observation(after.descriptor_json, after.directory_json, True)
