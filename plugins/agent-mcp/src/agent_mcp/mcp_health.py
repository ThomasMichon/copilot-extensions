"""Sweep Copilot CLI's own process logs for a known set of MCP-lifecycle
health signals, and snapshot the persisted tool-snapshot cache's current
staleness ratio.

Copilot CLI's runtime process log (``$COPILOT_HOME/logs``, default
``~/.copilot/logs/``) already carries a small set of warning/debug
signatures worth tracking as a health indicator over time:

- ``stale_schema_cache_entry`` -- a persisted MCP tool-snapshot cache entry
  rejected for a schema-version mismatch. This is the exact class
  ``clean-tool-cache`` (see :mod:`agent_mcp.tool_cache_maintenance`) purges;
  a healthy, regularly-cleaned cache should show this trending toward zero.
- ``cache_hydration_timeout`` -- the persisted tool cache's own load timed
  out (a tight, hardcoded budget in the runtime).
- ``pending_snapshot`` -- a reload/reconcile snapshot showing zero connected
  servers and at least one still `pending` (still connecting). A single hit
  is normal -- a server briefly passes through `pending` on every connect --
  so treat this as a rate/persistence signal (many hits, or hits recurring
  across widely-spaced snapshots for the same window) rather than proof of
  a stuck server on its own; ``first_seen``/``last_seen`` are reported so a
  caller can judge how long a window of elevated counts lasted.
- ``explicit_failed_retry`` -- a server with a nonzero failed-retry count on
  a reload snapshot -- an explicit, not merely suspected, failure.

This module is read-only: it never touches the log files or the cache
directory's contents, only reads them. It exists to answer "is a mitigation
like ``clean-tool-cache`` actually improving MCP session reliability over
time," not to fix anything itself.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Matches a raw runtime log line's leading RFC3339-ish timestamp, e.g.
# "2026-09-21T06:33:50.785Z [WARNING] [rust:copilot_runtime::...] message".
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")

_SIGNALS: dict[str, re.Pattern[str]] = {
    "stale_schema_cache_entry": re.compile(
        r"Skipping invalid MCP tool cache entry.*Unsupported MCP tool cache schema version"
    ),
    "cache_hydration_timeout": re.compile(r"Timed out loading persisted MCP tool cache"),
    # Matches the reload/reconcile snapshot's own counters regardless of
    # which log message they're attached to -- the field pair is the signal,
    # not the surrounding phrase, since the runtime has more than one call
    # site that logs a live-graph snapshot.
    "pending_snapshot": re.compile(r'"connected_count":0.*?"pending_count":([1-9]\d*)'),
    "explicit_failed_retry": re.compile(r'"failed_retry_count":([1-9]\d*)'),
}


@dataclass
class SignalHit:
    line: str
    timestamp: datetime | None


@dataclass
class SweepResult:
    log_dir: Path
    files_scanned: int
    lines_scanned: int
    since: datetime | None
    signal_counts: dict[str, int]
    signal_samples: dict[str, list[str]]
    first_seen: dict[str, str]
    last_seen: dict[str, str]


def resolve_log_dir(override: str | None = None) -> Path | None:
    """Mirror the runtime's own log-directory resolution: `$COPILOT_HOME/logs`,
    where `COPILOT_HOME` defaults to `~/.copilot` when unset (documented in
    the CLI's own `--help`: "COPILOT_HOME: override the directory where
    configuration and state files are stored; defaults to $HOME/.copilot").
    This is a different resolution path from the tool-snapshot cache's own
    `COPILOT_CACHE_HOME`/platform-cache-dir logic in
    :mod:`agent_mcp.tool_cache_maintenance` -- do not conflate the two.
    """
    if override:
        return Path(override).expanduser()
    copilot_home = os.environ.get("COPILOT_HOME")
    base = Path(copilot_home).expanduser() if copilot_home else Path.home() / ".copilot"
    return base / "logs"


def _parse_timestamp(line: str) -> datetime | None:
    match = _TIMESTAMP_RE.match(line)
    if not match:
        return None
    raw = match.group(1)
    try:
        # Python's fromisoformat wants "+00:00", not a trailing "Z".
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def sweep(
    log_dir: Path,
    *,
    since_hours: float | None = 24.0,
    max_samples: int = 3,
) -> SweepResult:
    since = (
        datetime.now(timezone.utc) - timedelta(hours=since_hours)
        if since_hours is not None
        else None
    )

    signal_counts: Counter[str] = Counter()
    signal_samples: dict[str, list[str]] = {name: [] for name in _SIGNALS}
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    files_scanned = 0
    lines_scanned = 0

    for log_path in sorted(log_dir.glob("process-*.log")):
        files_scanned += 1
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    lines_scanned += 1
                    timestamp = _parse_timestamp(line)
                    if since is not None and timestamp is not None and timestamp < since:
                        continue
                    for name, pattern in _SIGNALS.items():
                        if pattern.search(line):
                            signal_counts[name] += 1
                            if len(signal_samples[name]) < max_samples:
                                signal_samples[name].append(line.rstrip("\n")[:300])
                            if timestamp is not None:
                                if name not in first_seen or timestamp < first_seen[name]:
                                    first_seen[name] = timestamp
                                if name not in last_seen or timestamp > last_seen[name]:
                                    last_seen[name] = timestamp
        except OSError:
            continue

    return SweepResult(
        log_dir=log_dir,
        files_scanned=files_scanned,
        lines_scanned=lines_scanned,
        since=since,
        signal_counts=dict(signal_counts),
        signal_samples=signal_samples,
        first_seen={k: v.isoformat() for k, v in first_seen.items()},
        last_seen={k: v.isoformat() for k, v in last_seen.items()},
    )


def health_report(
    *,
    log_dir_override: str | None = None,
    cache_dir_override: str | None = None,
    since_hours: float | None = 24.0,
) -> dict[str, Any]:
    """Combine the log sweep with a read-only tool-cache staleness snapshot
    into one health-report dict. Never mutates anything on disk.
    """
    from . import tool_cache_maintenance as tcm

    report: dict[str, Any] = {"since_hours": since_hours}

    log_dir = resolve_log_dir(log_dir_override)
    if log_dir is not None and log_dir.is_dir():
        result = sweep(log_dir, since_hours=since_hours)
        report["log_sweep"] = {
            "log_dir": str(result.log_dir),
            "files_scanned": result.files_scanned,
            "lines_scanned": result.lines_scanned,
            "since": result.since.isoformat() if result.since else None,
            "signal_counts": {name: result.signal_counts.get(name, 0) for name in _SIGNALS},
            "signal_samples": result.signal_samples,
            "first_seen": result.first_seen,
            "last_seen": result.last_seen,
        }
    else:
        report["log_sweep"] = {"log_dir": str(log_dir) if log_dir else None, "found": False}

    cache_dir = tcm.resolve_cache_dir(cache_dir_override)
    if cache_dir is not None and cache_dir.is_dir():
        scanned = tcm.scan(cache_dir)
        stale = scanned.stale_entries
        report["tool_cache"] = {
            "cache_dir": str(cache_dir),
            "found": True,
            "total_entries": len(scanned.entries),
            "current_schema_version": scanned.current_version,
            "schema_version_counts": dict(scanned.version_counts),
            "stale_entries": len(stale),
            "stale_bytes": sum(e.size for e in stale),
        }
    else:
        report["tool_cache"] = {"cache_dir": str(cache_dir) if cache_dir else None, "found": False}

    return report
