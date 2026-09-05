"""Command-line interface for budget posture status."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

from . import __version__
from .models import ModelError, load_json, parse_config, parse_instant
from .posture import build_posture, unavailable_posture


def _default_config() -> Path:
    configured = os.environ.get("BUDGET_GUIDANCE_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".budget-guidance" / "config.json"


def _render_human(posture: dict[str, Any]) -> str:
    """Render concise human output from the machine-readable posture."""
    availability = posture["availability"]
    if posture["calculated"] is None:
        return f"Budget posture: {availability} - {posture['error']}"
    calculated = posture["calculated"]
    sources = sorted(
        field["selected"]["source"]
        for field in posture["fields"].values()
    )
    projection = (
        f", projected {calculated['projected_consumption']} at reset"
        if calculated["projected_consumption"] is not None
        else ", projection unavailable (no trailing rate)"
    )
    return (
        f"Budget posture: {calculated['warning_band']} ({availability}); "
        f"remaining {calculated['remaining']}, "
        f"{calculated['days_remaining']} days to reset, "
        f"sustainable/day {calculated['sustainable_daily_rate']}, "
        f"effective limit/day {calculated['effective_daily_limit']}"
        f"{projection}; sources {', '.join(dict.fromkeys(sources))}"
    )


def _status(args: argparse.Namespace) -> int:
    at = parse_instant(args.at, "--at") if args.at else datetime.now(timezone.utc)
    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        posture = unavailable_posture(at, f"configuration not found: {config_path}")
    else:
        try:
            posture = build_posture(parse_config(load_json(config_path)), at)
        except ModelError as exc:
            if args.json:
                print(
                    json.dumps(
                        {
                            "schema": "copilot-extensions.budget-posture-error",
                            "version": 1,
                            "error": str(exc),
                        },
                        sort_keys=True,
                    )
                )
            else:
                print(f"budget-guidance: invalid configuration: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(posture, indent=2, sort_keys=True))
    else:
        print(_render_human(posture))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the public command parser."""
    parser = argparse.ArgumentParser(prog="budget-guidance")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="report the current resolved posture")
    status.add_argument("--config", default=str(_default_config()))
    status.add_argument("--at", help="evaluate freshness at an RFC 3339 instant")
    status.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    status.set_defaults(handler=_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the budget-guidance CLI."""
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ModelError as exc:
        print(f"budget-guidance: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
