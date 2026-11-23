"""``vault copy`` subcommand."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .mutation_client import ordinary_mutation


@ordinary_mutation
def cmd_copy(args: argparse.Namespace) -> int:
    from . import cli

    if not cli._ensure_unlocked_service():
        return 1

    request: dict[str, Any] = {"action": "copy", "entry": args.entry, "dest": args.dest}
    if args.skip_notes:
        request["skip_notes"] = True
    if args.skip_url:
        request["skip_url"] = True
    resp = cli.send_command(request, timeout=60.0)
    if resp and resp.get("ok"):
        message = resp.get("message", "Entry copied")
        if resp.get("dest"):
            message = f"{message}: {resp.get('source', args.entry)} -> {resp['dest']}"
        print(message)
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    if "Unknown action" in error:
        error += " (the running vault service predates copy; restart it to update)"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def add_copy_parser(sub: Any) -> None:
    p = sub.add_parser("copy", aliases=["cp"],
                       help="Copy an entry to another group or entry path")
    p.add_argument("entry", help="Entry path")
    p.add_argument("dest", help=(
        "Destination group (existing group, or a path ending in '/') or full "
        "destination entry path. A bare name lands in the configured group; "
        "any path containing '/' may address any group"))
    p.add_argument("--skip-notes", action="store_true",
                   help="Copy an entry that has notes, leaving the notes behind")
    p.add_argument("--skip-url", action="store_true",
                   help="Copy an entry that has a URL, leaving the URL behind")
    p.set_defaults(func=cmd_copy)
