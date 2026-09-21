#!/usr/bin/env python3
"""Extension-load reliability report from Copilot CLI's own launch logs.

Every extension launch (fresh session, resumed session, or a forked
discovery pass) writes one log file under
``~/.copilot/logs/extensions/plugin-<name>_<name>-<launch-epoch-ms>-<pid>.log``,
ending in a terminal marker line: ``=== ready ===``, ``=== ready-timeout ===``,
``=== peer-closed-before-ready ===``, or an ``=== exit code=N
disposition=<disposition> ===`` line. This is real, already-being-written
diagnostic data -- no new instrumentation is required to read it.

This script summarizes that data per plugin over a time window, to answer
"how reliably does this plugin's extension actually load" without guessing.
It underpins a plugin-load reliability investigation: a `--split-at`
timestamp lets you compare a plugin's reliability before and after a fix
lands (e.g. before/after a version bump takes effect on this machine via
`copilot plugin update`).

Usage::

    python extension_load_reliability.py --plugin context-handoff --days 7
    python extension_load_reliability.py --plugin context-handoff \\
        --split-at 2026-09-21T05:00:00+00:00 --json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

_TERMINAL_MARKER = re.compile(r"^=== (.+?) ===\s*$", re.MULTILINE)
_DISPOSITION = re.compile(r"disposition=(\S+)")
_CLI_VERSION = re.compile(r"[\\/]pkg[\\/][\w-]+[\\/]([\w.\-]+)(?=[\\/]|\s|$)", re.MULTILINE)
_LAUNCH_EPOCH_MS = re.compile(r"-(\d{10,})-\d+\.log$")

# Terminal outcomes a launch log can resolve to. "unknown" covers older CLI
# versions that predate the disposition/ready-timeout markers entirely, or
# any log this parser cannot confidently classify -- always reported
# separately, never folded into "ready" or a failure bucket.
_KNOWN_OUTCOMES = ("ready", "ready-timeout", "peer-closed-before-ready", "crash")


@dataclass
class LogRecord:
    path: Path
    launch_time: datetime | None
    cli_version: str | None
    outcome: str  # one of _KNOWN_OUTCOMES, or "unknown"


def default_log_dir() -> Path:
    return Path.home() / ".copilot" / "logs" / "extensions"


def _parse_launch_time(name: str, mtime: float) -> datetime:
    match = _LAUNCH_EPOCH_MS.search(name)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc)
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def parse_log(path: Path) -> LogRecord:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    markers = _TERMINAL_MARKER.findall(text)
    disposition_match = _DISPOSITION.search(text)
    version_match = _CLI_VERSION.search(text)

    if "ready-timeout" in markers:
        outcome = "ready-timeout"
    elif "peer-closed-before-ready" in markers:
        outcome = "peer-closed-before-ready"
    elif "ready" in markers:
        outcome = "ready"
    elif disposition_match and disposition_match.group(1) == "crash":
        outcome = "crash"
    else:
        outcome = "unknown"

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    launch_time = _parse_launch_time(path.name, mtime) if mtime or True else None

    return LogRecord(
        path=path,
        launch_time=launch_time,
        cli_version=version_match.group(1) if version_match else None,
        outcome=outcome,
    )


def iter_plugin_logs(log_dir: Path, plugin: str) -> Iterator[Path]:
    if not log_dir.is_dir():
        return
    yield from sorted(log_dir.glob(f"plugin-{plugin}_{plugin}-*.log"))


@dataclass
class WindowSummary:
    label: str
    window_start: datetime | None
    window_end: datetime | None
    total: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    cli_versions: dict[str, int] = field(default_factory=dict)

    @property
    def ready_timeout_rate(self) -> float | None:
        if self.total == 0:
            return None
        return self.outcomes.get("ready-timeout", 0) / self.total

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "total": self.total,
            "outcomes": self.outcomes,
            "ready_timeout_rate": self.ready_timeout_rate,
            "cli_versions": self.cli_versions,
        }


def summarize(
    records: list[LogRecord],
    *,
    label: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> WindowSummary:
    summary = WindowSummary(label=label, window_start=window_start, window_end=window_end)
    for record in records:
        summary.total += 1
        summary.outcomes[record.outcome] = summary.outcomes.get(record.outcome, 0) + 1
        version = record.cli_version or "unknown"
        summary.cli_versions[version] = summary.cli_versions.get(version, 0) + 1
    return summary


def load_records(
    log_dir: Path,
    plugin: str,
    *,
    since: datetime | None = None,
) -> list[LogRecord]:
    records = []
    for path in iter_plugin_logs(log_dir, plugin):
        record = parse_log(path)
        if since is not None and record.launch_time is not None and record.launch_time < since:
            continue
        records.append(record)
    return records


def report(
    log_dir: Path,
    plugin: str,
    *,
    days: int = 7,
    split_at: datetime | None = None,
) -> list[WindowSummary]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    records = load_records(log_dir, plugin, since=since)
    if split_at is None:
        return [summarize(records, label="window", window_start=since, window_end=None)]

    before = [r for r in records if r.launch_time is not None and r.launch_time < split_at]
    after = [r for r in records if r.launch_time is not None and r.launch_time >= split_at]
    return [
        summarize(before, label="before", window_start=since, window_end=split_at),
        summarize(after, label="after", window_start=split_at, window_end=None),
    ]


def _format_human(summaries: list[WindowSummary]) -> str:
    lines = []
    for summary in summaries:
        rate = summary.ready_timeout_rate
        rate_text = f"{rate:.1%}" if rate is not None else "n/a"
        lines.append(f"[{summary.label}] {summary.total} launches, ready-timeout rate {rate_text}")
        for outcome, count in sorted(summary.outcomes.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {outcome}: {count}")
        if summary.cli_versions:
            lines.append("  cli_versions:")
            for version, count in sorted(summary.cli_versions.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {version}: {count}")
    return "\n".join(lines)


def _parse_split_at(value: str) -> datetime:
    """Parse --split-at, treating a naive (offset-less) ISO-8601 value as UTC.

    Parsed log launch times are always timezone-aware (see
    _parse_launch_time); comparing them against a naive datetime raises
    TypeError. A bare `--split-at 2026-09-21T05:00:00` is valid ISO-8601 and
    a reasonable thing to pass, so normalize instead of rejecting it.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True, help="Plugin name, e.g. context-handoff")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default 7)")
    parser.add_argument("--split-at", help="ISO8601 timestamp; reports separate before/after windows")
    parser.add_argument("--log-dir", help="Override the extension log directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a human summary")
    args = parser.parse_args(argv)

    log_dir = Path(args.log_dir) if args.log_dir else default_log_dir()
    split_at = _parse_split_at(args.split_at) if args.split_at else None
    summaries = report(log_dir, args.plugin, days=args.days, split_at=split_at)

    if args.json:
        print(json.dumps([s.to_dict() for s in summaries], indent=2))
    else:
        print(_format_human(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
