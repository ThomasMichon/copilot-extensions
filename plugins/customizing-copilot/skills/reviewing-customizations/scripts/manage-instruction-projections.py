#!/usr/bin/env python3
"""Synchronize or scan declarative plugin instruction projections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from instruction_projections import (
    BLOCKING,
    Result,
    discover_enabled_sources,
    scan_repository,
    sync_repository,
    validate_repository_root,
)


def _print_human(result) -> None:
    if result.findings:
        order = {"blocking": 0, "warning": 1}
        for finding in sorted(
            result.findings,
            key=lambda item: (
                order.get(item.severity, 9),
                item.check,
                item.path,
            ),
        ):
            label = "BLOCK" if finding.severity == "blocking" else "WARN "
            print(
                f"[{label}] {finding.check}: {finding.path}\n"
                f"        {finding.message}"
            )
    else:
        print("[OK] instruction projections are consistent")
    if result.operation == "sync":
        print(
            f"\n{len(result.changed)} changed, "
            f"{len(result.unchanged)} unchanged, "
            f"lock {'updated' if result.lock_updated else 'unchanged'}"
        )
    print(
        f"{result.blocking} blocking, {result.warnings} warning(s); "
        f"{result.declared} declared, {result.locked} locked"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("sync", "scan"):
        subparser = subparsers.add_parser(operation)
        subparser.add_argument("root", nargs="?", default=".")
        subparser.add_argument("--json", action="store_true")
        subparser.add_argument(
            "--installed-root",
            type=Path,
            help="override the installed plugin payload root",
        )
        if operation == "scan":
            subparser.add_argument(
                "--from-settings",
                action="store_true",
                help="also compare against currently enabled plugin payloads",
            )
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    try:
        validate_repository_root(root)
    except ValueError as exc:
        result = Result(operation=args.operation)
        result.add(BLOCKING, "projection-root", root, str(exc))
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            _print_human(result)
        return 1
    sources = None
    if args.operation == "sync" or args.from_settings:
        try:
            sources = discover_enabled_sources(
                root,
                installed_root=(
                    args.installed_root.expanduser().resolve()
                    if args.installed_root is not None
                    else None
                ),
                require_trust=False,
            )
        except ValueError as exc:
            result = Result(operation=args.operation)
            result.add(
                BLOCKING,
                "projection-settings",
                root,
                str(exc),
            )
            if args.json:
                print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
            else:
                _print_human(result)
            return 1
    result = (
        sync_repository(root, sources or [])
        if args.operation == "sync"
        else scan_repository(root, sources)
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        _print_human(result)
    return 1 if result.blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
