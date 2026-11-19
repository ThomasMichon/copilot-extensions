"""Strict retained feed intent; no transport, consumer or completion is selected."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, cast
from urllib.parse import urlsplit

from .delegated_waiter_contract import positive_timeout

STORAGE_PROTOCOL = "agent-dispatch.repository-feed-waiter/v1"
CACHE_PROTOCOL = "agent-pull-requests.repository-cache/v1"
_IDENTITY = {"namespace", "provider", "endpoint", "account", "repo"}
_BINDING = {"protocol", "socket", "identity", "target", "consumer", "reviewer"}
_ORIGINAL = {"binding", "request_id", "issued_at", "deadline_at", "baseline"}


@dataclass(frozen=True)
class FeedWaiterPage:
    """A bounded native candidate window, including progress past nonmatches."""

    waiters: tuple[dict[str, Any], ...]
    next_after: int
    scanned: int


def feed_service_scope(binding: dict[str, Any]) -> str:
    """Reserve consumer/request identity across bindings in the same namespace."""
    return json.dumps({
        "protocol": STORAGE_PROTOCOL, "namespace": binding["identity"]["namespace"],
        "consumer": binding["consumer"],
    }, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: object, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _validate_binding(binding: object, repo: str) -> None:
    if not isinstance(binding, dict) or set(binding) != _BINDING:
        raise ValueError("invalid repository-feed binding")
    identity = binding["identity"]
    path = binding["socket"]
    if (
        binding["protocol"] != CACHE_PROTOCOL
        or not isinstance(identity, dict) or set(identity) != _IDENTITY
        or any(not isinstance(v, str) or not v or len(v) > 512
               or any(ord(char) < 32 or ord(char) == 127 for char in v)
               for v in identity.values())
        or identity["provider"] not in ("github", "gitea", "azure-devops")
        or identity["repo"] != repo
        or any(not _text(identity[field], r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
               for field in ("namespace", "account"))
        or not isinstance(path, str) or not path.startswith("/") or len(path) > 4096
        or str(PurePosixPath(path)) != path or ".." in PurePosixPath(path).parts
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or not _text(binding["target"], r"[a-z0-9][a-z0-9-]{0,63}")
        or not _text(binding["consumer"], r"[A-Za-z0-9:_-]{1,200}")
        or not _text(binding["reviewer"], r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}(?:\[bot\])?")
        or len(binding["reviewer"]) > 128
    ):
        raise ValueError("invalid repository-feed binding")
    endpoint = urlsplit(identity["endpoint"])
    if (
        endpoint.scheme != "https" or not endpoint.hostname
        or endpoint.username is not None or endpoint.password is not None
        or endpoint.query or endpoint.fragment or identity["endpoint"].endswith("/")
        or any(part in (".", "..") for part in endpoint.path.split("/"))
    ):
        raise ValueError("invalid repository-feed forge endpoint")
    endpoint.port


def validate_feed_binding(binding: object, *, repo: str) -> dict[str, Any]:
    """Detach trusted local selection before asynchronous preparation."""
    _validate_binding(binding, repo)
    return cast(dict[str, Any], json.loads(json.dumps(binding, allow_nan=False)))


def validate_feed_delegation(value: object, *, armed: bool = False) -> dict[str, Any]:
    """Validate detached storage intent without importing optional PR packages."""
    fields = {
        "protocol", "coordinator_id", "preparation_id", "subscriber_id",
        "repo", "number", "timeout", "feed",
    }
    if not isinstance(value, dict) or set(value) != fields | (
        {"registration_id"} if armed else set()
    ) or value["protocol"] != STORAGE_PROTOCOL:
        raise ValueError("invalid repository-feed waiter identity")
    if (
        any(not _text(value[field], r"[A-Za-z0-9:_-]{1,200}")
            for field in ("coordinator_id", "preparation_id", "subscriber_id"))
        or not _text(value["repo"], r"[\w.-]+/[\w.-]+")
        or type(value["number"]) is not int or not 0 < value["number"] < 2**31
        or armed and (
            not isinstance(value["registration_id"], str)
            or not value["registration_id"] or len(value["registration_id"]) > 200
        )
    ):
        raise ValueError("invalid repository-feed waiter identity")
    timeout = positive_timeout(value["timeout"])
    feed = value["feed"]
    if not isinstance(feed, dict) or set(feed) != _ORIGINAL:
        raise ValueError("invalid original repository-feed request")
    _validate_binding(feed["binding"], value["repo"])
    if (
        not _text(feed["request_id"], r"[A-Za-z0-9:_-]{1,128}")
        or any(type(feed[field]) not in (int, float) or not math.isfinite(feed[field])
               or feed[field] <= 0 for field in ("issued_at", "deadline_at"))
        or feed["deadline_at"] != feed["issued_at"] + timeout
    ):
        raise ValueError("invalid original repository-feed request")
    baseline = feed["baseline"]
    if baseline is not None and (
        not isinstance(baseline, dict) or set(baseline) != {"event_cursor", "action_key"}
        or type(baseline["event_cursor"]) is not int
        or not 0 <= baseline["event_cursor"] < 2**63
        or baseline["action_key"] is not None
        and not _text(baseline["action_key"], r"[a-f0-9]{64}")
    ):
        raise ValueError("invalid repository-feed activity baseline")
    return cast(dict[str, Any], json.loads(json.dumps(value, allow_nan=False)))
