"""Explicit local preference exchange and separately confirmed scalar floors."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import preferences


def add_parser(subparsers) -> None:
    root = subparsers.add_parser("preferences", help="inspect portable preference data")
    commands = root.add_subparsers(dest="preferences_command", required=True)
    parser = commands.add_parser("preview", help="read explicit local snapshots without applying them")
    parser.add_argument("--input", type=Path, required=True, help="received candidate snapshot")
    parser.add_argument("--accepted", type=Path, help="last-accepted snapshot; never written")
    parser.add_argument("--local-overrides", type=Path, help="explicit local-machine preference document")
    parser.add_argument("--publisher", required=True, help="expected configured publisher identity")
    parser.add_argument("--machine", required=True, help="logical execution identity for scope projection")
    parser.add_argument("--harness", required=True, help="canonical harness identity for scope projection")
    parser.add_argument(
        "--conflict", action="store_true", help="report a known provider conflict; blocks candidate adoption",
    )
    parser.set_defaults(func=run_preview)
    for name in ("accept", "publish"):
        parser = commands.add_parser(name, help=f"preview or explicitly {name} a local snapshot")
        parser.add_argument("--input", type=Path, required=True, help="complete candidate snapshot")
        parser.add_argument("--state-dir", type=Path, required=True, help="unsynchronized local state with policy.json")
        parser.add_argument("--apply", action="store_true", help="commit the exact preview, not native adoption")
        parser.add_argument("--expect", default="", help="token from the current exchange preview")
        if name == "accept":
            parser.add_argument("--machine", required=True, help="logical execution identity")
            parser.add_argument("--harness", required=True, help="canonical harness identity")
            parser.add_argument("--local-overrides", type=Path)
            parser.add_argument("--conflict", action="store_true")
            parser.add_argument(
                "--full-snapshot", action="store_true", help="explicitly reconcile a missing parent only",
            )
        else:
            parser.add_argument("--output", type=Path, required=True, help="exact local publication document")
        parser.set_defaults(func=run_exchange)
    carrier = commands.add_parser(
        "carrier", help="explicit exchange through a configured local OneDrive replica", allow_abbrev=False,
    )
    operations = carrier.add_subparsers(dest="carrier_operation", required=True)
    for name in ("receive", "publish"):
        parser = operations.add_parser(name, allow_abbrev=False)
        parser.add_argument("--state-dir", type=Path, required=True, help="local policy and carrier.json")
        parser.add_argument("--apply", action="store_true", help="confirm local exchange, not native adoption")
        parser.add_argument("--expect", default="")
        if name == "publish":
            parser.add_argument("--input", type=Path, required=True, help="complete locally authored snapshot")
        else:
            parser.add_argument("--machine", required=True)
            parser.add_argument("--harness", required=True)
            parser.add_argument("--local-overrides", type=Path)
            parser.add_argument("--full-snapshot", action="store_true")
        parser.set_defaults(func=run_carrier)
    parser = commands.add_parser("copilot", help="preview or adopt accepted Copilot scalar floors")
    parser.add_argument("--state-dir", type=Path, required=True, help="local accepted state and policy")
    parser.add_argument("--local-overrides", type=Path)
    parser.add_argument("--apply", action="store_true", help="apply only model/reasoning floors through the native owner")
    parser.add_argument("--expect", default="", help="token from the current native preview")
    parser.set_defaults(func=run_copilot)
    parser = commands.add_parser("telemetry", help="preview or adopt accepted retained-telemetry defaults")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--local-overrides", type=Path)
    parser.add_argument("--producer", choices=("agent-bridge", "agent-dispatch"), required=True)
    parser.add_argument("--directory", type=Path, required=True, help="explicit preprovisioned local namespace")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect", default="")
    parser.set_defaults(func=run_telemetry)
    parser = commands.add_parser(
        "repositories", help="read accepted relationship defaults without native effects", allow_abbrev=False,
    )
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--local-overrides", type=Path)
    parser.add_argument("--machine", required=True, help="native caller's logical execution identity")
    parser.add_argument("--harness", required=True, help="native caller's canonical repository identity")
    parser.set_defaults(func=run_repositories)


def run_repositories(args) -> int:
    from .preference_repositories import project

    try:
        result = project(
            args.state_dir, machine=args.machine, harness=args.harness, local=args.local_overrides,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0
    except (preferences.PreferenceError, OSError) as error:
        reason = str(error) if isinstance(error, preferences.PreferenceError) else "preference-input-unavailable"
        print(json.dumps({"ok": False, "error": reason}), file=sys.stderr)
        return 2


def run_preview(args) -> int:
    try:
        result = preferences.preview(
            preferences.read(args.input, optional=True),
            preferences.read(args.accepted) if args.accepted else None,
            publisher=args.publisher, machine=args.machine, harness=args.harness,
            local=preferences.read(args.local_overrides, local=True) if args.local_overrides else None,
            conflict=args.conflict,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0 if result["candidate_eligible"] else 1
    except preferences.PreferenceError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2


def run_exchange(args) -> int:
    from .preference_exchange import exchange

    try:
        fields = {}
        if args.preferences_command == "accept":
            fields = {
                "machine": args.machine, "harness": args.harness,
                "local": args.local_overrides, "conflict": args.conflict,
                "full_snapshot": args.full_snapshot,
            }
        else:
            fields = {"output": args.output}
        result = exchange(
            args.preferences_command, state=args.state_dir, source=args.input,
            apply=args.apply, expected=args.expect, **fields,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0 if result["eligible"] else 1
    except (preferences.PreferenceError, OSError, TimeoutError) as error:
        reason = str(error) if isinstance(error, preferences.PreferenceError) else "exchange-unavailable"
        print(json.dumps({"ok": False, "error": reason}), file=sys.stderr)
        return 2


def run_carrier(args) -> int:
    from .cli_health import _silence_failed_stdout
    from .preference_carrier import exchange

    result = None
    try:
        fields = (
            {"source": args.input} if args.carrier_operation == "publish"
            else {
                "machine": args.machine, "harness": args.harness,
                "local": args.local_overrides, "full_snapshot": args.full_snapshot,
            }
        )
        result = exchange(
            args.carrier_operation, state=args.state_dir, apply=args.apply, expected=args.expect, **fields,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
        return 0 if result["eligible"] else 1
    except (preferences.PreferenceError, OSError, TimeoutError) as error:
        if result is not None and isinstance(error, OSError):
            _silence_failed_stdout()
        reason = (
            "response-output-unavailable" if result is not None
            else str(error) if isinstance(error, preferences.PreferenceError) else "carrier-exchange-unavailable"
        )
        print(json.dumps({
            "ok": False, "error": reason, "applied": None if args.apply else False,
            "configuration_applied": False, "binding_changed": False, "peer_receipt": "not-observed",
        }), file=sys.stderr)
        return 2


def run_copilot(args) -> int:
    from configuration_layers import ConfigurationError
    from ._peer_launch import ContextRefused
    from .manifest import ManifestError
    from .preference_copilot import adopt
    from .surfaces._common import SurfaceStateError

    try:
        result = adopt(
            args.state_dir, local=args.local_overrides, apply=args.apply, expected=args.expect,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0
    except (
        preferences.PreferenceError, ConfigurationError, ContextRefused,
        ManifestError, SurfaceStateError, OSError,
    ) as error:
        reason = str(error) if isinstance(error, preferences.PreferenceError) else "native-configuration-unavailable"
        print(json.dumps({"ok": False, "error": reason}), file=sys.stderr)
        return 2


def run_telemetry(args) -> int:
    import subprocess
    from configuration_layers import ConfigurationError
    from ._peer_launch import ContextRefused
    from .preference_telemetry import adopt

    try:
        result = adopt(
            args.state_dir, producer=args.producer, directory=args.directory,
            local=args.local_overrides, apply=args.apply, expected=args.expect,
        )
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        if result["status"] in {"error", "unknown"} or result.get("native", {}).get("status") == "error":
            return 2
        return 0 if result["eligible"] else 1
    except (
        preferences.PreferenceError, ConfigurationError, ContextRefused,
        OSError, RuntimeError, subprocess.SubprocessError,
    ) as error:
        reason = str(error) if isinstance(error, preferences.PreferenceError) else "native-configuration-unavailable"
        print(json.dumps({
            "ok": False, "error": reason, "configuration_applied": None if args.apply else False,
            "activation": "not-requested",
        }), file=sys.stderr)
        return 2
