"""Scheduler / timer producer -- recurring tasks from a declarative spec.

A schedule spec is a JSON document listing recurring task templates. Each
*tick* computes which occurrences of each schedule fall due (an interval
cadence, or one or more daily wall-clock times), and enqueues one task per
occurrence with:

* ``not_before`` set to the occurrence time (the coordinator won't let a
  worker claim it until then -- the deferral gate already in the engine), and
* a deterministic ``dedup_key`` of ``sched:<id>:<occurrence-epoch>`` so
  re-ticking (or overlapping windows) never double-creates an occurrence.

Because every occurrence is idempotent, you can drive ``tick`` as often as you
like from any external timer (cron, a systemd timer, ``manage_schedule``), or
use the built-in :func:`serve` loop -- the "timer producer" -- when you'd
rather not wire an external one.

Spec shape (JSON)::

    {
      "default_repo": "example.com/acme/widget",
      "schedules": [
        {
          "id": "nightly-digest",
          "title": "Generate the nightly digest",
          "prompt": "Summarize the day's merged work.",
          "repo": "example.com/acme/widget",
          "at": ["09:00", "17:00"],
          "require": ["logger"],
          "labels": ["scheduled"],
          "source": "schedule"
        },
        {
          "id": "hourly-health",
          "title": "Sweep service health",
          "interval_seconds": 3600
        }
      ]
    }

A schedule uses **either** ``interval_seconds`` **or** ``at`` (a list of
``"HH:MM"`` local times), not both. ``repo`` (the lane) falls back to the
spec-level ``default_repo``; a schedule with no resolvable lane is reported as
an error and skipped (tasks are always lane-scoped).
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..client import DispatchClient

# Default windows: how far ahead an occurrence is pre-created (so a deferred
# task is queued before it's due) and how far back a just-missed occurrence is
# still created (to survive brief producer downtime). Overridable per schedule.
_DEFAULT_INTERVAL_LOOKBACK = 3600.0
_DEFAULT_DAILY_HORIZON = 86400.0
_DEFAULT_DAILY_LOOKBACK = 3600.0
_MAX_OCCURRENCES_PER_TICK = 512  # safety cap against a tiny interval + huge horizon


class ScheduleError(ValueError):
    """A malformed schedule entry or spec."""


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load and validate a schedule spec file."""
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("schedules"), list):
        raise ScheduleError("spec must be an object with a 'schedules' list")
    return data


def _interval_occurrences(
    interval: float, now: float, horizon: float, lookback: float
) -> list[float]:
    """Occurrence epochs on an ``interval`` cadence anchored at epoch 0, within
    ``[now - lookback, now + horizon]``."""
    if interval <= 0:
        raise ScheduleError("interval_seconds must be > 0")
    start, end = now - lookback, now + horizon
    out: list[float] = []
    k = math.ceil(start / interval)
    while k * interval <= end and len(out) < _MAX_OCCURRENCES_PER_TICK:
        out.append(float(k * interval))
        k += 1
    return out


def _daily_occurrences(
    times: list[str], now: float, horizon: float, lookback: float
) -> list[float]:
    """Occurrence epochs for each ``"HH:MM"`` local time, within
    ``[now - lookback, now + horizon]``. Yesterday/today/tomorrow are all
    considered so a window crossing midnight is covered."""
    base = datetime.fromtimestamp(now).astimezone()
    out: list[float] = []
    for hhmm in times:
        try:
            hour, minute = (int(part) for part in hhmm.split(":", 1))
        except ValueError as exc:
            raise ScheduleError(f"invalid time {hhmm!r} (want 'HH:MM')") from exc
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ScheduleError(f"time {hhmm!r} out of range")
        for day_offset in (-1, 0, 1):
            occ = (base + timedelta(days=day_offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            ts = occ.timestamp()
            if now - lookback <= ts <= now + horizon:
                out.append(ts)
    return sorted(set(out))


def due_occurrences(schedule: dict[str, Any], now: float) -> list[float]:
    """The occurrence epochs of one schedule that are due as of ``now``."""
    has_interval = "interval_seconds" in schedule
    has_daily = "at" in schedule
    if has_interval == has_daily:
        raise ScheduleError(
            f"schedule {schedule.get('id')!r} needs exactly one of "
            "'interval_seconds' or 'at'"
        )
    if has_interval:
        interval = float(schedule["interval_seconds"])
        horizon = float(schedule.get("horizon_seconds", interval))
        lookback = float(
            schedule.get("lookback_seconds", min(interval, _DEFAULT_INTERVAL_LOOKBACK))
        )
        return _interval_occurrences(interval, now, horizon, lookback)
    times = schedule["at"]
    if not isinstance(times, list) or not times:
        raise ScheduleError(f"schedule {schedule.get('id')!r} 'at' must be a non-empty list")
    horizon = float(schedule.get("horizon_seconds", _DEFAULT_DAILY_HORIZON))
    lookback = float(schedule.get("lookback_seconds", _DEFAULT_DAILY_LOOKBACK))
    return _daily_occurrences(times, now, horizon, lookback)


def _create_kwargs(schedule: dict[str, Any], repo: str, occ: float) -> dict[str, Any]:
    """The ``DispatchClient.create`` kwargs for one occurrence of a schedule."""
    return {
        "repo": repo,
        "prompt": schedule.get("prompt", ""),
        "proposed": bool(schedule.get("proposed", False)),
        "requires": schedule.get("require", []),
        "affinity": schedule.get("affinity", {}),
        "labels": schedule.get("labels", []),
        "target_machine": schedule.get("target_machine"),
        "target_worktree": schedule.get("target_worktree"),
        "target_repo": schedule.get("target_repo"),
        "source": schedule.get("source", "schedule"),
        "origin_ref": f"schedule/{schedule['id']}",
        "dedup_key": f"sched:{schedule['id']}:{int(occ)}",
        "not_before": occ,
    }


def run_tick(client: DispatchClient, spec: dict[str, Any], now: float | None = None) -> dict:
    """Create every due occurrence in ``spec``, once (idempotent via dedup_key).

    Returns ``{"created": [...], "errors": [...]}``. ``created`` holds the task
    snapshots the coordinator returned (a duplicate ``dedup_key`` returns the
    existing task, so re-ticks are safe and simply re-report the same rows).
    """
    now = time.time() if now is None else now
    default_repo = spec.get("default_repo")
    created: list[dict] = []
    errors: list[dict] = []
    for schedule in spec.get("schedules", []):
        sid = schedule.get("id")
        if not sid or "title" not in schedule:
            errors.append({"id": sid, "error": "schedule needs an 'id' and a 'title'"})
            continue
        repo = schedule.get("repo") or default_repo
        if not repo:
            errors.append({"id": sid, "error": "no repo (lane): set 'repo' or 'default_repo'"})
            continue
        try:
            occurrences = due_occurrences(schedule, now)
        except ScheduleError as exc:
            errors.append({"id": sid, "error": str(exc)})
            continue
        for occ in occurrences:
            try:
                task = client.create(schedule["title"], **_create_kwargs(schedule, repo, occ))
            except Exception as exc:
                errors.append({"id": sid, "not_before": occ, "error": str(exc)})
                continue
            created.append(task)
    return {"created": created, "errors": errors}


def serve(
    spec_path: str | Path,
    *,
    url: str,
    token: str | None = None,
    interval: float = 60.0,
    on_tick=None,
) -> None:
    """Built-in timer: reload the spec and :func:`run_tick` every ``interval``
    seconds until interrupted. The spec is re-read each tick so edits take
    effect without a restart. ``on_tick(result)`` is called with each tick's
    result (defaults to a compact stderr line)."""
    import sys

    def _default_on_tick(result: dict) -> None:
        print(
            f"agent-dispatch schedule: created={len(result['created'])} "
            f"errors={len(result['errors'])}",
            file=sys.stderr,
        )

    on_tick = on_tick or _default_on_tick
    while True:
        try:
            spec = load_spec(spec_path)
            with DispatchClient(url, token=token) as client:
                result = run_tick(client, spec)
            on_tick(result)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"agent-dispatch schedule: tick failed: {exc}", file=sys.stderr)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return
