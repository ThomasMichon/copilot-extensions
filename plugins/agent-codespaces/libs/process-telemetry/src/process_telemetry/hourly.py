"""Closed, content-free service contributions; no discovery or service lifecycle."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from .measurement import OUTCOMES, STAGES

SCHEMA = "process-telemetry.hourly-contribution"
DECLARATION = "process-telemetry.hourly-reporting"
MAX_BYTES = 64 * 1024
MAX_GROUPS = 32
HOUR_MS = 3_600_000
TASK_OUTCOMES = frozenset({
    "proposed", "created", "claimed", "started", "suspended", "resumed", "released",
    "yielded", "completed", "abandoned", "detached", "submitted", "approved",
    "reopened", "reset",
})
COUNTERS = frozenset({
    "files", "bytes", "records", "observations", "malformed", "partial", "unsupported",
    "outside_hour", "future", "unavailable", "duplicates",
})
METRICS = {
    "task-lifecycle": {"unit": "count", "aggregation": "count", "scope": "observed-events"},
    "operation-duration": {"unit": "ns", "aggregation": "distribution", "scope": "observed-events"},
}
_TAG = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")


def _keys(value: object, required: Iterable[str]) -> None:
    if type(value) is not dict or set(value) != set(required):
        raise ValueError("invalid hourly fields")


def _int(value: object) -> int:
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("invalid hourly integer")
    return value


def _tag(value: object) -> str:
    if type(value) is not str or _TAG.fullmatch(value) is None:
        raise ValueError("invalid service identity")
    return value


def hour_start(value: str) -> int:
    """Accept exactly one canonical absolute UTC hour, independent of locale."""
    if type(value) is not str or len(value) != 20:
        raise ValueError("canonical UTC hour required")
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:00:00Z").replace(tzinfo=timezone.utc)
    if parsed.strftime("%Y-%m-%dT%H:00:00Z") != value:
        raise ValueError("canonical UTC hour required")
    return _int(int(parsed.timestamp()) * 1000)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value or len(key) > 4096:
            raise ValueError("duplicate hourly field")
        value[key] = item
    return value


def decode(raw: bytes) -> Any:
    """Bound and decode strict UTF-8/JSON without echoing private input."""
    if len(raw) > MAX_BYTES:
        raise ValueError("hourly output limit")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite hourly number")),
        )
        pending = [(value, 0)]
        visited = 0
        while pending:
            item, depth = pending.pop()
            visited += 1
            if depth > 8 or visited > 4096:
                raise ValueError("hourly structure limit")
            if type(item) is float and not math.isfinite(item):
                raise ValueError("nonfinite hourly number")
            if type(item) is str and len(item) > 4096:
                raise ValueError("hourly string limit")
            if type(item) is dict:
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
        return value
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid hourly JSON") from error


def declaration(value: dict[str, Any], service: str) -> dict[str, Any]:
    """Validate metadata belonging to the selected runtime, not a package version."""
    _keys(value, {"schema", "version", "service", "operation", "metrics"})
    if (
        value["schema"] != DECLARATION or type(value["version"]) is not int
        or value["version"] != 1 or value["service"] != _tag(service)
        or value["operation"] != "telemetry-hourly"
        or type(value["metrics"]) is not list or not value["metrics"]
        or any(type(metric) is not str or metric not in METRICS for metric in value["metrics"])
        or len(set(value["metrics"])) != len(value["metrics"])
    ):
        raise ValueError("unsupported hourly declaration")
    return value


def validate(
    value: dict[str, Any], *, service: str, hour: str, metrics: list[str], now_ms: int,
) -> dict[str, Any]:
    """Validate a namespaced contribution without promoting it to machine health."""
    _keys(value, {
        "schema", "version", "service", "hour", "generated_at_ms", "source",
        "coverage", "groups", "health", "diagnostics",
    })
    start = hour_start(hour)
    end = start + HOUR_MS
    generated = _int(value["generated_at_ms"])
    if (
        value["schema"] != SCHEMA or type(value["version"]) is not int
        or value["version"] != 1 or value["service"] != _tag(service)
        or value["hour"] != hour or generated < start or generated > _int(now_ms)
    ):
        raise ValueError("hourly owner or time mismatch")
    source = value["source"]
    _keys(source, {"first_ms", "last_ms", "continuity"})
    if source["continuity"] is not None:
        raise ValueError("unsupported native continuity")
    coverage = value["coverage"]
    _keys(coverage, {*COUNTERS, "state", "reason", "scan_limited", "expected_events", "producer_loss"})
    if (
        type(coverage["state"]) is not str
        or coverage["state"] not in {"observed", "partial", "unavailable", "unsupported"}
        or type(coverage["reason"]) is not str or coverage["reason"] not in {
            "retained-events", "no-observations", "capture-unavailable", "capture-unsupported",
            "native-observations", "mixed-observations",
        }
        or type(coverage["scan_limited"]) is not bool
        or coverage["expected_events"] is not None or coverage["producer_loss"] != "unknown"
    ):
        raise ValueError("invalid hourly coverage")
    for name in COUNTERS:
        _int(coverage[name])
    groups = value["groups"]
    if type(groups) is not list or len(groups) > MAX_GROUPS:
        raise ValueError("hourly group limit")
    seen = set()
    total = 0
    for group in groups:
        if type(group) is not dict:
            raise ValueError("invalid hourly group")
        metric = group.get("metric")
        if type(metric) is not str or metric not in METRICS or metric not in metrics:
            raise ValueError("unsupported hourly metric")
        duration = metric == "operation-duration"
        _keys(group, {
            "metric", "stage", "unit", "aggregation", "scope", "observations", "outcomes",
            *({"successful_durations", "p50", "p95", "maximum"} if duration else set()),
        })
        if any(group[name] != expected for name, expected in METRICS[metric].items()):
            raise ValueError("incompatible hourly units")
        stage = group["stage"]
        if type(stage) is not str or stage not in (STAGES if duration else {"lifecycle"}):
            raise ValueError("unsupported hourly stage")
        identity = metric, stage
        if identity in seen:
            raise ValueError("duplicate hourly group")
        seen.add(identity)
        count = _int(group["observations"])
        if not count or type(group["outcomes"]) is not dict or not group["outcomes"]:
            raise ValueError("invalid hourly count")
        if any(
            type(key) is not str or key not in (OUTCOMES if duration else TASK_OUTCOMES)
            for key in group["outcomes"]
        ) or sum(_int(count) for count in group["outcomes"].values()) != count:
            raise ValueError("invalid hourly outcomes")
        total += count
        if duration:
            successful = _int(group["successful_durations"])
            if successful != group["outcomes"].get("ok", 0):
                raise ValueError("invalid hourly duration population")
            values = [group[name] for name in ("p50", "p95", "maximum")]
            if successful:
                if list(map(_int, values)) != sorted(values):
                    raise ValueError("invalid hourly distribution")
            elif values != [None, None, None]:
                raise ValueError("unknown durations must remain absent")
    if total != coverage["observations"] or total > coverage["records"]:
        raise ValueError("inconsistent hourly population")
    has_evidence = bool(total or value["health"] or value["diagnostics"])
    if has_evidence:
        first, last = _int(source["first_ms"]), _int(source["last_ms"])
        if not start <= first <= last < end or last > generated:
            raise ValueError("source observations outside hour")
        if coverage["state"] not in {"observed", "partial"} or coverage["reason"] not in {
            "retained-events", "native-observations", "mixed-observations",
        }:
            raise ValueError("inconsistent hourly coverage")
    elif source["first_ms"] is not None or source["last_ms"] is not None or coverage["state"] == "observed":
        raise ValueError("missing hourly observations")
    if coverage["state"] == "observed" and (
        coverage["scan_limited"] or any(coverage[key] for key in ("malformed", "partial", "unavailable", "future"))
    ):
        raise ValueError("partial coverage cannot be observed")
    for field in ("health", "diagnostics"):
        if type(value[field]) is not list or len(value[field]) > 16:
            raise ValueError("hourly observation limit")
    for health in value["health"]:
        _keys(health, {"domain", "state", "evidence", "observed_at_ms"})
        if (
            health["domain"] != "service" or health["evidence"] != "native-owner"
            or type(health["state"]) is not str
            or health["state"] not in {"healthy", "degraded", "unavailable", "unknown"}
            or not start <= _int(health["observed_at_ms"]) < end
            or health["observed_at_ms"] > generated
            or not source["first_ms"] <= health["observed_at_ms"] <= source["last_ms"]
        ):
            raise ValueError("unsupported hourly health evidence")
    for diagnostic in value["diagnostics"]:
        _keys(diagnostic, {"component", "reason", "qualification", "first_ms", "last_ms", "count"})
        if (
            diagnostic["component"] != service
            or type(diagnostic["reason"]) is not str
            or diagnostic["reason"] not in {"operation-error", "capture-gap"}
            or type(diagnostic["qualification"]) is not str
            or diagnostic["qualification"] not in {
                "symptom", "confirmed-cause", "hypothesis", "recovery-outcome",
            }
            or not start <= _int(diagnostic["first_ms"]) <= _int(diagnostic["last_ms"]) < end
            or diagnostic["last_ms"] > generated or not _int(diagnostic["count"])
            or not source["first_ms"] <= diagnostic["first_ms"] <= diagnostic["last_ms"] <= source["last_ms"]
        ):
            raise ValueError("unsupported hourly diagnostic")
    return value
