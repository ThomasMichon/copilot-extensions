"""``vault move`` / ``vault copy`` subcommands."""

from __future__ import annotations

import sys

_DEST_HELP = (
    "Destination group (existing group, or a path ending in '/') or full "
    "destination entry path (rename). A bare name lands in the configured "
    "group; any path containing '/' may address any group")


def _relocate(args, action: str, default_message: str, timeout: float) -> int:
    from . import cli

    if not cli._ensure_unlocked_service():
        return 1

    request = {"action": action, "entry": args.entry, "dest": args.dest}
    if args.force:
        request["overwrite"] = True
    if getattr(args, "skip_notes", False):
        request["skip_notes"] = True
    resp = cli.send_command(request, timeout=timeout)
    if resp and resp.get("ok"):
        message = resp.get("message", default_message)
        if resp.get("dest"):
            message = f"{message}: {resp.get('source', args.entry)} -> {resp['dest']}"
        print(message)
        return 0
    error = resp.get("error", "unknown") if resp else "service unreachable"
    print(f"Error: {error}", file=sys.stderr)
    return 1


def cmd_move(args):
    return _relocate(args, "move", "Entry moved", timeout=30.0)


def cmd_copy(args):
    return _relocate(args, "copy", "Entry copied", timeout=60.0)


def add_relocate_parsers(sub) -> None:
    p = sub.add_parser("move", aliases=["mv"],
                       help="Move or rename an entry (any group)")
    p.add_argument("entry", help="Entry path")
    p.add_argument("dest", help=_DEST_HELP)
    p.add_argument("-f", "--force", action="store_true",
                   help="Replace an existing destination entry")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("copy", aliases=["cp"],
                       help="Copy an entry to another group or entry path")
    p.add_argument("entry", help="Entry path")
    p.add_argument("dest", help=_DEST_HELP)
    p.add_argument("-f", "--force", action="store_true",
                   help="Replace an existing destination entry")
    p.add_argument("--skip-notes", action="store_true",
                   help="Copy an entry that has notes, leaving the notes behind")
    p.set_defaults(func=cmd_copy)
