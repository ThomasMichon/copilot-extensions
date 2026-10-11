"""The thin standalone entry point; this initial slice performs offline planning."""

import argparse
import sys
import time
from pathlib import Path

from . import __version__
from .planning import MAX_BYTES, PlanError, canonical, decode, plan


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
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        result = plan(read(args.template, "template"), read(args.config, "config") if args.config else None,
                      read(args.inventory, "inventory"), fleet_id=args.fleet_id,
                      machine_limit=args.machine_limit, now=time.time())
        serialized = canonical(result, output=True).decode("utf-8")
    except PlanError as exc:
        print(f"fleet-manager: {exc}", file=sys.stderr)
        return 1
    print(serialized)
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
