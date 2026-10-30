"""``agent-containers drivers`` -- list registered runtime drivers."""

from __future__ import annotations

import argparse
import json

from .external_driver import PATH_PREFIX, drivers_dir, scan_drivers


def add_drivers_parser(sub) -> None:
    parser = sub.add_parser(
        "drivers",
        help="List registered external runtime drivers and invalid entries",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.set_defaults(func=cmd_drivers)


def cmd_drivers(args: argparse.Namespace) -> int:
    specs, findings = scan_drivers()
    if args.json:
        print(json.dumps({
            "drivers_dir": str(drivers_dir()),
            "drivers": [
                {"name": s.name, "command": list(s.command), "source": s.source}
                for s in specs.values()
            ],
            "findings": findings,
        }, indent=2))
        return 0
    print(f"Driver manifests: {drivers_dir()}")
    if not specs:
        print(f"No manifest drivers (PATH fallback: {PATH_PREFIX}<name>).")
    for spec in specs.values():
        print(f"  {spec.name:<20} {' '.join(spec.command)}")
    for finding in findings:
        print(f"  INVALID {finding['path']}: {finding['reason']}")
    return 1 if findings else 0
