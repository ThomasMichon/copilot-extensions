"""Strict existing-owner workspace frontdoor; no owner loader or bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from .review_workspace import MAX_GENERATION_BYTES
from .watch_replay_store import json_object
from .workspace_tools import COMMANDS, WorkspaceTools, WorkspaceToolUnavailable


def add_parser(commands):
    workspace = commands.add_parser("workspace", allow_abbrev=False,
                                    help="Workspace source tools; an actual owner binding is required.")
    subcommands = workspace.add_subparsers(dest="workspace_command", required=True)
    for command in COMMANDS:
        parser = subcommands.add_parser(command, allow_abbrev=False)
        if command != "capabilities":
            parser.add_argument("--request", required=True, help="Bounded request JSON file or - for stdin; not an owner grant.")
    return workspace


def run(args, host=None):
    try:
        request = {}
        if args.workspace_command != "capabilities":
            if args.request == "-":
                data = sys.stdin.buffer.read(MAX_GENERATION_BYTES + 1)
            else:
                with Path(args.request).open("rb") as stream:
                    data = stream.read(MAX_GENERATION_BYTES + 1)
            if len(data) > MAX_GENERATION_BYTES:
                raise ValueError("workspace request limit")
            request = json_object(data)
        host = WorkspaceTools() if host is None else host
        if not isinstance(host, WorkspaceTools):
            raise WorkspaceToolUnavailable("workspace_owner_unavailable")
        result = asyncio.run(host.invoke(args.workspace_command, request))
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except PermissionError:
        print(json.dumps({"error": "workspace_owner_refused", "effect_completion": "not_inferred"}))
        return 4
    except WorkspaceToolUnavailable:
        print(json.dumps({"error": "workspace_owner_or_operation_unavailable", "effect_completion": "not_inferred"}))
        return 3
    except (OSError, ValueError, RuntimeError, OverflowError):
        print(json.dumps({"error": "workspace_input_or_result_unavailable", "effect_completion": "not_inferred"}))
        return 1


def main(argv=None, *, host=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser(commands)
    return run(parser.parse_args(argv), host)


if __name__ == "__main__":
    raise SystemExit(main())
