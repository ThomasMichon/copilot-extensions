"""Explicit local preference preview; no carrier enrollment or native apply."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import preferences


def add_parser(subparsers) -> None:
    root = subparsers.add_parser("preferences", help="inspect portable preference data")
    commands = root.add_subparsers(dest="preferences_command", required=True)
    parser = commands.add_parser("preview", help="read explicit local snapshots without applying them")
    parser.add_argument("--input", type=Path, required=True, help="received candidate snapshot")
    parser.add_argument("--accepted", type=Path, help="last-accepted snapshot; never written")
    parser.add_argument("--local-overrides", type=Path, help="explicit local-machine preference document")
    parser.add_argument("--publisher", required=True, help="expected configured publisher identity")
    parser.add_argument("--machine", required=True, help="logical execution identity for scope projection")
    parser.add_argument("--harness", required=True, help="canonical harness identity for scope projection")
    parser.add_argument(
        "--conflict", action="store_true", help="report a known provider conflict; blocks candidate adoption",
    )
    parser.set_defaults(func=run_preview)


def run_preview(args) -> int:
    try:
        result = preferences.preview(
            preferences.read(args.input, optional=True),
            preferences.read(args.accepted) if args.accepted else None,
            publisher=args.publisher, machine=args.machine, harness=args.harness,
            local=preferences.read(args.local_overrides, local=True) if args.local_overrides else None,
            conflict=args.conflict,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0 if result["candidate_eligible"] else 1
    except preferences.PreferenceError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
