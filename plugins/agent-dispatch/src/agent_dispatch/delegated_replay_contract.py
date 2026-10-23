"""Nonsecret, immutable routing and original-request intent for optional replay."""

from __future__ import annotations

import json
import math
import re
from urllib.parse import urlsplit


def validate_replay(value: object, *, repo: str, timeout: float) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "binding", "request_id", "issued_at", "deadline_at",
    }:
        raise ValueError("invalid replay intent")
    binding = value["binding"]
    if not isinstance(binding, dict) or set(binding) != {
        "gateway", "route_prefix", "target", "repo", "caller_scope",
    }:
        raise ValueError("invalid replay binding")
    if any(not isinstance(v, str) or not v or len(v) > 512 for v in binding.values()):
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
