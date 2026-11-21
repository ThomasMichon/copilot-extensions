"""Strict, non-starting optional pending-queue CLI."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import quote

from .client import BridgeClientError, BridgeConnectionError
from .pending_queue import CONTRACT, MAX_ID, SCHEMA, VERSION


def _bounded(value: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"expected {minimum}..{maximum}")
    return parsed


def execute(client: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Negotiate once with a pinned provider; never replay a failed mutation."""
    endpoint = client._base
    try:
        support = client._request(
            "GET",
            "/api/v1/live-sessions/pending-queue-contract",
            _endpoint=endpoint,
            allow_retry=False,
        )
    except BridgeClientError as exc:
        if exc.status in {404, 405, 426}:
            raise BridgeClientError(
                426, "Selected provider does not support pending-queue v1"
            ) from exc
        raise
    if not isinstance(support, dict) or any(
        type(support.get(key)) is not type(value) or support[key] != value
        for key, value in CONTRACT.items()
    ):
        raise BridgeClientError(426, "Selected provider does not support pending-queue v1")
    path = "/api/v1/live-sessions/" + quote(args.session_id, safe="") + "/pending-queue"
    selection = {
        "contract_version": VERSION,
        "worktree_id": args.worktree_id,
        "after": args.after,
        "limit": args.limit,
    }
    if args.reconcile:
        result = client._request(
            "POST",
            path + "/reconcile",
            {**selection, "consumer_incarnation": args.consumer_incarnation},
            _endpoint=endpoint,
            allow_retry=False,
        )
    else:
        result = client._request(
            "GET",
            path,
            params=selection,
            _endpoint=endpoint,
            allow_retry=False,
        )
    if (
        not isinstance(result, dict)
        or result.get("schema") != SCHEMA
        or type(result.get("version")) is not int
        or result["version"] != VERSION
        or not isinstance(result.get("target"), dict)
        or result["target"].get("session_id") != args.session_id
        or result["target"].get("worktree_id") != args.worktree_id
    ):
        raise BridgeClientError(
            502, "Pending queue response identity or contract changed; do not replay"
        )
    return result


def _cmd(args: argparse.Namespace) -> None:
    from . import __main__ as core

    if args.reconcile != bool(args.consumer_incarnation):
        raise SystemExit("--reconcile and --consumer-incarnation must be supplied together")
    try:
        result = execute(core._get_client(ensure=False), args)
    except (BridgeClientError, BridgeConnectionError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "version": VERSION,
                    "status": "unavailable",
                    "error": str(exc)
                    if isinstance(exc, BridgeClientError)
                    else "Bridge read failed",
                }
            )
        )
        raise SystemExit(3) from exc
    print(json.dumps(result))


def register_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register a body-free exact-session read with explicit offer preparation."""
    parser = sub.add_parser(
        "pending-queue",
        allow_abbrev=False,
        help="Inspect original backend inbox metadata; optionally prepare unstarted offers",
    )
    parser.add_argument("session_id", help="Exact native session ID on this Bridge host")
    parser.add_argument("--worktree-id", required=True)
    parser.add_argument("--limit", type=lambda v: _bounded(v, minimum=1, maximum=100), default=50)
    parser.add_argument(
        "--after", type=lambda v: _bounded(v, minimum=0, maximum=MAX_ID), default=0
    )
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--consumer-incarnation")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.set_defaults(func=_cmd)
