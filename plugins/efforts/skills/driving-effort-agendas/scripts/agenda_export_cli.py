#!/usr/bin/env python3
"""Explicit complete agenda export and readback, without publication or replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agenda import AgendaError

CAPABILITY = "agenda-complete-export-v1"
CONTRIBUTORS_CAPABILITY = "agenda-export-contributors-v1"
SCHEMA = "efforts.agenda-export-cli"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info", allow_abbrev=False)
    for command in ("export", "verify", "contributors"):
        selected = commands.add_parser(command, allow_abbrev=False)
        selected.add_argument("--destination", type=Path, required=True)
        selected.add_argument("--worktree", required=True)
        selected.add_argument("--store-id", required=True)
        selected.add_argument("--expected-revision", type=int)
        selected.add_argument("--contract-version", type=int, choices=(1,), default=1)
        if command == "export":
            selected.add_argument("--state-home", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output: dict[str, Any]
        if args.command == "info":
            output = {"ok": True, "capabilities": [CAPABILITY],
                      "optional_capabilities": [CONTRIBUTORS_CAPABILITY]}
        elif args.command == "contributors":
            from agenda_export_contributors import read_contributors

            output = {"ok": True, "contributors": read_contributors(
                args.destination, worktree=args.worktree, store_id=args.store_id,
                expected_revision=args.expected_revision,
            )}
        else:
            from agenda_export import export_store, verify_export

            selection = {"worktree": args.worktree, "store_id": args.store_id,
                         "expected_revision": args.expected_revision}
            manifest = (
                export_store(args.state_home, args.destination, **selection)
                if args.command == "export" else verify_export(args.destination, **selection)
            )
            output = {"ok": True, "manifest": manifest}
        code = 0
    except (AgendaError, OSError) as exc:
        output = {"ok": False, "error": {"code": "export-unavailable", "message": str(exc)[:1024]}}
        code = 2
    print(json.dumps({"schema": SCHEMA, "version": 1, **output},
                     ensure_ascii=True, allow_nan=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
