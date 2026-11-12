"""Explicit launcher-to-conduit ownership, without ancestry or state discovery."""

from __future__ import annotations

import argparse
import sys


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--session-owner-pid", type=int,
        help="opt in to session custody with the originating launcher's captured owner PID",
    )
    parser.add_argument(
        "--session-owner-birth",
        help="native birth paired with --session-owner-pid; never a parent-PID guess",
    )


def requested(args: argparse.Namespace) -> bool:
    return any(
        getattr(args, name, None) is not None
        for name in ("session_owner_pid", "session_owner_birth")
    )


def run(target: str, args: argparse.Namespace) -> int:
    from . import custody_launch, forward

    pid = getattr(args, "session_owner_pid", None)
    birth = getattr(args, "session_owner_birth", None)
    if pid is None or birth is None:
        print(
            "agent-mcp: --session-owner-pid and --session-owner-birth must be supplied together",
            file=sys.stderr,
        )
        return 2
    if not forward._multiplex_enabled() or not forward._ensure_serve_enabled():
        print("agent-mcp: explicit session custody requires serve startup enabled", file=sys.stderr)
        return 2
    try:
        custody_launch.run_adopted(
            forward._serve_handle(getattr(args, "socket", None)),
            forward._abs_bridge_ref(target), owner_pid=pid, owner_birth=birth,
            **({"required_trace_bookkeeping": 1}
               if getattr(args, "checkpoint_trace_bookkeeping", 0) == 1 else {}),
        )
    except (OSError, ValueError) as exc:
        print(f"agent-mcp: explicit session custody refused: {exc}", file=sys.stderr)
        return 1
    return 0
