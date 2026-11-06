"""Native source account-relay consumer; no broker dependency or extra wheel."""

from __future__ import annotations

import json
import math
import socket
import time
import ipaddress
import stat
import struct
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_LEASE_CONTRACT = "credential-relay.account-source-lease/v1"
SOURCE_LEASE_EXCHANGE = "connection-bound/v1"


@dataclass
class SourceLease:
    """Source authorization, not token expiry or permission to resume execution."""

    token: str = field(repr=False)
    lease_id: str
    lease_issued_at: float
    lease_expires_at: float
    _last_wall: float = field(repr=False)
    _received_monotonic: float = field(repr=False)
    _budget: float = field(repr=False)
    _expired: bool = field(default=False, init=False, repr=False)
    _last_elapsed: float = field(default=0.0, init=False, repr=False)

    def remaining_seconds(self) -> float:
        now = time.time()
        elapsed = time.monotonic() - self._received_monotonic
        remaining = min(self.lease_expires_at - now, self._budget - elapsed)
        if now < self._last_wall or elapsed < self._last_elapsed or remaining <= 0:
            self._expired = True
        self._last_wall = now
        self._last_elapsed = elapsed
        return 0.0 if self._expired else remaining


def _reference(path, limit=65536):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("account-relay-reference-invalid")
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if not value or len(value) > limit:
        raise ValueError("account-relay-reference-invalid")
    return value


def _text(value):
    if (
        not isinstance(value, str) or not value or len(value) > 2048
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise ValueError("account-relay-policy-invalid")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("account-relay-policy-invalid")
        result[key] = value
    return result


def _selection(path: Path, *, source: str, account: str, host: str | None = None):
    """Select an approved binding without reading its stored capability."""
    try:
        policy = json.loads(_reference(path), object_pairs_hook=_pairs)
        if (
            not isinstance(policy, dict)
            or set(policy) != {"schema", "version", "transport", "bindings"}
            or policy["schema"] != "credential-relay.account-worker"
            or type(policy["version"]) is not int or policy["version"] != 1
            or not isinstance(policy["bindings"], list)
            or not 0 < len(policy["bindings"]) <= 128
        ):
            raise ValueError
        matches = []
        identities = set()
        keys = {"source", "account", "recipient", "host", "action", "capability_file"}
        for binding in policy["bindings"]:
            if not isinstance(binding, dict) or set(binding) != keys:
                raise ValueError
            for key, value in binding.items():
                if key == "capability_file":
                    if (
                        not isinstance(value, str) or not Path(value).is_absolute()
                        or any(ord(char) < 32 for char in value)
                    ):
                        raise ValueError
                else:
                    _text(value)
            identity = binding["source"], binding["account"]
            if identity in identities or binding["action"] != "get-github-token":
                raise ValueError
            identities.add(identity)
            if identity == (source, account):
                matches.append(binding)
        if len(matches) != 1:
            raise ValueError
        binding = matches[0]
        if host is not None and binding["host"] != host:
            raise ValueError
        return policy["transport"], binding
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RuntimeError("source-account-relay-unavailable") from None


def acquire(path: Path, *, source: str, account: str, host: str | None = None,
            timeout: float = 20) -> str:
    """Native TokenProvider: reread the selected capability for each operation."""
    transport, binding = _selection(path, source=source, account=account, host=host)
    try:
        capability = _reference(binding["capability_file"], 4096).decode("ascii").strip()
        fields = {key: value for key, value in binding.items() if key != "capability_file"}
        return exchange(transport, fields, capability, timeout=timeout)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise RuntimeError("source-account-relay-unavailable") from None


def exchange_for(path: Path, fields: dict, capability: str, *, host: str) -> str:
    """HTTP façade: never borrow the native worker's stored capability."""
    transport, binding = _selection(
        path, source=fields.get("source"), account=fields.get("account"), host=host,
    )
    expected = {key: value for key, value in binding.items() if key != "capability_file"}
    if fields != expected:
        raise RuntimeError("source-account-relay-unavailable")
    return exchange(transport, fields, capability)


def exchange(transport: dict, fields: dict, capability: str, *, timeout: float = 20) -> str:
    """Fixed account operation for an embedding's already-admitted caller.

    This does not authenticate an HTTP caller; the caller must possess the
    exact source/account/recipient capability checked by the source custodian.
    No arbitrary operation, URL, redirect or credential cache is supported.
    """
    wire = _account_wire("get-account-token", fields, capability)
    response = _roundtrip(transport, wire, timeout=timeout)
    return _response_token(response, fields)


def acquire_source_lease(
    path: Path, *, source: str, account: str, host: str | None = None, timeout: float = 20,
) -> SourceLease:
    """Read the selected capability only after the serving peer declares support."""
    transport, binding = _selection(path, source=source, account=account, host=host)
    fields = {key: value for key, value in binding.items() if key != "capability_file"}

    def capability():
        return _reference(binding["capability_file"], 4096).decode("ascii").strip()

    return _source_lease(transport, fields, capability, timeout=timeout)


def exchange_source_lease_for(
    path: Path, fields: dict, capability: str, *, host: str, timeout: float = 20,
) -> SourceLease:
    """Embedding seam using only the admitted caller's capability, never a stored one."""
    transport, binding = _selection(
        path, source=fields.get("source"), account=fields.get("account"), host=host,
    )
    expected = {key: value for key, value in binding.items() if key != "capability_file"}
    if fields != expected:
        raise RuntimeError("source-account-relay-unavailable")
    return exchange_source_lease(transport, fields, capability, timeout=timeout)


def exchange_source_lease(
    transport: dict, fields: dict, capability: str, *, timeout: float = 20,
) -> SourceLease:
    """Negotiate and acquire once; unsupported peers never receive a capability."""
    return _source_lease(transport, fields, lambda: capability, timeout=timeout)


def _source_lease(transport, fields, capability, *, timeout):
    try:
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise ValueError
        started = time.monotonic()
        started_at = time.time()

        def acquire(support):
            if support != {
                "contract": SOURCE_LEASE_CONTRACT, "operation": "get-account-source-lease",
                "exchange": SOURCE_LEASE_EXCHANGE,
            }:
                raise RuntimeError("source-account-relay-lease-unsupported")
            return _account_wire("get-account-source-lease", fields, capability())

        response = _roundtrip(
            transport, f"describe-account-relay\nexchange={SOURCE_LEASE_EXCHANGE}\n\n",
            timeout=timeout, followup=acquire,
        )
        token = _response_token(response, fields, source_lease=True)
        if (
            response["lease_contract"] != SOURCE_LEASE_CONTRACT
            or len(response["lease_id"]) != 32
            or any(char not in "0123456789abcdef" for char in response["lease_id"])
        ):
            raise ValueError
        issued = float(response["lease_issued_at"])
        expires = float(response["lease_expires_at"])
        received_at = time.time()
        received = time.monotonic()
        if (
            not math.isfinite(issued) or not math.isfinite(expires)
            or not 0 < issued < expires < 1e12 or expires - issued > 3600
            or received_at < started_at or issued > received_at or received < started
        ):
            raise ValueError
        budget = min(expires - received_at, expires - issued - (received - started))
        if budget <= 0:
            raise ValueError
        return SourceLease(
            token, response["lease_id"], issued, expires, received_at, received, budget,
        )
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RuntimeError("source-account-relay-unavailable") from None


def _account_wire(operation, fields, capability):
    try:
        if set(fields) != {"source", "account", "recipient", "host", "action"}:
            raise ValueError
        for value in fields.values():
            _text(value)
        if fields["action"] != "get-github-token" or not 32 <= len(capability) <= 4096:
            raise ValueError
        _text(capability)
        request = dict(fields, auth=capability)
        return operation + "\n" + "\n".join(f"{k}={v}" for k, v in request.items()) + "\n\n"
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("source-account-relay-unavailable") from None


def _response_token(response, fields, *, source_lease=False):
    try:
        keys = {"protocol", "host", "username", "token"}
        if source_lease:
            keys |= {"lease_contract", "lease_id", "lease_issued_at", "lease_expires_at"}
        if (
            set(response) != keys
            or response["protocol"] != "https" or response["host"] != fields["host"]
            or response["username"] != fields["account"]
            or not response["token"] or len(response["token"]) > 16384
            or any(ord(char) < 33 or ord(char) == 127 for char in response["token"])
        ):
            raise ValueError
        return response["token"]
    except (ValueError, TypeError, KeyError):
        raise RuntimeError("source-account-relay-unavailable") from None


def _roundtrip(transport: dict, wire: str, *, timeout: float, followup=None) -> dict:
    try:
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise ValueError
        if not isinstance(transport, dict):
            raise ValueError
        peer_uid = None
        namespace_identity = None
        if set(transport) == {"socket_descriptor", "owner_uid"}:
            descriptor_path = Path(transport["socket_descriptor"])
            owner = transport["owner_uid"]
            metadata = descriptor_path.stat()
            if (
                type(owner) is not int or owner < 1
                or metadata.st_uid != owner or metadata.st_mode & 0o022
                or not hasattr(socket, "SO_PEERCRED")
            ):
                raise ValueError
            descriptor = json.loads(_reference(descriptor_path, 16384), object_pairs_hook=_pairs)
            if (
                set(descriptor) != {
                    "schema", "version", "socket", "device", "inode", "uid",
                    "publication", "source_generation",
                }
                or descriptor["schema"] != "credential-relay.namespace-endpoint"
                or type(descriptor["version"]) is not int or descriptor["version"] != 1
                or type(descriptor["uid"]) is not int
                or descriptor["uid"] != owner
                or type(descriptor["device"]) is not int or descriptor["device"] < 0
                or type(descriptor["inode"]) is not int or descriptor["inode"] < 1
                or any(
                    not isinstance(descriptor[key], str) or len(descriptor[key]) != 32
                    or any(char not in "0123456789abcdef" for char in descriptor[key])
                    for key in ("publication", "source_generation")
                )
                or not isinstance(descriptor["socket"], str)
                or Path(descriptor["socket"]).name != descriptor["socket"]
                or not descriptor["socket"].startswith("account-")
                or not descriptor["socket"].endswith(".sock")
            ):
                raise ValueError
            address = descriptor_path.parent / descriptor["socket"]
            observed = address.stat()
            if (
                address.is_symlink() or not stat.S_ISSOCK(observed.st_mode) or observed.st_uid != owner
                or observed.st_dev != descriptor["device"] or observed.st_ino != descriptor["inode"]
            ):
                raise ValueError
            namespace_identity = (address, observed.st_dev, observed.st_ino)
            peer_uid = owner
            transport = {"socket": str(address)}
        if set(transport) == {"socket"}:
            address = transport["socket"]
            if not isinstance(address, str) or any(ord(char) < 32 for char in address):
                raise ValueError
            if not Path(address).is_absolute() or not hasattr(socket, "AF_UNIX"):
                raise ValueError
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        elif set(transport) == {"host", "port"}:
            host = ipaddress.ip_address(transport["host"])
            networks = (
                "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
            )
            if (
                not any(host in ipaddress.ip_network(net) for net in networks)
                or type(transport["port"]) is not int or not 0 < transport["port"] <= 65535
            ):
                raise ValueError
            address = (transport["host"], transport["port"])
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        else:
            raise ValueError
        deadline = time.monotonic() + timeout
        with connection:
            connection.settimeout(timeout)
            connection.connect(address)
            if peer_uid is not None:
                _, actual_uid, _ = struct.unpack(
                    "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
                )
                endpoint, device, inode = namespace_identity
                observed = endpoint.stat()
                if actual_uid != peer_uid or (observed.st_dev, observed.st_ino) != (device, inode):
                    raise ValueError
            def request(value):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError
                connection.settimeout(remaining)
                connection.sendall(value.encode("utf-8"))
                data = bytearray()
                while b"\n\n" not in data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or len(data) >= 32768:
                        raise ValueError
                    connection.settimeout(remaining)
                    part = connection.recv(min(4096, 32768 - len(data)))
                    if not part:
                        if not data:
                            return {}
                        raise ValueError
                    data.extend(part)
                response = {}
                for line in data.decode("utf-8").strip().splitlines():
                    key, separator, value = line.partition("=")
                    if not separator or key in response:
                        raise ValueError
                    response[key] = value
                return response

            response = request(wire)
            return request(followup(response)) if followup is not None else response
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RuntimeError("source-account-relay-unavailable") from None


def token_provider(path: Path, *, source: str, account: str, host: str | None = None):
    return lambda: acquire(path, source=source, account=account, host=host)
