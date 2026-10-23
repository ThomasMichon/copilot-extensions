"""Closed source-observation signal models; no SDK or delivery side effects."""

from __future__ import annotations

import ipaddress
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
