"""Explicit local report command; never installs or activates telemetry."""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path


def add_parser(subparsers) -> None:
    health = subparsers.add_parser("health", help="bounded local health observations")
    commands = health.add_subparsers(dest="health_command", required=True)
    report = commands.add_parser("report", help="summarize explicit local telemetry files")
    report.add_argument("--input", type=Path, action="append", default=[])
    report.add_argument(
        "--store-dir", type=Path,
        help="Read an explicit local health store; never probes, records or publishes",
    )
    report.add_argument(
        "--service", action="append", default=[],
        help="Installed same-cell service, optionally LABEL=SERVICE; read-only hourly export",
    )
    report.add_argument("--hour", help="UTC service hour: YYYY-MM-DDTHH:00:00Z (default: current hour)")
    report.add_argument("--now", help="aware ISO observation time (default: current UTC time)")
    report.add_argument("--chart", type=Path, help="create a new static SVG; never overwrite")
    report.set_defaults(func=run_report)
    record = commands.add_parser("record", help="preview or confirm an explicit local hourly observation", allow_abbrev=False)
    record.add_argument("--input", type=Path, required=True)
    record.add_argument("--store-dir", type=Path, required=True, help="preprovisioned local store with policy.json")
    record.add_argument("--now", help="aware ISO evaluation time (default: current UTC time)")
    record.add_argument("--apply", action="store_true")
    record.add_argument("--expect", default="", help="exact token from the current local preview")
    record.set_defaults(func=run_slots)
    inspect = commands.add_parser("inspect", help="read 168 age-qualified local health slots", allow_abbrev=False)
    inspect.add_argument("--store-dir", type=Path, required=True)
    inspect.add_argument("--now", help="aware ISO evaluation time (default: current UTC time)")
    inspect.set_defaults(func=run_slots)
    observe = commands.add_parser("observe", help="read one explicitly selected local Bridge listener", allow_abbrev=False)
    observe.add_argument("--bridge-port", type=int, required=True, help="numeric loopback port; no discovery or service start")
    observe.add_argument("--publisher", required=True)
    observe.add_argument("--continuity", required=True)
    observe.set_defaults(func=run_observe)
    collect = commands.add_parser(
        "collect", help="collect one local hourly candidate; never stage or enroll a job", allow_abbrev=False,
    )
    collect.add_argument("--store-dir", type=Path, required=True, help="explicit preprovisioned v2 store")
    collect.add_argument("--hour", help="canonical UTC hour; current health by default, historical services only")
    collect.set_defaults(func=run_collect)
    run = commands.add_parser(
        "run", help="preview or apply a policy-scoped two-hour local collection", allow_abbrev=False,
    )
    run.add_argument("--store-dir", type=Path, required=True, help="explicit preprovisioned v2 store")
    run.add_argument("--apply", action="store_true")
    run.add_argument("--expect-policy", default="", help="approval token from the local policy preview")
    run.set_defaults(func=run_collection)


def run_collection(args) -> int:
    from agent_procutil.owned_process import OwnedCleanupError

    from .health_run import run
    from .health_slots import HealthError

    result = None
    try:
        try:
            result = run(args.store_dir, apply=args.apply, expected=args.expect_policy)
        except (OwnedCleanupError, KeyboardInterrupt) as error:
            result = getattr(error, "health_run_result", None)
            if result is None:
                raise
        print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
        return 0 if result["status"] in {"preview", "completed"} else 2
    except (ValueError, OSError, OverflowError) as error:
        if result is not None and isinstance(error, OSError):
            _silence_failed_stdout()
        print(json.dumps({
            "ok": False,
            "error": "response-output-unavailable" if result is not None else (
                str(error) if isinstance(error, HealthError) else "collection-unavailable"
            ),
            "applied": None if result is not None and args.apply else False,
            "carrier_publication": "not-requested",
        }), file=sys.stderr)
        return 2


def run_collect(args) -> int:
    from agent_procutil.owned_process import OwnedCleanupError

    from .health_collect import collect
    from .health_slots import HealthError

    result = None
    try:
        result = collect(args.store_dir, args.hour)
        print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
        return 0
    except (ValueError, OSError, OverflowError, OwnedCleanupError) as error:
        if result is not None and isinstance(error, OSError):
            _silence_failed_stdout()
        reason = (
            "collection-cleanup-unconfirmed" if isinstance(error, OwnedCleanupError)
            else str(error) if isinstance(error, HealthError) else "collection-unavailable"
        )
        print(json.dumps({
            "ok": False, "error": reason, "applied": False, "carrier_publication": "not-requested",
        }), file=sys.stderr)
        return 2


def run_observe(args) -> int:
    from .health_observe import observe_bridge
    from .health_slots import HealthError

    result = None
    try:
        result = observe_bridge(args.bridge_port, args.publisher, args.continuity)
        print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
        return 0
    except (ValueError, OSError, OverflowError) as error:
        if result is not None and isinstance(error, OSError):
            _silence_failed_stdout()
        reason = str(error) if isinstance(error, HealthError) else "observation-unavailable"
        print(json.dumps({"ok": False, "error": reason}), file=sys.stderr)
        return 2


def _silence_failed_stdout() -> None:
    if sys.stdout is sys.__stdout__:
        # A second buffered flush must not replace the command's exit status.
        try:
            with open(os.devnull, "wb") as sink:
                os.dup2(sink.fileno(), sys.stdout.fileno())
        except (OSError, ValueError):
            pass


def _chart_overlaps_store(chart: Path, store: Path) -> bool:
    if Path(os.path.abspath(chart)).is_relative_to(Path(os.path.abspath(store))):
        return True
    try:
        destination = chart.resolve()
    except RuntimeError:
        raise ValueError("invalid chart path") from None
    try:
        namespace = store.resolve()
    except (OSError, RuntimeError):
        # An unresolvable store is unavailable evidence, not an output failure.
        return False
    return destination.is_relative_to(namespace)


def run_slots(args) -> int:
    from . import health_slots

    result = None
    try:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise health_slots.HealthError("aware-time-required")
        fields = {"now_ms": int(now.timestamp() * 1000)}
        if args.health_command == "record":
            result = health_slots.record(
                args.store_dir, args.input, apply=args.apply, expected=args.expect, **fields,
            )
        else:
            result = health_slots.inspect(args.store_dir, **fields)
        print(json.dumps(result, separators=(",", ":"), allow_nan=False), flush=True)
        if result.get("status") == "unknown":
            return 2
        return 0 if result.get("eligible", True) else 1
    except (ValueError, OSError, TimeoutError, OverflowError) as error:
        if result is not None and isinstance(error, OSError):
            _silence_failed_stdout()
        reason = (
            "response-output-unavailable" if result is not None
            else str(error) if isinstance(error, health_slots.HealthError) else "local-store-unavailable"
        )
        print(json.dumps({
            "ok": False, "error": reason,
            "applied": None if result is not None and getattr(args, "apply", False) else False,
            "probe_execution": "not-performed", "carrier_publication": "not-requested",
        }), file=sys.stderr)
        return 2


def run_report(args) -> int:
    from .health_report import HourlyReport, MAX_INPUTS

    try:
        if len(args.input) > MAX_INPUTS:
            raise ValueError("too many inputs")
        now = (
            datetime.fromisoformat(args.now.replace("Z", "+00:00"))
            if args.now else datetime.now(timezone.utc)
        )
        report = HourlyReport(now)
        if not args.input and not args.service and args.store_dir is None:
            raise ValueError("an explicit input, service or health store is required")
        if (
            args.store_dir is not None and args.chart is not None
            and _chart_overlaps_store(args.chart, args.store_dir)
        ):
            raise ValueError("chart overlaps health store")
        seen = set()
        for path in args.input:
            descriptor = None
            try:
                before = path.lstat()
                if (
                    not stat.S_ISREG(before.st_mode) or path.is_symlink()
                    or getattr(before, "st_file_attributes", 0) & 0x400
                ):
                    report.unavailable()
                    continue
                descriptor = os.open(
                    path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                )
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                ):
                    report.unavailable()
                    continue
                identity = opened.st_dev, opened.st_ino
                if identity in seen:
                    report.coverage["duplicate_inputs"] += 1
                    continue
                seen.add(identity)
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = None
                    report.scan(stream, opened.st_size)
            except OSError:
                report.unavailable()
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        result = report.result(len(args.input))
        if args.service:
            from .service_reports import collect
            from process_telemetry.hourly import hour_start

            hour = args.hour if args.hour is not None else now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
            if not report.first_hour <= hour_start(hour) // 3_600_000 <= report.last_hour:
                raise ValueError("service hour outside report window")
            result["services"] = collect(args.service, hour, now_ms=report.now_ns // 1_000_000)
            result["services_collected_at_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)
        elif args.hour is not None:
            raise ValueError("hour requires an explicit service")
        if args.store_dir is not None:
            from . import health_slots

            try:
                view = health_slots.inspect(args.store_dir, now_ms=report.now_ns // 1_000_000)
                result["health"] = {"state": "available", "view": view}
            except (OSError, health_slots.HealthError):
                result["health"] = {"state": "unavailable", "reason": "local-store-unavailable"}
        if args.chart is not None:
            from .health_chart import render

            with args.chart.open("x", encoding="utf-8") as output:
                output.write(render(result))
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0
    except (OSError, ValueError, OverflowError):
        print("health report unavailable: invalid request or output", file=sys.stderr)
        return 2
