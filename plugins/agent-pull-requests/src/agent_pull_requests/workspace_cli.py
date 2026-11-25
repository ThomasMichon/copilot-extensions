"""Strict existing-owner workspace frontdoor; no owner loader or bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from collections.abc import Callable, Sequence
from typing import Any, Protocol, cast

from .review_workspace import MAX_GENERATION_BYTES
from .watch_replay_store import json_object
from .workspace_tools import COMMANDS, WorkspaceTools, WorkspaceToolUnavailable


class _ToolView(Protocol):
    def for_one_shot(self) -> _ToolView: ...

    async def capabilities(self, *, operation: Any = None) -> dict[str, Any]: ...

    async def invoke(self, command: str, request: dict[str, Any]) -> dict[str, Any]: ...


def add_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    workspace = commands.add_parser("workspace", allow_abbrev=False,
                                    help="Workspace source tools; an actual owner binding is required.")
    workspace.add_argument("--client-profile", metavar="NAME",
                           help="Already adopted read-only service-client profile; not a path or authority grant.")
    subcommands = workspace.add_subparsers(dest="workspace_command", required=True)
    for command in COMMANDS:
        parser = subcommands.add_parser(command, allow_abbrev=False)
        if command != "capabilities":
            parser.add_argument("--request", required=True, help="Bounded request JSON file or - for stdin; not an owner grant.")
    return workspace


def run(args: argparse.Namespace, host: object | None = None) -> int:
    if getattr(args, "client_profile", None) is not None:
        from .workspace_observer import run as observe
        return observe(args, host=host)
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
        create_tools: Callable[[], WorkspaceTools] = WorkspaceTools
        host = create_tools() if host is None else host
        if not isinstance(host, WorkspaceTools):
            raise WorkspaceToolUnavailable("workspace_owner_unavailable")
        view = cast(_ToolView, host).for_one_shot()
        result = asyncio.run(view.invoke(args.workspace_command, request))
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


def main(argv: Sequence[str] | None = None, *, host: object | None = None) -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser(commands)
    return run(parser.parse_args(argv), host)


if __name__ == "__main__":
    from ._process_entry import run as _process_entry
    raise SystemExit(_process_entry(main))
