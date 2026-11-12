"""Bounded, content-free hourly projections of existing local telemetry."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import BinaryIO

from process_telemetry.measurement import OUTCOMES, STAGES
from process_telemetry.report import summarize

HOURS = 168
MAX_INPUTS = 16
MAX_BYTES = 8 * 1024 * 1024
MAX_LINE = 64 * 1024
MAX_RECORDS = 20_000
MAX_GROUPS = 32
_HOUR_NS = 3_600_000_000_000
_TAG = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_TASK_EVENTS = frozenset({
    "proposed", "created", "claimed", "started", "suspended", "resumed",
    "released", "yielded", "completed", "abandoned", "detached",
    "submitted", "approved", "reopened", "reset",
})
_BRIDGE_TRACE_TARGETS = {
    "conversation": {
        "user_message": {"sending"},
        "agent_message": {"responding"},
        "agent_thought": {"responding"},
        "session_state_changed": {"responding", "idle", "stopped", "ended", "error"},
        "turn_complete": {"end-turn", "cancelled", "idle"},
        "error": {"error"},
    },
    "tool_call": {
        "tool_call": {"running"},
        "tool_call_start": {"running"},
        "tool_call_update": {"completed", "cancelled", "error"},
        "tool_call_progress": {"completed", "cancelled", "error"},
        "session_state_changed": {"completed", "cancelled", "error"},
        "turn_complete": {"completed", "cancelled"},
        "error": {"error"},
    },
}
_BRIDGE_TRACE_STATES = {
    name: frozenset(state for targets in events.values() for state in targets)
    for name, events in _BRIDGE_TRACE_TARGETS.items()
}


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite value")
    return result


def _constant(_value: str):
    raise ValueError("nonfinite value")


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _bridge_trace(record: dict, stamp: int):
    """Project producer state labels, never infer latency or a work verdict."""
    name, event, target, prior = (
        record.get("name"), record.get("event"), record.get("to"), record.get("from"),
    )
    if not all(isinstance(value, str) for value in (name, event, target)):
        return None
    events = _BRIDGE_TRACE_TARGETS.get(name)
    if events is None or target not in events.get(event, ()):
        return None
    if prior is not None and (
        not isinstance(prior, str) or prior not in _BRIDGE_TRACE_STATES[name]
    ):
        return None
    if name == "tool_call" and target != "running" and record.get("tool_status") != target:
        return None
    group = "bridge-conversation" if name == "conversation" else "bridge-tool"
    return stamp * 1_000_000, (group,), target, None


def _observation(record: object):
    if not isinstance(record, dict):
        return None
    if record.get("schema") == "agent.performance.v1":
        component, stage = record.get("component"), record.get("stage")
        duration, started, outcome = (
            record.get("duration_ns"), record.get("start_time_ns"), record.get("outcome"),
        )
        if (
            record.get("kind") != "span" or not isinstance(component, str)
            or not _TAG.fullmatch(component) or not isinstance(stage, str)
            or stage not in STAGES or not isinstance(outcome, str) or outcome not in OUTCOMES
            or not _integer(duration) or not _integer(started)
            or started + duration >= 2**63
        ):
            return None
        return started + duration, ("performance", component, stage), outcome, duration
    if "schema" in record:
        return None
    stamp = record.get("ts")
    if not _integer(stamp) or stamp >= 2**63 // 1_000_000:
        return None
    kind, name, event = record.get("kind"), record.get("name"), record.get("event")
    if kind == "state_transition":
        trace = _bridge_trace(record, stamp)
        if trace is not None:
            return trace
    if kind == "state_transition" and name == "session" and event == "session_state_changed":
        return stamp * 1_000_000, ("session",), "transition", None
    if kind == "error" and name == "session_health" and event in ("connect_failed", "error"):
        return stamp * 1_000_000, ("session",), "error", None
    if kind == "event" and name == "session_health" and event in (
        "context_warning", "context_critical",
    ):
        return stamp * 1_000_000, ("session",), "warning", None
    if (
        kind == "state_transition" and name == "task" and isinstance(event, str)
        and event.startswith("task.") and event[5:] in _TASK_EVENTS
    ):
        return stamp * 1_000_000, ("task",), event[5:], None
    return None


class HourlyReport:
    """One bounded scan; unknown producer population never becomes uptime."""

    def __init__(self, now: datetime):
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("an aware observation time is required")
        self.now = now.astimezone(timezone.utc)
        seconds = int(self.now.timestamp())
        self.now_ns = seconds * 1_000_000_000 + self.now.microsecond * 1000
        self.last_hour = self.now_ns // _HOUR_NS
        self.first_hour = self.last_hour - HOURS + 1
        self.coverage = Counter()
        self.limited = False
        self.groups: set[tuple[str, ...]] = set()
        self.buckets: dict[int, dict[tuple[str, ...], list]] = {}

    def unavailable(self) -> None:
        self.coverage["unavailable_inputs"] += 1

    def scan(self, stream: BinaryIO, size: int) -> None:
        self.coverage["opened_inputs"] += 1
        remaining = min(size, MAX_BYTES - self.coverage["bytes_read"])
        if remaining < size:
            self.limited = True
        while remaining:
            if self.coverage["records_seen"] >= MAX_RECORDS:
                self.limited = True
                return
            line = stream.readline(min(MAX_LINE + 1, remaining))
            if not line:
                self.coverage["changed_inputs"] += 1
                return
            remaining -= len(line)
            self.coverage["bytes_read"] += len(line)
            self.coverage["records_seen"] += 1
            if len(line) > MAX_LINE:
                self.coverage["oversized_records"] += 1
                while remaining and not line.endswith(b"\n"):
                    line = stream.readline(min(MAX_LINE, remaining))
                    if not line:
                        break
                    remaining -= len(line)
                    self.coverage["bytes_read"] += len(line)
                continue
            if not line.endswith(b"\n"):
                self.coverage["partial_records"] += 1
                continue
            try:
                record = json.loads(
                    line, object_pairs_hook=_unique, parse_float=_float,
                    parse_constant=_constant,
                )
            except (ValueError, UnicodeError, RecursionError):
                self.coverage["malformed_records"] += 1
                continue
            observation = _observation(record)
            if observation is None:
                self.coverage["unsupported_records"] += 1
                continue
            stamp, group, outcome, duration = observation
            hour = stamp // _HOUR_NS
            if stamp > self.now_ns:
                self.coverage["future_records"] += 1
                continue
            if hour < self.first_hour:
                self.coverage["expired_records"] += 1
                continue
            if group not in self.groups and len(self.groups) >= MAX_GROUPS:
                self.coverage["unrepresented_records"] += 1
                self.limited = True
                continue
            self.groups.add(group)
            values = self.buckets.setdefault(hour, {}).setdefault(group, [])
            values.append((outcome, duration))
            self.coverage["accepted_records"] += 1

    def result(self, declared_inputs: int) -> dict:
        hours = []
        for hour in range(self.first_hour, self.last_hour + 1):
            entries = []
            for group, values in sorted(self.buckets.get(hour, {}).items()):
                outcomes = dict(sorted(Counter(value[0] for value in values).items()))
                entry = {"kind": group[0], "observations": len(values), "outcomes": outcomes}
                if group[0] == "performance":
                    timing = summarize([
                        {"outcome": outcome, "duration_ns": duration, "dropped": 0}
                        for outcome, duration in values
                    ], expected=len(values))
                    entry.update(
                        component=group[1], stage=group[2],
                        successful_durations=timing["successful"],
                        p50_ns=timing["p50_ns"], p95_ns=timing["p95_ns"],
                        max_ns=timing["max_ns"],
                    )
                entries.append(entry)
            hours.append({
                "hour": datetime.fromtimestamp(hour * 3600, timezone.utc).isoformat(),
                "state": "observed" if entries else "no-observation",
                "groups": entries,
            })
        fields = (
            "opened_inputs", "unavailable_inputs", "duplicate_inputs", "changed_inputs", "bytes_read",
            "records_seen", "accepted_records", "malformed_records", "partial_records",
            "oversized_records", "unsupported_records", "future_records",
            "expired_records", "unrepresented_records",
        )
        return {
            "schema": "agent-machines.health-report.v1",
            "generated_at": self.now.isoformat(),
            "hours": hours,
            "coverage": {
                **{field: self.coverage[field] for field in fields},
                "declared_inputs": declared_inputs,
                "scan_limited": self.limited,
                "expected_events": None,
                "producer_loss": "unknown",
                "population": "observed-only",
                "availability": "not-inferred",
                "p99_qualified": False,
            },
        }
