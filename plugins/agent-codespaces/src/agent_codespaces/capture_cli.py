"""``sync-sessions`` command: non-destructive, non-lifecycle-transition
Copilot session capture for a single CodeSpace.

Split out of ``__main__.py`` (module-size budget), mirroring
``agent-containers``' ``rescue_capture_cli.py`` split. Backs
session-rescue-parity Phase 3: pulls a CodeSpace's Copilot session-state
into the agent-logger hub while the CodeSpace stays leased and running --
it never boots, stops, finalizes, or deletes anything. See
``sessions.capture_codespace_sessions`` for the full gating contract
(state preflight, lease/claim disposition, account binding, liveness
gate before and after the pull).
"""
from __future__ import annotations

import argparse
import json
import sys

from .sessions import capture_codespace_sessions

_DEFERRED_EXIT = 75


def add_capture_parser(sub) -> None:
    """Register the ``sync-sessions`` subcommand."""
    p = sub.add_parser(
        "sync-sessions",
        help="Capture a CodeSpace's Copilot sessions non-destructively "
             "(it stays leased and running; never boots/stops/deletes)",
    )
    p.add_argument("name", help="CodeSpace name")
    p.add_argument(
        "--account", default=None,
        help="Explicit gh account owning this CodeSpace (required unless an "
             "exact per-name account binding already exists -- capture "
             "fails closed rather than guess across accounts)",
    )
    p.add_argument(
        "--timeout", type=float, default=300.0,
        help="Seconds for the session pull (default: 300)",
    )
    p.add_argument("--verbose", action="store_true", help="Verbose session-sync output")
    p.add_argument("--json", action="store_true", help="Emit the result as JSON")
    p.set_defaults(func=cmd_sync_sessions)


def cmd_sync_sessions(args: argparse.Namespace) -> int:
    result = capture_codespace_sessions(
        args.name, account=args.account, timeout=args.timeout, verbose=args.verbose,
    )
    if args.json:
        print(json.dumps(result))
    elif result.get("ok"):
        print(f"[OK] Captured {result.get('session_count', 0)} session(s) from "
              f"{args.name}: {result.get('detail', '')}")
    else:
        label = "Deferred" if result.get("deferred") else "Failed"
        print(f"[{label}] {args.name}: {result.get('detail')}", file=sys.stderr)
    if result.get("ok"):
        return 0
    return _DEFERRED_EXIT if result.get("deferred") else 1
