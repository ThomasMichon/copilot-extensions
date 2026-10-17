"""Thin fleet planning/diagnostics and explicit opt-in NEW Gateway serving."""

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .planning import MAX_BYTES, PlanError, canonical, decode, plan
from .release import ROLES, inspect_release


def read(path, label):
    try:
        with path.open("rb") as stream:
            return decode(stream.read(MAX_BYTES + 1))
    except OSError:
        raise PlanError(f"{label} file could not be read") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"fleet-manager {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser("plan", help="Compare explicit non-secret snapshots; never provision")
    planning.add_argument("--template", type=Path, required=True)
    planning.add_argument("--config", type=Path, help="Existing adopter config; omitted means no config")
    planning.add_argument("--inventory", type=Path, required=True)
    planning.add_argument("--fleet-id", required=True)
    planning.add_argument("--machine-limit", type=int, required=True)
    inspection = commands.add_parser("inspect", help="Inspect explicit release snapshots; never probe or install")
    inspection.add_argument("--release", type=Path, required=True)
    inspection.add_argument("--observation", type=Path, help="Explicit installation observation; absent means unknown")
    inspection.add_argument("--role", choices=ROLES, required=True)
    inspection.add_argument("--authority", required=True)
    inspection.add_argument("--installation", required=True)
    diagnostic = commands.add_parser("doctor", help="Check this installed thin client; never install or probe a controller")
    diagnostic.add_argument("--role", choices=("client",), default="client")
    hosting = commands.add_parser("serve", help="Host the explicitly configured NEW Gateway over TLS")
    hosting.add_argument("--role", choices=("gateway",), required=True)
    hosting.add_argument("--config", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    if args.command == "serve":
        try:
            from .gateway_server import serve
        except ImportError:
            print("fleet-manager: serve requires the server/auth extras", file=sys.stderr)
            return 1
        return serve(args.config)
    try:
        if args.command == "doctor":
            from .client_diagnostics import diagnose_client

            result = diagnose_client()
        elif args.command == "inspect":
            result = inspect_release(
                read(args.release, "release"),
                read(args.observation, "observation") if args.observation else None,
                role=args.role, authority=args.authority, installation=args.installation,
                now=time.time(),
            )
        else:
            result = plan(read(args.template, "template"), read(args.config, "config") if args.config else None,
                          read(args.inventory, "inventory"), fleet_id=args.fleet_id,
                          machine_limit=args.machine_limit, now=time.time())
        serialized = canonical(result, output=True).decode("utf-8")
    except PlanError as exc:
        print(f"fleet-manager: {exc}", file=sys.stderr)
        return 1
    print(serialized)
    if args.command == "doctor":
        return 0
    if args.command == "inspect":
        return 0 if result["status"] in ("installed", "ready") else 2
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
