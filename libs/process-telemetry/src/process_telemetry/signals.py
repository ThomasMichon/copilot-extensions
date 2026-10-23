"""Closed source-observation signal models; no SDK or delivery side effects."""

from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

SIGNALS = ("traces", "logs", "metrics")
STAGES = frozenset({
    "source_entry", "runtime_entry", "first_refresh", "input_ack", "fixture_rows", "ui_stop",
})
BOUNDS = (.001, .005, .01, .025, .05, .1, .25, .5, 1., 2., 5., 10.)
STATUSES = frozenset({
    "accepted", "accepted_warning", "partial", "unsupported", "transport_unknown",
    "invalid_ack", "sdk_unavailable", "deadline_exhausted", "inactive", "not_requested",
})


def validate_signal_endpoint(endpoint: str, signal: str) -> str:
    if type(endpoint) is not str or signal not in SIGNALS:
        raise ValueError("invalid signal endpoint")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "http" or not parsed.hostname
        or not ipaddress.ip_address(parsed.hostname).is_loopback
        or not parsed.port or parsed.path != f"/v1/{signal}"
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or any(ord(c) <= 32 for c in endpoint)
    ):
        raise ValueError("signal endpoint must be an explicit literal loopback HTTP URL")
    return endpoint


@dataclass(frozen=True)
class SignalEndpoints:
    traces: str | None = None
    logs: str | None = None
    metrics: str | None = None

    def __post_init__(self):
        for signal in SIGNALS:
            value = getattr(self, signal)
            if value is not None:
                validate_signal_endpoint(value, signal)

    @property
    def requested(self) -> tuple[str, ...]:
        return tuple(signal for signal in SIGNALS if getattr(self, signal) is not None)


@dataclass(frozen=True)
class SignalIdentity:
    source_revision: str | None = None
    payload_version: str | None = None
    selected_version: str | None = None
    serving_version: str | None = None

    def __post_init__(self):
        for value in vars(self).values():
            if value is not None and (
                type(value) is not str or not re.fullmatch(r"[a-zA-Z0-9_.+-]{1,80}", value)
            ):
                raise ValueError("invalid signal identity")


@dataclass(frozen=True)
class SignalResult:
    status: str
    attempted_records: int = 0
    accepted_records: int = 0
    rejected_records: int = 0
    unknown_records: int = 0
    not_attempted_records: int = 0
    requests: int = 0
    attempted_payload_bytes: int = 0

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError("invalid signal status")
        if any(type(v) is not int or not 0 <= v < 2**63 for k, v in vars(self).items() if k != "status"):
            raise ValueError("invalid signal count")
        if self.attempted_records != self.accepted_records + self.rejected_records + self.unknown_records:
            raise ValueError("invalid signal outcome partition")

    @property
    def generated_records(self):
        return self.attempted_records + self.not_attempted_records


@dataclass(frozen=True)
class SignalBatchResult:
    traces: SignalResult
    logs: SignalResult
    metrics: SignalResult

    def __post_init__(self):
        if any(type(getattr(self, signal)) is not SignalResult for signal in SIGNALS):
            raise ValueError("closed signal results required")


def validate_source_records(spans: list[dict]) -> list[dict]:
    from .otlp import validate_record

    if type(spans) is not list or not 1 <= len(spans) <= 32:
        raise ValueError("signal batch requires one to 32 source records")
    try:
        records = [validate_record(span) for span in spans]
    except (TypeError, KeyError) as exc:
        raise ValueError("invalid source record") from exc
    allowed = {
        "schema", "kind", "component", "stage", "trace_id", "span_id", "parent_span_id",
        "start_time_ns", "duration_ns", "outcome", "queue_wait_ns", "roster_count",
        "source_revision", "payload_version", "runtime_version",
    }
    for record in records:
        if (
            record.keys() - allowed or record["component"] != "worktree-manager-source"
            or record["stage"] not in STAGES or record["parent_span_id"] is not None
            or record["start_time_ns"] + record["duration_ns"] >= 2**63
            or record.get("roster_count", 0) > 1_000_000
        ):
            raise ValueError("unsupported source signal record")
    if len({r["trace_id"] for r in records}) != 1:
        raise ValueError("source batch must have one trace")
    return records


def metric_points(records: list[dict]) -> list[dict]:
    """Fixed-cardinality DELTA aggregation of this observed batch, not estimates."""
    groups = {}
    for record in records:
        labels = (record["stage"], record["outcome"])
        measures = [("ce.source.observations", "{observation}", 1)]
        if record["stage"] in ("first_refresh", "input_ack") and record["outcome"] != "unavailable":
            measures.append(("ce.source.duration", "s", record["duration_ns"] / 1e9))
        if "queue_wait_ns" in record:
            measures.append(("ce.source.queue_wait", "s", record["queue_wait_ns"] / 1e9))
        for name, unit, value in measures:
            groups.setdefault((name, unit, *labels), []).append(value)
    points = []
    for (name, unit, stage, outcome), values in sorted(groups.items()):
        point = {"name": name, "unit": unit, "stage": stage, "outcome": outcome}
        if unit == "{observation}":
            point["value"] = len(values)
        else:
            buckets = [0] * (len(BOUNDS) + 1)
            for value in values:
                buckets[next((i for i, bound in enumerate(BOUNDS) if value <= bound), len(BOUNDS))] += 1
            point.update(
                count=len(values), sum=sum(values), min=min(values), max=max(values),
                bounds=list(BOUNDS), bucket_counts=buckets,
            )
        points.append(point)
    assert len(points) <= 56
    return points


def validate_metric_points(points):
    """Validate sender-side batch metadata before it can enter a run report."""
    from .measurement import OUTCOMES

    if type(points) is not list or len(points) > 56:
        raise ValueError("invalid sender metric batch")
    common = {"name", "unit", "stage", "outcome", "start_time_ns", "time_ns"}
    histogram = {"count", "sum", "min", "max", "bounds", "bucket_counts"}
    for point in points:
        if type(point) is not dict:
            raise ValueError("invalid sender metric point")
        is_sum = point.get("name") == "ce.source.observations"
        if (
            point.keys() != common | ({"value"} if is_sum else histogram)
            or point["name"] not in ("ce.source.observations", "ce.source.duration", "ce.source.queue_wait")
            or point["unit"] != ("{observation}" if is_sum else "s")
            or type(point["stage"]) is not str or point["stage"] not in STAGES
            or type(point["outcome"]) is not str or point["outcome"] not in OUTCOMES
        ):
            raise ValueError("invalid sender metric schema")
        if any(type(point[k]) is not int or not 0 <= point[k] < 2**64 for k in ("start_time_ns", "time_ns")):
            raise ValueError("invalid sender metric timestamp")
        if point["start_time_ns"] >= point["time_ns"]:
            raise ValueError("invalid sender metric interval")
        if is_sum:
            if type(point["value"]) is not int or not 1 <= point["value"] <= 32:
                raise ValueError("invalid sender metric count")
            continue
        if point["name"] == "ce.source.duration" and (
            point["stage"] not in ("first_refresh", "input_ack") or point["outcome"] == "unavailable"
        ):
            raise ValueError("unmeasured sender duration")
        if type(point["count"]) is not int or not 1 <= point["count"] <= 32:
            raise ValueError("invalid sender histogram count")
        if any(type(point[k]) not in (int, float) or not math.isfinite(point[k]) or point[k] < 0
               for k in ("sum", "min", "max")) or point["min"] > point["max"]:
            raise ValueError("invalid sender histogram measurement")
        bounds, buckets = point["bounds"], point["bucket_counts"]
        if (
            type(bounds) is not list or any(type(v) not in (int, float) for v in bounds)
            or bounds != list(BOUNDS) or type(buckets) is not list or len(buckets) != len(BOUNDS) + 1
            or any(type(v) is not int or not 0 <= v <= 32 for v in buckets)
            or sum(buckets) != point["count"]
        ):
            raise ValueError("invalid sender histogram buckets")
    return points
