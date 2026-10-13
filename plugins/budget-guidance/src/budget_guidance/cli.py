"""Command-line interface for budget posture status."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any

from . import __version__
from .models import ModelError, load_json, parse_config, parse_instant
from .posture import build_posture, unavailable_posture
from .quota import normalize_quota


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


def _emit_model_error(args: argparse.Namespace, exc: ModelError, *, human_prefix: str) -> int:
    """Report a ModelError in the shape ``args.json`` requests and return exit code 2."""
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
        print(f"budget-guidance: {human_prefix}{exc}", file=sys.stderr)
    return 2


def _status(args: argparse.Namespace) -> int:
    # `args.at is not None` (not a truthy check) so an explicitly supplied
    # empty string ("--at \"\"") is still validated and rejected rather than
    # silently defaulting to "now".
    try:
        at = parse_instant(args.at, "--at") if args.at is not None else datetime.now(timezone.utc)
    except ModelError as exc:
        return _emit_model_error(args, exc, human_prefix="")
    try:
        config_path = Path(args.config).expanduser()
        if not config_path.is_file():
            posture = unavailable_posture(at, f"configuration not found: {config_path}")
        else:
            posture = build_posture(parse_config(load_json(config_path)), at)
    except ModelError as exc:
        return _emit_model_error(args, exc, human_prefix="invalid configuration: ")
    if args.json:
        print(json.dumps(posture, indent=2, sort_keys=True))
    else:
        print(_render_human(posture))
    return 0


def _version(_args: argparse.Namespace) -> int:
    print(f"budget-guidance {__version__}")
    return 0


def _quota(args: argparse.Namespace) -> int:
    try:
        path = Path(args.capture)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ModelError("quota capture must be a regular file")
        try:
            capture = load_json(path, max_bytes=65536)
        except ModelError as exc:
            if isinstance(exc.__cause__, OSError):
                raise exc.__cause__
            raise ModelError(
                "quota capture must be valid UTF-8 JSON with unique keys within 65536 bytes"
            ) from None
        if capture.get("schema") != "copilot-extensions.copilot-quota-capture":
            raise ModelError("unsupported quota capture schema")
        if not isinstance(capture.get("version"), Decimal) or capture["version"] != 1:
            raise ModelError("unsupported quota capture version")
        for field in ("session_id", "runtime_version", "acquired_at"):
            if not isinstance(capture.get(field), str):
                raise ModelError(f"capture.{field} must be a string")
        projection = capture.get("projection")
        if not isinstance(projection, dict):
            raise ModelError("capture.projection must be an object")
        result = normalize_quota(
            projection, session_id=capture["session_id"],
            expected_session_id=args.session_id,
            runtime_version=capture["runtime_version"],
            acquired_at=parse_instant(capture["acquired_at"], "capture.acquired_at"),
            category=args.category,
            provider_id=args.provider_id, quota_id=args.quota_id,
            evaluated_at=(
                parse_instant(args.at, "--at") if args.at is not None
                else datetime.now(timezone.utc)
            ),
            max_age_seconds=args.max_age_seconds,
        )
    except OSError as exc:
        return _emit_model_error(
            args, ModelError(f"cannot read quota capture: {exc.strerror}"), human_prefix=""
        )
    except ModelError as exc:
        return _emit_model_error(args, exc, human_prefix="")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"Session quota: {result['availability']}; "
            f"quantity {result['quantity_kind']}; allowance {result['allowance']}; "
            f"used {result['consumption']}; provider freshness {result['freshness']}; "
            f"diagnostics {', '.join(result['diagnostics'])}"
        )
    return 0 if result["availability"] in {"available", "cached", "unlimited"} else 1


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
    quota = subparsers.add_parser("quota", help="inspect a bounded session quota capture")
    quota.add_argument("--capture", required=True, help="allowlisted JSON capture, at most 64 KiB")
    quota.add_argument("--session-id", required=True, help="expected requesting SDK session")
    quota.add_argument("--category", default="premium_interactions")
    quota.add_argument("--provider-id", help="opt in to one SDK provider's quota evidence")
    quota.add_argument("--quota-id", help="select a quota within the explicit provider")
    quota.add_argument("--at", help="evaluate provider freshness at an RFC 3339 instant")
    quota.add_argument("--max-age-seconds", type=int, default=300)
    quota.add_argument("--json", action="store_true")
    quota.set_defaults(handler=_quota)
    version = subparsers.add_parser("version", help="print the budget-guidance version and exit")
    version.set_defaults(handler=_version)
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
