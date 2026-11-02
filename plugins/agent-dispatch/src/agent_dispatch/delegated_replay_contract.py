"""Nonsecret, immutable routing and original-request intent for optional replay."""

from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit

SCOPED_PROTOCOL = "agent-pull-requests.watch/v2"
_ROUTING_FIELDS = {"gateway", "route_prefix", "target", "repo", "caller_scope"}
_SERVICE_FIELDS = {
    "fleet", "gateway", "slot", "namespace", "principal", "target", "consumer", "account", "repo",
}


def _validate_service_binding(binding: dict) -> None:
    service = binding["service_binding"]
    if (binding["protocol"] != SCOPED_PROTOCOL or not isinstance(service, dict)
            or set(service) != _SERVICE_FIELDS
            or any(not isinstance(v, str) or not v or len(v) > 256
                   or any(ord(char) < 32 for char in v) for v in service.values())
            or any(service[name] != binding[name] for name in ("target", "repo"))):
        raise ValueError("invalid scoped replay binding")


def validate_replay(value: object, *, repo: str, timeout: float) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "binding", "request_id", "issued_at", "deadline_at",
    }:
        raise ValueError("invalid replay intent")
    binding = value["binding"]
    if not isinstance(binding, dict) or set(binding) not in (
        _ROUTING_FIELDS, _ROUTING_FIELDS | {"protocol", "service_binding"},
    ):
        raise ValueError("invalid replay binding")
    if "protocol" in binding:
        _validate_service_binding(binding)
    if any(not isinstance(binding[k], str) or not binding[k] or len(binding[k]) > 512
           for k in _ROUTING_FIELDS):
        raise ValueError("invalid replay binding")
    gateway = urlsplit(binding["gateway"])
    if (
        gateway.scheme != "https" or not gateway.hostname or gateway.username
        or gateway.password or gateway.path or gateway.query or gateway.fragment
        or gateway.netloc != gateway.netloc.lower()
        or not re.fullmatch(r"(?:/[a-zA-Z0-9_-]+)+", binding["route_prefix"])
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", binding["target"])
        or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,200}", binding["caller_scope"])
        or binding["repo"] != repo
    ):
        raise ValueError("invalid replay binding")
    if (
        not isinstance(value["request_id"], str)
        or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,128}", value["request_id"])
        or any(type(value[f]) not in (int, float) or not math.isfinite(value[f])
               or value[f] <= 0 for f in ("issued_at", "deadline_at"))
        or value["deadline_at"] != value["issued_at"] + timeout
    ):
        raise ValueError("invalid original replay request")
    return json.loads(json.dumps(value, allow_nan=False))


def replay_scope(binding: dict) -> str:
    return json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False)
