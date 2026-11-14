"""Explicit filtered client: no broader repository-profile or process-wait fallback."""

import json
import argparse
from pathlib import Path
import sys
from typing import Any

from .local_client import LocalClient, LocalClientError

OPERATIONS = ("list", "capabilities", "subscribe", "query", "inspect", "renew", "events", "acknowledge", "cancel")
COMMANDS = tuple(f"population-{operation}" for operation in OPERATIONS)


class PopulationClient(LocalClient):
    protocol = "agent-pull-requests.filtered-population/v1"
    route = "populations"
    storage = "repository_cache_version:3"


def add_commands(commands: Any) -> None:
    for operation in OPERATIONS:
        parser = commands.add_parser(f"population-{operation}", allow_abbrev=False)
        parser.add_argument("--socket", type=Path, required=True)
        parser.add_argument("--namespace", required=True)
        parser.add_argument("--target", required=operation != "list", default="listing")
        if operation in ("capabilities", "list"):
            continue
        if operation == "subscribe":
            parser.add_argument("--request-id", required=True)
            parser.add_argument("--ttl", type=int, default=3600)
            selection = parser.add_mutually_exclusive_group(required=True)
            selection.add_argument("--filter-file", type=Path)
            selection.add_argument("--definition")
        else:
            parser.add_argument("--registration-id", required=True)
            if operation in ("events", "query"):
                parser.add_argument("--after", type=int)
                parser.add_argument("--limit", type=int, default=16)
            elif operation == "renew":
                parser.add_argument("--expires-at", type=int, required=True)
            elif operation == "acknowledge":
                parser.add_argument("--event-id", required=True)


def run(args: argparse.Namespace) -> int:
    try:
        operation = args.command.removeprefix("population-")
        client = PopulationClient(args.socket, args.namespace, args.target)
        if operation in ("list", "capabilities"):
            print(json.dumps(client._http("GET", operation), allow_nan=False))
            return 0
        if operation == "subscribe":
            payload: dict[str, Any] = {"request_id": args.request_id, "ttl": args.ttl}
            if args.definition is not None:
                payload["definition"] = args.definition
            else:
                from pr_filters import Expression
                if not args.filter_file.is_absolute():
                    raise ValueError("absolute filter file required")
                with args.filter_file.open("rb") as stream:
                    payload["filter"] = Expression.loads(stream.read(32769)).document()
        else:
            payload = {"registration_id": args.registration_id}
            if operation in ("query", "events"):
                payload["limit"] = args.limit
                if args.after is not None:
                    payload["after"] = args.after
            elif operation == "renew":
                payload["expires_at"] = args.expires_at
            elif operation == "acknowledge":
                payload["event_id"] = args.event_id
        print(json.dumps(client.request(operation, payload), allow_nan=False))
        return 0
    except (LocalClientError, ValueError, OSError, KeyError) as error:
        print(f"filtered population request refused: {error}", file=sys.stderr)
        return 2
