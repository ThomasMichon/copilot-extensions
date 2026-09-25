"""``rescue-capture`` command: non-destructive session-evidence capture.

Split out of ``__main__.py`` to stay under its grandfathered module-size
ceiling (same pattern as ``claim_provider_cli.py``). Captures a restricted
fleet member's Copilot session-state evidence into ``$STATE_DIR/rescues/``
without stopping or removing the container -- for an always-on fleet (e.g.
a headless review service) whose only prior rescue trigger
(removal/stop) essentially never fires.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from . import fleet as fleet_mod
from .config import load_config

_BUSY_EXIT = 75


def add_rescue_capture_parser(sub) -> None:
    """Register the ``rescue-capture`` subcommand."""
    p = sub.add_parser(
        "rescue-capture",
        help="Capture session evidence for a fleet, non-destructively",
    )
    p.add_argument("fleet", help="Fleet name")
    p.add_argument("--json", action="store_true", help="Emit operation result JSON")
    p.set_defaults(func=cmd_rescue_capture)


def cmd_rescue_capture(args: argparse.Namespace) -> int:
    config = load_config()
    result = fleet_mod.rescue_capture_fleet(config, args.fleet)
    if args.json:
        print(json.dumps(asdict(result), indent=2))
        return _BUSY_EXIT if result.deferred else 0
    print(f"Captured: {', '.join(result.captured) if result.captured else '(none)'}")
    for name, reason in result.deferred.items():
        print(f"Deferred: {name} ({reason})")
    return _BUSY_EXIT if result.deferred else 0
