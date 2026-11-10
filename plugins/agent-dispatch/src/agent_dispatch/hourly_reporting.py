"""Dispatch-owned privacy reduction over its existing retained telemetry."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone

from process_telemetry.configured_spool import retained_directory
from process_telemetry.hourly import TASK_OUTCOMES, decode, hour_start, validate
from process_telemetry.hourly_scan import reduce, retained_sources
from process_telemetry.measurement import OUTCOMES, STAGES

from .telemetry import SINK_ENV_VAR, _default_config_path

SERVICE = "agent-dispatch"
METRICS = ["task-lifecycle", "operation-duration"]


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def observation(event):
    if type(event) is not dict:
        return None
    if event.get("schema") == "agent.performance.v1":
        start, duration = event.get("start_time_ns"), event.get("duration_ns")
        stage, outcome = event.get("stage"), event.get("outcome")
        if (
            event.get("kind") == "span" and event.get("component") == SERVICE
            and type(stage) is str and stage in STAGES
            and type(outcome) is str and outcome in OUTCOMES
            and _integer(start) and _integer(duration) and start + duration < 2**63
        ):
            return (start + duration) // 1_000_000, "operation-duration", stage, outcome, duration
        return None
    stamp, event_type = event.get("ts"), event.get("event")
    if (
        "schema" not in event and event.get("kind") == "state_transition"
        and event.get("name") == "task" and _integer(stamp)
        and type(event_type) is str and event_type.startswith("task.")
        and event_type[5:] in TASK_OUTCOMES
    ):
        return stamp, "task-lifecycle", "lifecycle", event_type[5:], None
    return None


def export(hour: str) -> dict:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if hour_start(hour) > now_ms:
        raise ValueError("future hour")
    paths, limited, unavailable, unsupported = [], False, False, False
    try:
        with _default_config_path().open("rb") as stream:
            config = decode(stream.read(64 * 1024 + 1))
        if os.environ.get(SINK_ENV_VAR) or type(config) is not dict or "retained_spool" not in config:
            unsupported = True
        else:
            directory = retained_directory(config) / "retained"
            info = directory.lstat()
            if (
                directory.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise OSError("retained namespace unavailable")
            paths, limited = retained_sources(directory, "agent-dispatch-telemetry", hour=hour)
    except FileNotFoundError:
        unavailable = True
    except (ValueError, KeyError, TypeError, OSError):
        unavailable = True
    result = reduce(
        paths, service=SERVICE, hour=hour, generated_at_ms=now_ms, observation=observation,
        limited=limited, unavailable=unavailable, unsupported=unsupported,
    )
    return validate(result, service=SERVICE, hour=hour, metrics=METRICS, now_ms=now_ms)


def run(args) -> int:
    try:
        result = export(args.hour)
    except (ValueError, OSError, OverflowError):
        print('{"error":"hourly-export-unavailable"}')
        return 2
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0
