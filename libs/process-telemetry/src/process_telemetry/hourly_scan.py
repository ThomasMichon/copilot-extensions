"""Bounded read-only retained-event reduction; the native adapter selects sources."""

from __future__ import annotations

import os
import re
import stat
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hourly import COUNTERS, HOUR_MS, MAX_GROUPS, SCHEMA, decode, hour_start
from .report import summarize

MAX_FILES = 16
MAX_ENTRIES = 256
MAX_SCAN_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 20_000
MAX_LINE = 64 * 1024
Observation = tuple[int, str, str, str, int | None]


def retained_sources(directory: Path, component: str, *, hour: str) -> tuple[list[Path], bool]:
    """Enumerate only the owner's retained namespace with bounded directory work."""
    if re.fullmatch(r"[a-z][a-z0-9_-]*", component) is None:
        raise ValueError("invalid retained component")
    pattern = re.compile(rf"{component}(?:\.\d{{8}}T\d{{12}}Z-[0-9a-f]{{32}})?\.jsonl\Z")
    start = hour_start(hour)
    active = directory / f"{component}.jsonl"
    paths: list[Path] = []
    try:
        active.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        paths.append(active)
    else:
        paths.append(active)
    archives: list[Path] = []
    limited = False
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_ENTRIES:
                limited = True
                break
            if pattern.fullmatch(entry.name):
                if entry.name == active.name:
                    if not paths:
                        paths.append(active)
                else:
                    archives.append(Path(entry.path))

    def priority(path: Path) -> tuple[int, int, str]:
        stamp = path.name[len(component) + 1:].partition("-")[0]
        try:
            rotated = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
            at_ms = int(rotated.timestamp() * 1000)
        except (ValueError, OverflowError):
            return 2, 0, path.name
        return (0, at_ms - start, path.name) if at_ms >= start else (1, -at_ms, path.name)

    paths.extend(sorted(archives, key=priority))
    return paths[:MAX_FILES], limited or len(paths) > MAX_FILES


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


def reduce(
    paths: list[Path], *, service: str, hour: str, generated_at_ms: int,
    observation: Callable[[object], Observation | None], limited: bool = False,
    unavailable: bool = False, unsupported: bool = False,
) -> dict[str, Any]:
    """Summarize only native-admitted observations, never their private labels."""
    start = hour_start(hour)
    counts = Counter({key: 0 for key in COUNTERS})
    groups: dict[tuple[str, str], list[tuple[str, int | None]]] = {}
    first = last = None
    seen = set()
    limited = limited or len(paths) > MAX_FILES
    for path in paths[:MAX_FILES]:
        descriptor = None
        try:
            before = path.lstat()
            if not _regular(before) or path.is_symlink():
                raise OSError("not a retained plain file")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            opened = os.fstat(descriptor)
            identity = opened.st_dev, opened.st_ino
            if not _regular(opened) or identity != (before.st_dev, before.st_ino):
                raise OSError("retained identity changed")
            if identity in seen:
                counts["duplicates"] += 1
                continue
            seen.add(identity)
            counts["files"] += 1
            remaining = min(opened.st_size, MAX_SCAN_BYTES - counts["bytes"])
            limited = limited or remaining < opened.st_size
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                start_offset = opened.st_size - remaining
                stream.seek(start_offset)
                data = stream.read(remaining)
                counts["bytes"] += len(data)
                if len(data) != remaining:
                    counts["partial"] += 1
                lower = 0
                if start_offset and data:
                    boundary = data.find(b"\n")
                    lower = len(data) if boundary < 0 else boundary + 1
                    counts["partial"] += 1
                cursor = len(data)
                while cursor > lower:
                    if counts["records"] >= MAX_RECORDS:
                        limited = True
                        break
                    boundary = data.rfind(b"\n", lower, cursor - 1)
                    beginning = lower if boundary < 0 else boundary + 1
                    length = cursor - beginning
                    complete = data[cursor - 1:cursor] == b"\n"
                    line = data[beginning:cursor] if length <= MAX_LINE else b""
                    cursor = beginning
                    counts["records"] += 1
                    if length > MAX_LINE:
                        counts["malformed"] += 1
                        continue
                    if not complete:
                        counts["partial"] += 1
                        continue
                    try:
                        event = decode(line)
                    except ValueError:
                        counts["malformed"] += 1
                        continue
                    item = observation(event)
                    if item is None:
                        counts["unsupported"] += 1
                        continue
                    stamp, metric, stage, outcome, duration = item
                    if stamp > generated_at_ms:
                        counts["future"] += 1
                        continue
                    if not start <= stamp < start + HOUR_MS:
                        counts["outside_hour"] += 1
                        continue
                    key = metric, stage
                    if key not in groups and len(groups) >= MAX_GROUPS:
                        limited = True
                        continue
                    groups.setdefault(key, []).append((outcome, duration))
                    counts["observations"] += 1
                    first = stamp if first is None else min(first, stamp)
                    last = stamp if last is None else max(last, stamp)
                after = os.fstat(stream.fileno())
                if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    counts["partial"] += 1
        except OSError:
            counts["unavailable"] += 1
        finally:
            if descriptor is not None:
                os.close(descriptor)
    entries = []
    for (metric, stage), values in sorted(groups.items()):
        entry: dict[str, Any] = {
            "metric": metric, "stage": stage, "unit": "count",
            "aggregation": "count", "scope": "observed-events",
            "observations": len(values), "outcomes": dict(sorted(Counter(v[0] for v in values).items())),
        }
        if metric == "operation-duration":
            stats = summarize([
                {"outcome": outcome, "duration_ns": duration, "dropped": 0}
                for outcome, duration in values
            ], expected=len(values))
            entry.update(
                unit="ns", aggregation="distribution",
                successful_durations=stats["successful"],
                p50=stats["p50_ns"], p95=stats["p95_ns"], maximum=stats["max_ns"],
            )
        entries.append(entry)
    partial = limited or unavailable or any(counts[key] for key in ("malformed", "partial", "unavailable", "future"))
    state = (
        "unsupported" if unsupported else
        "partial" if partial and (entries or counts["records"] or limited) else
        "observed" if entries else "unavailable"
    )
    reason = (
        "retained-events" if entries else "capture-unsupported" if unsupported else
        "capture-unavailable" if partial else "no-observations"
    )
    return {
        "schema": SCHEMA, "version": 1, "service": service, "hour": hour,
        "generated_at_ms": generated_at_ms,
        "source": {"first_ms": first, "last_ms": last, "continuity": None},
        "coverage": {
            **counts, "state": state, "reason": reason, "scan_limited": limited,
            "expected_events": None, "producer_loss": "unknown",
        },
        "groups": entries, "health": [], "diagnostics": [],
    }
