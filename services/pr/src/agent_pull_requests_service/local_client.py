"""Supported cache-only Unix HTTP client; never starts a host or reads its store."""

from __future__ import annotations

import argparse
from collections.abc import Generator
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
COMMANDS = ("list", "query", "next-actions", "subscribe", "inspect", "events", "acknowledge", "cancel", "follow", "stream")


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


class LocalDeadlineExpired(LocalClientError):
    """The original finite polling window ended without advancing delivery."""


class _DeadlineSocket(socket.socket):
    deadline: float

    def __init__(self, deadline: float) -> None:
        if sys.platform != "linux":
            raise LocalClientError("Linux local transport required")
        super().__init__(socket.AF_UNIX)
        self.deadline = deadline

    def budget(self) -> None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("local transport deadline expired")
        self.settimeout(remaining)

    def recv_into(self, *args: Any, **kwargs: Any) -> int:
        self.budget()
        return super().recv_into(*args, **kwargs)

    def sendall(self, data: Any, flags: int = 0) -> None:
        self.budget()
        super().sendall(data, flags)


class _Connection(http.client.HTTPConnection):
    def __init__(self, path: Path, *, deadline: float | None = None, maximum: int = 30) -> None:
        super().__init__("localhost", timeout=30)
        self.path = path
        self.deadline = min(deadline, time.monotonic() + maximum) if deadline is not None else time.monotonic() + maximum

    def connect(self) -> None:
        if sys.platform != "linux" or not self.path.is_absolute():
            raise LocalClientError("Linux absolute Unix socket required")
        connection = _DeadlineSocket(self.deadline)
        try:
            connection.budget()
            connection.connect(str(self.path))
            peer = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
            if struct.unpack("iII", peer)[1] != os.getuid():
                raise LocalClientError("local server OS identity differs")
        except BaseException:
            connection.close()
            raise
        self.sock = connection


class LocalClient:
    protocol = PROTOCOL
    route = "repositories"
    storage = "repository_cache_version:2"

    def __init__(self, path: Path, namespace: str, target: str) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", target):
            raise LocalClientError("invalid configured target")
        self.path, self.namespace, self.target = path, namespace, target
        self.binding: dict[str, Any] | None = None
        self.deadline: float | None = None
        self.reviewer_actions = False
        self.streaming = False

    def _http(self, method: str, operation: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise LocalDeadlineExpired("finite polling window elapsed; original IDs retained")
        connection = _Connection(self.path, deadline=self.deadline)
        try:
            body = json.dumps(value, allow_nan=False).encode() if value is not None else None
            path = f"/v1/{self.route}" if operation == "list" else f"/v1/{self.route}/{self.target}/{operation}"
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
            if value.get("protocol") != self.protocol or value.get("namespace") != self.namespace:
                raise LocalClientError("local namespace/contract differs")
            return value
        except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise LocalDeadlineExpired("finite polling window elapsed; original IDs retained") from None
            raise LocalClientError("local service unavailable; retain original request/registration IDs") from None
        finally:
            connection.close()

    def _select(self) -> None:
        if self.binding is None:
            selected = self._http("GET", "capabilities")
            if selected.get("continuing") is not True or selected.get("storage") != self.storage or (
                selected.get("forge_writes") is not False or selected.get("task_emissions") is not False
            ):
                raise LocalClientError("selected local continuing profile unsupported")
            self.binding = {
                name: selected[name] for name in ("protocol", "namespace", "repository_key")
            }
            feature = selected.get("reviewer_actions")
            self.reviewer_actions = isinstance(feature, dict) and (
                feature.get("protocol") == "agent-pull-requests.reviewer-actions/v1"
                and feature.get("storage") == "repository_activity_version:1"
                and feature.get("effects") == "none"
                and feature.get("page_cursor") == "projection-sha256"
            )
            feature = selected.get("stream")
            self.streaming = isinstance(feature, dict) and (
                feature.get("protocol") == "agent-pull-requests.repository-stream/v1"
                and feature.get("transport") == "local-sse"
                and feature.get("ack_on_write") is False
                and feature.get("resume") == "registration-scoped-current-views"
            )

    def request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Select running support and retain namespace/backend binding through every request."""
        self._select()
        assert self.binding is not None
        if operation == "next-actions" and not self.reviewer_actions:
            raise LocalClientError("selected local reviewer-action profile unsupported")
        value = self._http("POST", operation, {**self.binding, "request": payload})
        result = value.get("result")
        if value.get("repository_key") != self.binding["repository_key"] or not isinstance(result, dict):
            raise LocalClientError("local binding/result differs")
        return result

    def stream(
        self, payload: dict[str, Any], *, last_event_id: str | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Finite SSE read, never automatic ACK, resubscription or provider access."""
        seconds = payload.get("seconds", 60)
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise LocalClientError("finite stream window required")
        previous = self.deadline
        self.deadline = min(previous, time.monotonic() + seconds) if previous is not None else time.monotonic() + seconds
        connection: _Connection | None = None
        try:
            self._select()
            assert self.binding is not None
            if not self.streaming or not self.reviewer_actions:
                raise LocalClientError("selected local stream profile unsupported")
            connection = _Connection(self.path, deadline=self.deadline, maximum=seconds)
            headers = {"content-type": "application/json", "accept": "text/event-stream"}
            if last_event_id is not None:
                if not re.fullmatch(r"v1\.[a-f0-9]{64}\.[0-9]{1,16}", last_event_id):
                    raise LocalClientError("invalid original stream cursor")
                headers["last-event-id"] = last_event_id
            connection.request(
                "POST", f"/v1/{self.route}/{self.target}/stream",
                body=json.dumps({**self.binding, "request": payload}, allow_nan=False).encode(),
                headers=headers,
            )
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise LocalClientError("local response exceeds its budget")
                failure = json.loads(raw, object_pairs_hook=_unique)
                if not isinstance(failure, dict):
                    raise LocalClientError("invalid local stream refusal")
                raise LocalClientError(str(failure.get("error", "local stream unavailable")))
            if response.getheader("content-type") != "text/event-stream":
                raise LocalClientError("local stream transport differs")
            fields: dict[str, str] = {}
            used = frames = size = 0
            while True:
                line = response.readline(MAX_BYTES + 1)
                if not line:
                    raise LocalClientError("local stream closed; retain original registration/cursor")
                used, size = used + len(line), size + len(line)
                if used > 16 * MAX_BYTES or size > MAX_BYTES:
                    raise LocalClientError("local stream exceeds its byte budget")
                if line.startswith(b":"):
                    continue
                if line == b"\n":
                    size = 0
                    if not fields:
                        continue
                    frames += 1
                    if frames > 256 or set(fields) - {"id", "event", "data"} or not {"event", "data"} <= fields.keys():
                        raise LocalClientError("invalid local stream frame")
                    data = json.loads(fields["data"], object_pairs_hook=_unique, parse_constant=_invalid_constant)
                    if fields["event"] == "end":
                        return
                    if fields["event"] == "error" and isinstance(data, dict):
                        raise LocalClientError(str(data.get("error", "local stream unavailable")))
                    if fields["event"] not in ("status", "pulls", "next-actions") or (
                        not isinstance(data, dict) or self.binding is None
                        or data.get("namespace") != self.namespace
                        or data.get("repository_key") != self.binding["repository_key"]
                        or data.get("registration_id") != payload.get("registration_id")
                        or data.get("reviewer") != payload.get("reviewer")
                        or data.get("protocol") != "agent-pull-requests.repository-stream/v1"
                        or re.fullmatch(r"v1\.[a-f0-9]{64}\.[0-9]{1,16}", fields.get("id", "")) is None
                    ):
                        raise LocalClientError("local stream binding differs")
                    yield {"event": fields["event"], "id": fields["id"], "data": data}
                    fields = {}
                    continue
                name, separator, value = line.decode("utf-8").rstrip("\n").partition(": ")
                if not separator or name in fields:
                    raise LocalClientError("invalid local stream field")
                fields[name] = value
        except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError):
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise LocalDeadlineExpired("finite stream window elapsed; original IDs retained") from None
            raise LocalClientError("local stream unavailable; retain original registration/cursor") from None
        finally:
            if connection is not None:
                connection.close()
            self.deadline = previous


def add_commands(commands: Any) -> None:
    """Register strict supported client forms separately from host activation."""
    for name in COMMANDS:
        parser = commands.add_parser(name, allow_abbrev=False)
        parser.add_argument("--socket", type=Path, required=True)
        parser.add_argument("--namespace", required=True)
        parser.add_argument("--target", required=name != "list", default="listing")
        if name == "list":
            continue
        if name in ("query", "next-actions"):
            if name == "next-actions":
                parser.add_argument("--reviewer", required=True)
                parser.add_argument("--max-age-seconds", type=int, default=1800)
                parser.add_argument("--cursor")
            else:
                parser.add_argument("--number", type=int)
            parser.add_argument("--after", type=int, default=0)
            parser.add_argument("--limit", type=int, default=16)
        elif name == "subscribe":
            parser.add_argument("--request-id", required=True)
            parser.add_argument("--ttl", type=int, default=3600)
            parser.add_argument("--after", type=int)
        else:
            parser.add_argument("--registration-id", required=True)
            if name in ("events", "follow", "stream"):
                parser.add_argument("--limit", type=int, default=16)
                if name != "stream":
                    parser.add_argument("--after", type=int)
            if name == "acknowledge":
                parser.add_argument("--event-id", required=True)
            if name == "follow":
                parser.add_argument("--timeout", type=int, default=60)
                parser.add_argument("--interval", type=int, default=1)
                parser.add_argument("--ack", action="store_true")
            if name == "stream":
                parser.add_argument("--reviewer", required=True)
                parser.add_argument("--max-age-seconds", type=int, default=1800)
                parser.add_argument("--last-event-id")
                parser.add_argument("--timeout", type=int, default=60)
                parser.add_argument("--interval", type=int, default=1)


def run(args: argparse.Namespace) -> int:
    """Run one supported client action, or a finite continuing read loop."""
    try:
        client = LocalClient(args.socket, args.namespace, args.target)
        payload: dict[str, Any]
        if args.command == "list":
            print(json.dumps(client._http("GET", "list"), allow_nan=False))
            return 0
        if args.command == "stream":
            for frame in client.stream({
                "registration_id": args.registration_id, "reviewer": args.reviewer,
                "max_age_seconds": args.max_age_seconds, "limit": args.limit,
                "seconds": args.timeout, "interval": args.interval,
            }, last_event_id=args.last_event_id):
                print(json.dumps(frame, allow_nan=False), flush=True)
            return 0
        if args.command in ("query", "next-actions"):
            payload = {"after": args.after, "limit": args.limit}
            if args.command == "next-actions":
                payload.update(reviewer=args.reviewer, max_age_seconds=args.max_age_seconds)
                if args.cursor is not None:
                    payload["cursor"] = args.cursor
            elif args.number is not None:
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
        client.deadline = deadline
        while time.monotonic() < deadline:
            result = client.request("events", payload)
            print(json.dumps(result, allow_nan=False), flush=True)
            if result["events"]:
                last = result["events"][-1]
                if args.ack:
                    if time.monotonic() >= deadline:
                        return 0
                    client.request("acknowledge", {
                        "registration_id": args.registration_id, "event_id": last["event_id"],
                    })
                    payload.pop("after", None)
                else:
                    payload["after"] = last["sequence"]
            if result["status"] != "active" and not result["more"]:
                return 0
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
        return 0
    except LocalDeadlineExpired:
        return 0 if args.command in ("follow", "stream") else 2
    except LocalClientError as error:
        print(f"local repository request refused: {error}", file=sys.stderr)
        return 2
    except (ValueError, KeyError, BrokenPipeError):
        print("local repository request unavailable; original identities retained", file=sys.stderr)
        return 2
