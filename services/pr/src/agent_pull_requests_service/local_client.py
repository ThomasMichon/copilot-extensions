"""Supported cache-only Unix HTTP client; never starts a host or reads its store."""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys
import time
from typing import Any

PROTOCOL = "agent-pull-requests.repository-cache/v1"
MAX_BYTES = 1_048_576
COMMANDS = ("list", "query", "subscribe", "inspect", "events", "acknowledge", "cancel", "follow")


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise LocalClientError("duplicate local response member")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise LocalClientError("nonfinite local response number")


class LocalClientError(ValueError):
    """A bounded unavailable/unsupported response leaves original identities intact."""


class _Connection(http.client.HTTPConnection):
    def __init__(self, path: Path) -> None:
        super().__init__("localhost", timeout=30)
        self.path = path

    def connect(self) -> None:
        if sys.platform != "linux" or not self.path.is_absolute():
            raise LocalClientError("Linux absolute Unix socket required")
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(self.timeout)
        try:
            connection.connect(str(self.path))
            peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            if struct.unpack("3i", peer)[1] != os.getuid():
                raise LocalClientError("local server OS identity differs")
        except BaseException:
            connection.close()
            raise
        self.sock = connection


class LocalClient:
    def __init__(self, path: Path, namespace: str, target: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", target):
            raise LocalClientError("invalid configured target")
        self.path, self.namespace, self.target = path, namespace, target
        self.binding: dict[str, Any] | None = None

    def _http(self, method: str, operation: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
        connection = _Connection(self.path)
        try:
            body = json.dumps(value, allow_nan=False).encode() if value is not None else None
            path = "/v1/repositories" if operation == "list" else f"/v1/repositories/{self.target}/{operation}"
            connection.request(
                method, path, body=body,
                headers={"content-type": "application/json"} if body else {},
            )
            response = connection.getresponse()
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise LocalClientError("local response exceeds its budget")
            value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
            if not isinstance(value, dict):
                raise LocalClientError("invalid local response")
            if response.status != 200:
                raise LocalClientError(str(value.get("error", "local request unavailable")))
            if value.get("protocol") != PROTOCOL or value.get("namespace") != self.namespace:
                raise LocalClientError("local namespace/contract differs")
            return value
        except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
            raise LocalClientError("local service unavailable; retain original request/registration IDs") from None
        finally:
            connection.close()

    def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Select running support and retain namespace/backend binding through every request."""
        if self.binding is None:
            selected = self._http("GET", "capabilities")
            if selected.get("continuing") is not True or selected.get("storage") != "repository_cache_version:2" or (
                selected.get("forge_writes") is not False or selected.get("task_emissions") is not False
            ):
                raise LocalClientError("selected local continuing profile unsupported")
            self.binding = {
                name: selected[name] for name in ("protocol", "namespace", "repository_key")
            }
        value = self._http("POST", operation, {**self.binding, "request": payload})
        result = value.get("result")
        if value.get("repository_key") != self.binding["repository_key"] or not isinstance(result, dict):
            raise LocalClientError("local binding/result differs")
        return result


def add_commands(commands: Any) -> None:
    """Register strict supported client forms separately from host activation."""
    for name in COMMANDS:
        parser = commands.add_parser(name, allow_abbrev=False)
        parser.add_argument("--socket", type=Path, required=True)
        parser.add_argument("--namespace", required=True)
        parser.add_argument("--target", required=name != "list", default="listing")
        if name == "list":
            continue
        if name == "query":
            parser.add_argument("--number", type=int)
            parser.add_argument("--after", type=int, default=0)
            parser.add_argument("--limit", type=int, default=16)
        elif name == "subscribe":
            parser.add_argument("--request-id", required=True)
            parser.add_argument("--ttl", type=int, default=3600)
            parser.add_argument("--after", type=int)
        else:
            parser.add_argument("--registration-id", required=True)
            if name in ("events", "follow"):
                parser.add_argument("--limit", type=int, default=16)
                parser.add_argument("--after", type=int)
            if name == "acknowledge":
                parser.add_argument("--event-id", required=True)
            if name == "follow":
                parser.add_argument("--timeout", type=int, default=60)
                parser.add_argument("--interval", type=int, default=1)
                parser.add_argument("--ack", action="store_true")


def run(args: argparse.Namespace) -> int:
    """Run one supported client action, or a finite continuing read loop."""
    try:
        client = LocalClient(args.socket, args.namespace, args.target)
        payload: dict[str, Any]
        if args.command == "list":
            print(json.dumps(client._http("GET", "list"), allow_nan=False))
            return 0
        if args.command == "query":
            payload = {"after": args.after, "limit": args.limit}
            if args.number is not None:
                payload["number"] = args.number
        elif args.command == "subscribe":
            payload = {"request_id": args.request_id, "ttl": args.ttl, "after": args.after}
        else:
            payload = {"registration_id": args.registration_id}
            if args.command in ("events", "follow"):
                payload["limit"] = args.limit
                if args.after is not None:
                    payload["after"] = args.after
            if args.command == "acknowledge":
                payload["event_id"] = args.event_id
        if args.command != "follow":
            print(json.dumps(client.request(args.command, payload), allow_nan=False))
            return 0
        if not 1 <= args.timeout <= 3600 or not 1 <= args.interval <= 300:
            raise LocalClientError("finite follow budgets required")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            result = client.request("events", payload)
            print(json.dumps(result, allow_nan=False), flush=True)
            if result["events"]:
                last = result["events"][-1]
                if args.ack:
                    client.request("acknowledge", {
                        "registration_id": args.registration_id, "event_id": last["event_id"],
                    })
                    payload.pop("after", None)
                else:
                    payload["after"] = last["sequence"]
            if result["status"] != "active":
                return 0
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
        return 0
    except (LocalClientError, ValueError, KeyError, BrokenPipeError):
        print("local repository request unavailable; original identities retained", file=sys.stderr)
        return 2
