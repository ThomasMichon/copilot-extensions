"""Strict optional CLI consumer; its current authenticated caller is supplied."""

from __future__ import annotations

import asyncio
import argparse
import json
from pathlib import Path
import sys

from .attention_client import AttentionClient
from .attention_read import MAX_BATCH_BYTES, PROTOCOL
from .watch_replay_client import ReplayClientError
from .watch_replay_store import json_object


def add_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("attention", allow_abbrev=False,
                                help="Optional retained attention; no owner enrollment or effects.")
    subcommands = parser.add_subparsers(dest="attention_command", required=True)
    subcommands.add_parser("capabilities", allow_abbrev=False)
    read = subcommands.add_parser("read", allow_abbrev=False)
    read.add_argument("--request", required=True, help="Bounded {limit,after} JSON file or - for stdin.")


def run(args: argparse.Namespace, host: AttentionClient | None = None) -> int:
    async def invoke(payload: dict) -> dict:
        if host is None:
            if args.attention_command == "capabilities":
                return {"protocol": PROTOCOL, "operations": ["capabilities"],
                        "owner_binding": "unavailable", "effects": "none"}
            raise ReplayClientError("attention_owner_unavailable")
        if not isinstance(host, AttentionClient):
            raise ValueError("existing authenticated attention client required")
        capabilities = await host.capabilities()
        if args.attention_command == "capabilities":
            return capabilities
        return await host.read(**payload)

    try:
        payload = {}
        if args.attention_command == "read":
            if args.request == "-":
                raw = sys.stdin.buffer.read(MAX_BATCH_BYTES + 1)
            else:
                with Path(args.request).open("rb") as stream:
                    raw = stream.read(MAX_BATCH_BYTES + 1)
            if len(raw) > MAX_BATCH_BYTES:
                raise ValueError("attention request limit")
            payload = json_object(raw)
            if set(payload) != {"limit", "after"}:
                raise ValueError("invalid attention request fields")
        print(json.dumps(asyncio.run(invoke(payload)), sort_keys=True, allow_nan=False))
        return 0
    except ReplayClientError as error:
        if error.http_status == 403:
            print(json.dumps({"error": "attention_owner_refused", "effects": "none"}))
            return 4
        print(json.dumps({"error": error.code, "effects": "none"}))
        return 1 if error.code == "invalid_attention_response" else 3
    except PermissionError:
        print(json.dumps({"error": "attention_owner_refused", "effects": "none"}))
        return 4
    except (OSError, ValueError, TypeError, RuntimeError):
        print(json.dumps({"error": "attention_input_or_result_unavailable", "effects": "none"}))
        return 1
