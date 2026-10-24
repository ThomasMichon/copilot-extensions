"""Explicit account-relay consumer; deliberately separate from scoped JWT/PoP."""

from __future__ import annotations

import json
import socket
import time
import ipaddress
from pathlib import Path

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


def acquire(path: Path, *, source: str, account: str, timeout: float = 20) -> str:
    """Reread explicit policy/capability and reacquire on EVERY invocation.

    Only a private local TCP endpoint or an absolute Unix socket is supported.
    No URL, redirect, ambient login, reusable account-token cache or HTTP proxy.
    """
    if type(timeout) not in (int, float) or not 0 < timeout <= 30:
        raise ValueError("account-relay-policy-invalid")
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
            for value in binding.values():
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
        capability = _reference(binding["capability_file"], 4096).decode("ascii").strip()
        if len(capability) < 32:
            raise ValueError
        _text(capability)
        fields = {key: value for key, value in binding.items() if key != "capability_file"}
        return exchange(policy["transport"], fields, capability, timeout=timeout)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RuntimeError("source-account-relay-unavailable") from None


def exchange(transport: dict, fields: dict, capability: str, *, timeout: float = 20) -> str:
    """Fixed account operation for an embedding's already-admitted caller.

    This does not authenticate an HTTP caller; the caller must possess the
    exact source/account/recipient capability checked by the source custodian.
    No arbitrary operation, URL, redirect or credential cache is supported.
    """
    try:
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise ValueError
        if set(fields) != {"source", "account", "recipient", "host", "action"}:
            raise ValueError
        for value in fields.values():
            _text(value)
        if fields["action"] != "get-github-token" or not 32 <= len(capability) <= 4096:
            raise ValueError
        _text(capability)
        if not isinstance(transport, dict):
            raise ValueError
        if set(transport) == {"socket"}:
            address = _text(transport["socket"])
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
        request = dict(fields, auth=capability)
        wire = "get-account-token\n" + "\n".join(f"{k}={v}" for k, v in request.items()) + "\n\n"
        deadline = time.monotonic() + timeout
        with connection:
            connection.settimeout(timeout)
            connection.connect(address)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError
            connection.settimeout(remaining)
            connection.sendall(wire.encode("utf-8"))
            data = bytearray()
            while b"\n\n" not in data:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or len(data) >= 32768:
                    raise ValueError
                connection.settimeout(remaining)
                part = connection.recv(min(4096, 32768 - len(data)))
                if not part:
                    raise ValueError
                data.extend(part)
        response = {}
        for line in data.decode("utf-8").strip().splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in response:
                raise ValueError
            response[key] = value
        if (
            set(response) != {"protocol", "host", "username", "token"}
            or response["protocol"] != "https" or response["host"] != fields["host"]
            or response["username"] != fields["account"]
            or not response["token"] or len(response["token"]) > 16384
            or any(ord(char) < 33 or ord(char) == 127 for char in response["token"])
        ):
            raise ValueError
        return response["token"]
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise RuntimeError("source-account-relay-unavailable") from None


def token_provider(path: Path, *, source: str, account: str):
    return lambda: acquire(path, source=source, account=account)
