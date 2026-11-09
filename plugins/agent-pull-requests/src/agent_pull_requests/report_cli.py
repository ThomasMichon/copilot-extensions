"""Strict source embedding facade; no installed owner or ambient service loader."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .watch_replay_store import json_object


def add_parser(commands):
    """Register only explicit source-host report operations."""
    parser = commands.add_parser("report", allow_abbrev=False, help="Existing-owner source report tools.")
    subcommands = parser.add_subparsers(dest="report_command", required=True)
    for operation in ("capabilities", "threads", "diff"):
        child = subcommands.add_parser(operation, allow_abbrev=False)
        if operation != "capabilities":
            child.add_argument("--request", required=True, help="Bounded request JSON file or - for stdin.")
    return parser


def run(args, host=None):
    """Emit the complete validated exchange and preserve acquisition failure."""
    if host is None:
        print(json.dumps({"error": "report_owner_unavailable", "effects": "none"}))
        return 3
    from fleet_manager.gateway_service_channel import BODY_BYTES
    from .report_client import ReportClient
    from .report_read import parse_request
    from .watch_replay_client import ReplayClientError

    try:
        payload = {}
        if args.report_command != "capabilities":
            if args.request == "-":
                data = sys.stdin.buffer.read(BODY_BYTES + 1)
            else:
                with Path(args.request).open("rb") as stream:
                    data = stream.read(BODY_BYTES + 1)
            if len(data) > BODY_BYTES:
                raise ValueError("report request limit")
            payload = json_object(data)
        payload = parse_request(args.report_command, payload)
        if not isinstance(host, ReportClient):
            raise ReplayClientError("report_owner_unavailable")

        async def invoke():
            declaration = await host.capabilities()
            return declaration if args.report_command == "capabilities" else await host.read(
                args.report_command, payload,
            )

        value = asyncio.run(invoke())
        print(json.dumps(value, sort_keys=True, allow_nan=False))
        if args.report_command == "capabilities":
            return 0
        return int(bool(value["owner_exit"] or value["report"]["error"] or not value["report"]["supported"]))
    except ReplayClientError as error:
        print(json.dumps({"error": error.code, "effects": "none"}))
        return 4 if error.http_status == 403 else 3
    except (OSError, ValueError, RuntimeError, OverflowError, RecursionError):
        print(json.dumps({"error": "report_input_or_result_unavailable", "effects": "none"}))
        return 1


def main(argv=None, *, host=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    add_parser(parser.add_subparsers(dest="command", required=True))
    return run(parser.parse_args(argv), host)
