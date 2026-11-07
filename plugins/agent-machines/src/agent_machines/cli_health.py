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
    report.add_argument("--input", type=Path, action="append", required=True)
    report.add_argument("--now", help="aware ISO observation time (default: current UTC time)")
    report.add_argument("--chart", type=Path, help="create a new static SVG; never overwrite")
    report.set_defaults(func=run_report)


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
        if args.chart is not None:
            from .health_chart import render

            with args.chart.open("x", encoding="utf-8") as output:
                output.write(render(result))
        print(json.dumps(result, separators=(",", ":"), allow_nan=False))
        return 0
    except (OSError, ValueError, OverflowError):
        print("health report unavailable: invalid request or output", file=sys.stderr)
        return 2
