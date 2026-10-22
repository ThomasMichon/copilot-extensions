"""CLI for exact, read-only durable worktree metadata."""

from __future__ import annotations

import argparse

from . import output
from .record_read import RecordReadError, read_exact_record, read_workspace_record


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "record",
        help="Read one exact durable worktree record in this project (JSON; no launch authority)",
    )
    selection = sp.add_mutually_exclusive_group(required=True)
    selection.add_argument("--worktree-id", help="Exact recorded worktree ID (no suffix lookup)")
    selection.add_argument(
        "--cwd", help="Absolute trusted initialization directory (no adoption or resident lookup)",
    )
    sp.add_argument("--json", action="store_true", help="Emit JSON (the default)")


def cmd_record(args: argparse.Namespace) -> int:
    """Return exact ground-owned metadata without discovery or mutation."""
    try:
        if getattr(args, "cwd", None) is not None:
            context = read_workspace_record(args.cwd)
            output._json_output({"ok": True, "context": context})
            return 0
        worktree = read_exact_record(args.worktree_id)
    except RecordReadError as exc:
        output._json_output({
            "ok": False, "worktree": None,
            "error": {"code": exc.code, "message": str(exc)},
        })
        return 1
    output._json_output({"ok": True, "worktree": worktree})
    return 0
