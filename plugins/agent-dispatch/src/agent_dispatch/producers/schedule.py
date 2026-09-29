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
        },
        {
          "id": "agent-index-maintenance",
          "title": "Run the agent-index maintenance tick",
          "repo": "github.com/example/harness",
          "interval_seconds": 1800,
          "labels": ["agent-index-maintenance"],
          "payload_inline": "{\"path\":\"C:\\\\Users\\\\me\\\\.copilot\\\\installed-plugins\\\\copilot-extensions\\\\agent-index\\\\scripts\\\\maintenance_tick.py\"}"
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
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs

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
        "payload_ref": schedule.get("payload_ref"),
        "payload_inline": schedule.get("payload_inline"),
        "proposed": bool(schedule.get("proposed", False)),
        "requires": schedule.get("require", []),
        "affinity": schedule.get("affinity", {}),
        "labels": schedule.get("labels", []),
        "target_machine": schedule.get("target_machine"),
        "target_worktree": schedule.get("target_worktree"),
        "target_repo": schedule.get("target_repo"),
        "exclusive_key": schedule.get("exclusive_key"),
        "supersede_exclusive_key": bool(schedule.get("supersede_exclusive_key", False)),
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


def register_from_spec(client, spec: dict[str, Any]) -> dict:
    """Register every schedule in a loaded ``spec`` into the coordinator's
    registry (the migration path from a hand-edited spec file). The spec-level
    ``default_repo`` is baked into any entry lacking its own ``repo`` so each
    registered schedule is self-contained. Returns
    ``{"registered": [...], "errors": [...]}``."""
    default_repo = spec.get("default_repo")
    registered: list[dict] = []
    errors: list[dict] = []
    for entry in spec.get("schedules", []):
        resolved = dict(entry)
        if not resolved.get("repo") and default_repo:
            resolved["repo"] = default_repo
        try:
            registered.append(client.register_schedule(resolved))
        except Exception as exc:
            errors.append({"id": resolved.get("id"), "error": str(exc)})
    return {"registered": registered, "errors": errors}


def registry_spec(client) -> dict[str, Any]:
    """Assemble a :func:`run_tick` spec from the coordinator's registered,
    non-paused schedules -- so the registry is the single source of truth for
    what a tick produces (no spec file needed)."""
    return {"schedules": [rec["entry"] for rec in client.list_schedules(include_paused=False)]}


def run_registry_tick(client, now: float | None = None) -> dict:
    """:func:`run_tick` over the coordinator's registered schedules."""
    return run_tick(client, registry_spec(client), now=now)


def _parse_tick_process_output(completed: Any) -> dict[str, Any]:
    """Turn a completed ``agent-dispatch schedule tick`` subprocess into the
    same ``{"created": [...], "errors": [...]}`` shape :func:`run_tick`
    returns in-process.

    A clean run always prints a well-formed JSON object with list-valued
    ``created``/``errors`` keys. Anything else on a zero exit --
    empty/malformed/truncated stdout -- is itself a protocol violation, not
    a vacuous success; treat it as a synthetic error entry rather than
    silently normalizing it to an empty, error-free tick, which would
    otherwise lose the failure entirely. A hard failure (the coordinator
    connection itself refused, a malformed spec, ...) exits non-zero with
    no JSON on stdout -- its own diagnostic already streamed live to our
    own inherited stderr (see ``serve``'s own subprocess call), so only a
    generic, exit-code-keyed message is synthesized here.
    """
    try:
        parsed = json.loads(completed.stdout) if completed.stdout else None
    except ValueError:
        parsed = None
    if (
        isinstance(parsed, dict)
        and isinstance(parsed.get("created"), list)
        and isinstance(parsed.get("errors"), list)
    ):
        return {"created": parsed["created"], "errors": parsed["errors"]}
    return {
        "created": [],
        "errors": [
            {
                "error": (
                    "schedule tick produced no valid JSON result "
                    f"(exit {completed.returncode})"
                )
            }
        ],
    }


def serve(
    spec_path: str | Path,
    *,
    cli_argv: list[str] | None = None,
    interval: float = 60.0,
    on_tick=None,
    sleep: Callable[[float], None] = time.sleep,
    runner: Callable[..., Any] = subprocess.run,
) -> None:
    """Built-in timer: run ``agent-dispatch schedule tick <spec>`` in a fresh
    subprocess every ``interval`` seconds until interrupted. The spec is
    re-read (by that fresh process) each tick, so edits take effect without
    a restart of this loop. ``on_tick(result)`` is called with each tick's
    result (defaults to a compact stderr line).

    Like :func:`agent_dispatch.producers.emitter.serve`, this loop never
    builds or holds a :class:`DispatchClient` in-process across the sleep
    boundary -- the coordinator's address is an internal, invocation-time
    detail of ``agent-dispatch`` itself, and re-invoking its own CLI per
    tick gets that discovery for free instead of this loop having to know
    how to recover from a coordinator restart onto a new ephemeral port.
    ``cli_argv`` is the ``agent-dispatch`` argv prefix
    to reuse for every tick (e.g. including ``--shared``/``--url``/
    ``--token`` when the parent invocation targeted them) -- defaults to
    ``[sys.executable, "-m", "agent_dispatch"]``.
    """
    argv = list(cli_argv) if cli_argv is not None else [sys.executable, "-m", "agent_dispatch"]
    tick_argv = [*argv, "schedule", "tick", str(spec_path)]

    def _default_on_tick(result: dict) -> None:
        print(
            f"agent-dispatch schedule: created={len(result['created'])} "
            f"errors={len(result['errors'])}",
            file=sys.stderr,
        )

    on_tick = on_tick or _default_on_tick
    while True:
        try:
            # No local spec read here -- the forked ``schedule tick``
            # subprocess loads (and re-validates) the spec itself on every
            # invocation, so an edit takes effect on the very next tick with
            # no separate re-read needed in this wrapper.
            #
            # Only stdout is piped (captured for JSON parsing) -- stderr is
            # deliberately left to inherit ours directly (real-time OS-level
            # streaming, no Python-side buffering), so a malformed-spec
            # traceback or any other diagnostic the tick process writes
            # there flows straight through, exactly matching the
            # previously-inherited passthrough behavior.
            completed = runner(
                tick_argv,
                stdout=subprocess.PIPE,
                text=True,
                check=False,
                **no_window_kwargs(),
            )
            result = _parse_tick_process_output(completed)
            on_tick(result)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"agent-dispatch schedule: tick failed: {exc}", file=sys.stderr)
        try:
            sleep(interval)
        except KeyboardInterrupt:
            return


def serve_registry(
    *,
    url: str | None = None,
    token: str | None = None,
    interval: float = 60.0,
    lease_scope: str,
    holder: str,
    holder_session: str | None = None,
    lease_ttl: float | None = None,
    on_tick=None,
    resolve_target: Callable[[], tuple[str, str | None]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Lease-gated registry timer -- the fleet chronicler's producer loop.

    Every ``interval`` seconds this attempts to acquire/renew the job-lease for
    ``lease_scope`` as ``holder`` (pin-not-failover) and, **only while it holds
    the lease**, ticks the coordinator's registered schedules. A machine that
    does not hold the lease idles. So N machines may run this identical loop and
    exactly one (the lease holder -- e.g. the designated chronicler host)
    produces occurrences; if that host sleeps, ticking simply pauses and
    resumes on wake, with the schedules' own lookback windows replaying any
    just-missed occurrences (idempotent via ``dedup_key``). No wall-clock
    takeover ever moves the lease to another host.

    ``resolve_target`` re-derives ``(url, token)`` fresh at the start of every
    tick, same rationale as :func:`serve` above.
    """
    import sys

    if resolve_target is None and url is None:
        raise ValueError("serve_registry() requires either 'url' or 'resolve_target'")

    def _default_on_tick(result: dict) -> None:
        if result.get("held"):
            print(
                f"agent-dispatch schedule[registry]: held created="
                f"{len(result.get('created', []))} errors={len(result.get('errors', []))}",
                file=sys.stderr,
            )
        else:
            print(
                f"agent-dispatch schedule[registry]: lease {lease_scope!r} held by "
                f"{result.get('lease', {}).get('holder')!r} -- idling",
                file=sys.stderr,
            )

    on_tick = on_tick or _default_on_tick
    while True:
        try:
            tick_url, tick_token = (
                resolve_target() if resolve_target is not None else (url, token)
            )
            with DispatchClient(tick_url, token=tick_token) as client:
                lease = client.acquire_schedule_lease(
                    lease_scope,
                    holder,
                    holder_session=holder_session,
                    ttl=lease_ttl,
                )
                if lease.get("granted"):
                    result = run_registry_tick(client)
                    on_tick({"held": True, "lease": lease.get("lease"), **result})
                else:
                    on_tick({"held": False, "lease": lease.get("lease")})
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"agent-dispatch schedule[registry]: tick failed: {exc}", file=sys.stderr)
        try:
            sleep(interval)
        except KeyboardInterrupt:
            return
