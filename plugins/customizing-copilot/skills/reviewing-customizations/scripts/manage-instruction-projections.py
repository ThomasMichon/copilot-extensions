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
    render_local_cache,
    resolve_instruction_source,
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
    if result.operation in ("sync", "render-local-cache"):
        lock_suffix = (
            f", lock {'updated' if result.lock_updated else 'unchanged'}"
            if result.operation == "sync"
            else ""
        )
        print(
            f"\n{len(result.changed)} changed, "
            f"{len(result.unchanged)} unchanged{lock_suffix}"
        )
    print(
        f"{result.blocking} blocking, {result.warnings} warning(s); "
        f"{result.declared} declared, {result.locked} locked"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    resolve = subparsers.add_parser("resolve-source")
    resolve.add_argument("root")
    resolve.add_argument("destination")
    resolve.add_argument("--json", action="store_true")
    resolve.add_argument("--from-settings", action="store_true")
    resolve.add_argument("--agent-worktrees-path")
    for operation in ("sync", "scan", "render-local-cache"):
        subparser = subparsers.add_parser(operation)
        subparser.add_argument("root", nargs="?", default=".")
        subparser.add_argument("--json", action="store_true")
        subparser.add_argument(
            "--installed-root",
            type=Path,
            help="override the installed plugin payload root",
        )
        subparser.add_argument(
            "--agent-worktrees-path",
            help=(
                "Resolved agent-worktrees command (e.g. the session command "
                "catalog's argv[0]). Falls back to an ambient PATH lookup "
                "when omitted."
            ),
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
    if args.operation == "resolve-source":
        try:
            sources = (
                discover_enabled_sources(
                    root, require_trust=False,
                    agent_worktrees_command=args.agent_worktrees_path,
                ) if args.from_settings else None
            )
            selection = resolve_instruction_source(root, args.destination, sources)
        except (OSError, ValueError) as exc:
            print(json.dumps({"blocked": True, "error": str(exc)}))
            return 1
        print(json.dumps(selection, indent=2, sort_keys=True))
        return 0
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
    installed_root = (
        args.installed_root.expanduser().resolve()
        if args.installed_root is not None
        else None
    )

    def _discover():
        return discover_enabled_sources(
            root,
            installed_root=installed_root,
            require_trust=False,
            agent_worktrees_command=args.agent_worktrees_path,
        )

    if args.operation == "render-local-cache":
        try:
            result = render_local_cache(root, _discover)
        except ValueError as exc:
            result = Result(operation=args.operation)
            result.add(BLOCKING, "projection-settings", root, str(exc))
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            _print_human(result)
        return 1 if result.blocking else 0
    sources = None
    if args.operation == "sync" or getattr(args, "from_settings", False):
        try:
            sources = _discover()
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
