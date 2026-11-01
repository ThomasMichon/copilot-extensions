"""Owned agenda process boundary, with no provisioning or planning imports."""

from __future__ import annotations

import argparse
import socket
import sys
import time

from . import agenda_contract as contract
from . import config as cfg
from .worktrees_client import _control_client


def add_parsers(sub) -> None:
    for name in ("agenda-publish", "agenda-read", "agenda-feed"):
        parser = sub.add_parser(
            name,
            allow_abbrev=False,
            help={
                "agenda-publish": "Publish a committed agenda observation from bounded JSON stdin",
                "agenda-read": "Read the optional current-head agenda projection",
                "agenda-feed": "Hold an authenticated atomic agenda notification feed",
            }[name],
        )
        parser.add_argument("--json", action="store_true")
        if name != "agenda-publish":
            parser.add_argument("--worktree", required=True)
            parser.add_argument("--session", required=True)
        parser.set_defaults(func=cmd_agenda)


def _client():
    client = _control_client()
    health = client._request("health", max_response_bytes=contract.FRAME_BYTES, total_timeout=1.0)
    if not isinstance(health.get("capabilities"), list) or (
        contract.CAPABILITY not in health["capabilities"]
    ):
        raise contract.AgendaError("resident lacks worktree-agendas-v1", "unsupported")
    worktrees = health.get("worktrees")
    if not isinstance(worktrees, dict) or worktrees.get("status") != "ready":
        raise contract.AgendaError("resident agenda authority is not ready", "unavailable")
    generation = contract.hex_id(worktrees.get("generation"), "resident generation")
    return client, generation


def _emit(value) -> None:
    sys.stdout.write(contract.encode(value) + "\n")
    sys.stdout.flush()


def project_unavailable(argv, message: str) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    result = contract.failure("project-unavailable", message)
    _emit({"type": "error", **result} if args.command == "agenda-feed" else result)
    sys.stderr.write(f"project-unavailable: {message}\n")
    return 1


def _error(result) -> bool:
    if not isinstance(result, dict):
        raise contract.AgendaError("non-object agenda response", "invalid-response")
    if result.get("ok") is not False:
        return False
    fields = {"ok", "contract_version", "error"}
    contract.exact(result, fields)
    contract.exact(result["error"], {"code", "message"})
    if (
        type(result["contract_version"]) is not int
        or result["contract_version"] != 1
        or any(
            not isinstance(result["error"][key], str) or not result["error"][key]
            for key in ("code", "message")
        )
    ):
        raise contract.AgendaError("malformed agenda error", "invalid-response")
    return True


class Receiver:
    def __init__(self, request, generation):
        self.request, self.generation = request, generation
        self.previous = None
        self.closed = False

    def receive(self, frame) -> None:
        if not isinstance(frame, dict) or self.closed:
            raise contract.AgendaError("invalid or post-terminal agenda frame", "invalid-response")
        kind = frame.get("type")
        if not isinstance(kind, str):
            raise contract.AgendaError("agenda frame type must be text", "invalid-response")
        body = {key: value for key, value in frame.items() if key != "type"}
        if kind == "error":
            if not _error(body):
                raise contract.AgendaError("invalid agenda feed error", "invalid-response")
            self.closed = True
            return
        if kind in {"heartbeat", "going-away"}:
            contract.exact(
                body, {"generation", "sequence"} | ({"reason"} if kind == "going-away" else set())
            )
            if (
                self.previous is None
                or body["generation"] != self.generation
                or (
                    type(body["sequence"]) is not int
                    or body["sequence"] != self.previous["sequence"]
                )
            ):
                raise contract.AgendaError("agenda notice changed cursor", "invalid-response")
            if kind == "going-away":
                if not isinstance(body["reason"], str) or not body["reason"]:
                    raise contract.AgendaError("agenda notice omitted reason", "invalid-response")
                self.closed = True
            return
        if kind not in {"snapshot", "update"}:
            raise contract.AgendaError("unknown agenda frame", "invalid-response")
        contract.validate_reply(body, self.request, self.generation)
        if self.previous is None:
            if kind != "snapshot":
                raise contract.AgendaError(
                    "agenda feed did not start with snapshot", "invalid-response"
                )
        elif (
            kind != "update"
            or body["sequence"] <= self.previous["sequence"]
            or all(body[key] == self.previous[key] for key in ("availability", "observation"))
        ):
            raise contract.AgendaError("agenda feed repeated or regressed", "invalid-response")
        self.previous = body


def relay(client, request, generation) -> int:
    receiver = Receiver(request, generation)
    encoded = (
        contract.encode(
            {
                "version": 1,
                "token": client.token,
                "action": "agenda-feed",
                "payload": request,
            }
        ).encode("utf-8")
        + b"\n"
    )
    with socket.create_connection((client.host, client.port), timeout=client.timeout) as sock:
        sock.sendall(encoded)
        pending = bytearray()
        deadline = time.monotonic() + client.timeout
        while not receiver.closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise contract.AgendaError("agenda feed deadline expired", "unavailable")
            sock.settimeout(remaining)
            chunk = sock.recv(contract.FRAME_BYTES)
            if not chunk:
                raise contract.AgendaError(
                    "agenda feed disconnected without terminal frame", "disconnected"
                )
            pending.extend(chunk)
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                frame = contract.decode(line)
                receiver.receive(frame)
                _emit(frame)
                deadline = time.monotonic() + 5.0
                if receiver.closed:
                    return 1 if frame["type"] == "error" else 0
            if len(pending) >= contract.FRAME_BYTES:
                raise contract.AgendaError(
                    "agenda feed frame exceeds byte bound", "invalid-response"
                )
    return 0


def cmd_agenda(args) -> int:
    feed = args.command == "agenda-feed"
    try:
        if args.command == "agenda-publish":
            stdin = getattr(sys.stdin, "buffer", sys.stdin)
            raw = stdin.read(contract.BODY_BYTES + 1)
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            body = contract.decode(raw, contract.BODY_BYTES)
            contract.observation(body)
            request = {
                "contract_version": 1,
                "project": cfg.project_name(),
                "worktree_id": body["source"]["worktree"],
                "session_id": body["source"]["session"],
            }
        else:
            request = {
                "contract_version": 1,
                "project": cfg.project_name(),
                "worktree_id": args.worktree,
                "session_id": args.session,
            }
        contract.selection(request)
        client, generation = _client()
        if feed:
            return relay(client, request, generation)
        payload = (
            request
            if args.command == "agenda-read"
            else {
                "contract_version": 1,
                "project": request["project"],
                "expected_generation": generation,
                "observation": body,
            }
        )
        result = client._request(
            args.command, payload, max_response_bytes=contract.FRAME_BYTES, total_timeout=1.0
        )
        if _error(result):
            _emit(result)
            sys.stderr.write(f"{result['error']['code']}: {result['error']['message']}\n")
            return 1
        _emit(contract.validate_reply(result, request, generation))
        return 0
    except (contract.AgendaError, OSError, ValueError, RuntimeError, RecursionError) as exc:
        result = contract.failure(getattr(exc, "code", "unavailable"), str(exc))
        _emit({"type": "error", **result} if feed else result)
        sys.stderr.write(f"{result['error']['code']}: {result['error']['message']}\n")
        return 1
    except KeyboardInterrupt:
        return 130
