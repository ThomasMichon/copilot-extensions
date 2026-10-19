"""CLI for exact, read-only durable worktree metadata."""

from __future__ import annotations

import argparse

from . import output
from .record_read import RecordReadError, read_exact_record


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "record",
        help="Read one exact durable worktree record in this project (JSON; no launch authority)",
    )
    sp.add_argument("--worktree-id", required=True, help="Exact recorded worktree ID (no suffix lookup)")
    sp.add_argument("--json", action="store_true", help="Emit JSON (the default)")


def cmd_record(args: argparse.Namespace) -> int:
    """Return exact ground-owned metadata without discovery or mutation."""
    try:
        worktree = read_exact_record(args.worktree_id)
    except RecordReadError as exc:
        output._json_output({
            "ok": False, "worktree": None,
            "error": {"code": exc.code, "message": str(exc)},
        })
        return 1
    output._json_output({"ok": True, "worktree": worktree})
    return 0
