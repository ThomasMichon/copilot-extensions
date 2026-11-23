"""Project-scoped read-only Bridge listing and exact inspection."""

from __future__ import annotations

import argparse
import sys
from typing import Any


def _scope(args: argparse.Namespace) -> dict[str, Any]:
    from . import __main__ as core

    all_projects = getattr(args, "all_projects", False)
    project = None if all_projects else core._sender_repo()
    if not project and not all_projects:
        raise SystemExit("Cannot determine project; use --project REPO or --all-projects")
    return {
        "project": project,
        "machine": getattr(args, "machine", None),
        "namespace": getattr(args, "namespace", None),
        "all_projects": all_projects,
    }


def command(args: argparse.Namespace) -> None:
    from . import __main__ as core

    scope: dict[str, Any] = {
        **_scope(args),
        "status": getattr(args, "status", None),
        "live": getattr(args, "live", False),
    }
    hosts = getattr(args, "hosts", None)
    if hosts:
        scope["hosts"] = hosts
    result = core._get_client(ensure=False).list_bridges(**scope)
    worktree_id = getattr(args, "worktree_id", None)
    if worktree_id:
        result = {
            **result,
            "bridges": [row for row in result["bridges"] if row.get("worktree_id") == worktree_id],
        }
    if args.json:
        core._json_out(result)
        return
    for row in result["bridges"]:
        route = row.get("route") or {}
        handle = route.get("host") or row.get("machine") or "unknown-host"
        disposition = row.get("disposition") or {}
        activity = (
            disposition.get("activity") or disposition.get("summary") or row.get("title") or ""
        )
        print(
            f"{handle}/{row['handle']} [{row['status']}] "
            f"{row.get('hosting', 'unknown')}/{row.get('transport', 'unknown')} "
            f"control={row['control']} {activity}".rstrip()
        )
    if not result["complete"]:
        print(
            "[WARN] Bridge inventory is incomplete; inspect JSON source evidence.", file=sys.stderr
        )


def show(args: argparse.Namespace) -> None:
    """Inspect one conversation through the same inventory and project scope as list."""
    from . import __main__ as core

    scope = _scope(args)
    if getattr(args, "hosts", None):
        scope["hosts"] = args.hosts
    result = core._get_client(ensure=False).resolve_bridge(args.target, **scope)
    if args.json:
        core._json_out(result)
        return
    row = result["bridge"]
    route = row.get("route") or {}
    host = route.get("host") or row.get("machine") or "unknown-host"
    print(f"{host}/{row['handle']} [{row['status']}] control={row['control']}")
    for key in ("session_id", "hosting", "transport", "directory", "title"):
        if row.get(key) is not None:
            print(f"  {key}: {row[key]}")
    if not result["complete"]:
        print(
            "[WARN] Bridge inventory is incomplete; inspect JSON source evidence.", file=sys.stderr
        )


def register(sub: argparse._SubParsersAction[Any]) -> None:
    parser = sub.add_parser(
        "list", help="List project bridges, including dormant workspace conversations"
    )
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--all-projects", action="store_true")
    parser.add_argument("--machine", help="Filter the conversation's machine")
    parser.add_argument("--namespace")
    parser.add_argument("--status", choices=("running", "dormant", "retained"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--host",
        dest="hosts",
        action="append",
        help="Read this configured Bridge host (repeatable)",
    )
    parser.set_defaults(func=command)
    inspection = sub.add_parser(
        "show", help="Inspect one bridge by exact workspace, native or managed identity"
    )
    inspection.add_argument("target")
    inspection.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    inspection.add_argument("--all-projects", action="store_true")
    inspection.add_argument("--machine", help="Filter the conversation's machine")
    inspection.add_argument("--namespace")
    inspection.add_argument(
        "--host",
        dest="hosts",
        action="append",
        help="Read this configured Bridge host (repeatable)",
    )
    inspection.set_defaults(func=show)
