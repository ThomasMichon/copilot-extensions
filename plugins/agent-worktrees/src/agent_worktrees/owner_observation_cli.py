"""Passive selected owner-observation reads; never ensure a resident or runtime."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from . import config as cfg
from . import control_jsonl
from . import owner_observation_client as response
from . import owner_observation_contract as contract
from .owner_observation_transport import FEED_ACTION, HISTORY_ACTION, POINT_BYTES, READ_ACTION
from .worktrees_client import _control_client

COMMANDS = frozenset({"worktree-observations", "worktree-observation-history"})
JsonObject = dict[str, Any]


def add_parsers(sub: argparse._SubParsersAction) -> None:
    for name in sorted(COMMANDS):
        parser = sub.add_parser(
            name,
            allow_abbrev=False,
            help="Read selected owner observations"
            if name == "worktree-observations"
            else "Read one bounded retained owner-observation page",
        )
        parser.add_argument("--json", action="store_true")
        parser.add_argument("--contract-version", type=int, choices=(contract.VERSION,), default=1)
        parser.add_argument("--worktree", required=True)
        parser.add_argument("--session", default=None)
        if name == "worktree-observations":
            parser.add_argument("--follow", action="store_true")
            parser.add_argument("--after-cursor", default=None)
        else:
            parser.add_argument("--source-generation", required=True)
            parser.add_argument("--before-cursor", default=None)
            parser.add_argument("--limit", type=int, default=contract.PAGE_ENTRIES)
        parser.set_defaults(func=cmd_observations)


def _emit(frame: JsonObject) -> None:
    sys.stdout.write(contract.encode(frame) + "\n")
    sys.stdout.flush()


def _diagnostic(message: str) -> str:
    valid = message.encode("utf-8", errors="backslashreplace").decode("utf-8")
    return "".join(
        char if ord(char) >= 32 and char != "\x7f" else f"\\u{ord(char):04x}"
        for char in valid[:1024]
    )


def _failure(code: str, message: str) -> int:
    frame = contract.error(code, _diagnostic(message))
    _emit(frame)
    sys.stderr.write(f"{code}: {frame['error']['message']}\n")
    return 1


def project_unavailable(argv: list[str], message: str) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    add_parsers(parser.add_subparsers(dest="command", required=True))
    parser.parse_args(argv)
    return _failure("project-unavailable", message)


def _payload(args: argparse.Namespace) -> JsonObject:
    value: JsonObject = {
        "contract_version": args.contract_version,
        "project": cfg.project_name(),
        "worktree_id": args.worktree,
        "expected_head_session_id": args.session,
    }
    if args.command == "worktree-observation-history":
        value.update(
            source_generation=args.source_generation,
            before_cursor=args.before_cursor,
            limit=args.limit,
        )
    else:
        value["after_cursor"] = args.after_cursor
    contract.request(value, history=args.command == "worktree-observation-history")
    return value


def _decode_point(raw: bytes) -> Any:
    return response.decode_json(raw, limit=POINT_BYTES)


def _client(history: bool) -> tuple[control_jsonl.Endpoint, str]:
    client = _control_client()
    health = control_jsonl.request(
        client,
        "health",
        {},
        control_jsonl.Codec(
            POINT_BYTES, contract.encode, _decode_point, "owner health"
        ),
    )
    if health.get("error") or health.get("ok") is False:
        raise contract.ObservationError("resident rejected observation readiness", "unavailable")
    capabilities = health.get("capabilities")
    required = contract.HISTORY_CAPABILITY if history else contract.FEED_CAPABILITY
    if capabilities is None:
        raise contract.ObservationError("resident declares no observation support", "unsupported")
    if not isinstance(capabilities, list) or any(
        not isinstance(item, str) for item in capabilities
    ):
        raise contract.ObservationError(
            "invalid resident capability declaration", "invalid-response"
        )
    if required not in capabilities:
        raise contract.ObservationError(f"resident lacks {required}", "unsupported")
    metadata, roster = health.get("worktree_observations"), health.get("worktrees")
    if not isinstance(metadata, dict) or not isinstance(roster, dict):
        raise contract.ObservationError(
            "resident omitted observation readiness", "invalid-response"
        )
    if (
        health.get("published") is not True
        or health.get("status") != "ready"
        or metadata.get("status") != "ready"
        or roster.get("status") != "ready"
    ):
        raise contract.ObservationError(
            "resident observation authority is unavailable", "unavailable"
        )
    try:
        generation = contract.generation(metadata.get("generation"))
    except contract.ObservationError as error:
        raise contract.ObservationError(
            "invalid resident observation generation", "invalid-response"
        ) from error
    if generation != roster.get("generation"):
        raise contract.ObservationError(
            "observation and roster owners disagree", "source-replaced"
        )
    return client, generation


def cmd_observations(args: argparse.Namespace) -> int:
    try:
        history = args.command == "worktree-observation-history"
        request = _payload(args)
        client, generation = _client(history)
        codec = control_jsonl.Codec(
            contract.FRAME_BYTES, contract.encode, response.decode_frame, "owner observations"
        )
        if not history and args.follow:
            return control_jsonl.relay(
                client,
                FEED_ACTION,
                request,
                codec,
                response.Receiver(request, generation),
                _emit,
            )
        frame = control_jsonl.request(
            client,
            HISTORY_ACTION if history else READ_ACTION,
            request,
            control_jsonl.Codec(
                POINT_BYTES, contract.encode, _decode_point, "owner observations"
            ),
        )
        response.validate(frame, request, generation, history=history)
        if not history and (
            frame["type"] not in {"snapshot", "error"}
            or frame["type"] == "snapshot"
            and frame["base_cursor"] != request["after_cursor"]
        ):
            raise contract.ObservationError("invalid owner point snapshot", "invalid-response")
        _emit(frame)
        if frame["type"] == "error":
            sys.stderr.write(
                f"{frame['error']['code']}: {_diagnostic(frame['error']['message'])}\n"
            )
            return 1
        return 0
    except (contract.ObservationError, OSError, ValueError, RuntimeError, RecursionError) as error:
        code = (
            error.code
            if isinstance(error, (contract.ObservationError, control_jsonl.StreamError))
            else "unavailable"
        )
        return _failure(code, str(error))
    except KeyboardInterrupt:
        return 130
