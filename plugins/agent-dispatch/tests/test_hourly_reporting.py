from __future__ import annotations

import copy

import pytest

from agent_dispatch.hourly_reporting import METRICS, observation
from process_telemetry import hourly

HOUR = "2026-11-09T12:00:00Z"
STAMP = hourly.hour_start(HOUR) + 1000


def contribution():
    return {
        "schema": hourly.SCHEMA, "version": 1, "service": "agent-dispatch",
        "hour": HOUR, "generated_at_ms": STAMP + 1000,
        "source": {"first_ms": STAMP, "last_ms": STAMP, "continuity": None},
        "coverage": {
            **dict.fromkeys(hourly.COUNTERS, 0), "files": 1, "records": 1, "observations": 1,
            "state": "observed", "reason": "retained-events", "scan_limited": False,
            "expected_events": None, "producer_loss": "unknown",
        },
        "groups": [{
            "metric": "task-lifecycle", "stage": "lifecycle", "unit": "count",
            "aggregation": "count", "scope": "observed-events",
            "observations": 1, "outcomes": {"completed": 1},
        }],
        "health": [], "diagnostics": [],
    }


def validate(value):
    return hourly.validate(
        value, service="agent-dispatch", hour=HOUR, metrics=METRICS, now_ms=STAMP + 2000,
    )


def test_native_reduction_discards_task_identity_and_unbounded_labels():
    event = {
        "kind": "state_transition", "name": "task", "event": "task.completed", "ts": STAMP,
        "id": "private-task", "repo": "private-repository", "owner": "private-owner",
    }
    assert observation(event) == (STAMP, "task-lifecycle", "lifecycle", "completed", None)
    assert observation({**event, "event": "task.private-operation"}) is None
    assert observation({**event, "ts": True}) is None
    assert observation({**event, "schema": "unknown"}) is None


@pytest.mark.parametrize("change", [
    lambda v: v.update(service="another-service"),
    lambda v: v.update(version=True),
    lambda v: v.update(hour="2026-11-09T13:00:00Z"),
    lambda v: v.update(generated_at_ms=STAMP + 10_000),
    lambda v: v.update(extra="private-content"),
    lambda v: v["source"].update(first_ms=STAMP - hourly.HOUR_MS),
    lambda v: v["source"].update(last_ms=STAMP + 5000),
    lambda v: v["groups"][0].update(unit="ms"),
    lambda v: v["groups"][0].update(metric="arbitrary-label"),
    lambda v: v["groups"].append(copy.deepcopy(v["groups"][0])),
    lambda v: v["groups"][0].update(observations=True),
    lambda v: v["coverage"].update(observations=2),
    lambda v: v["coverage"].update(scan_limited=True),
    lambda v: v["coverage"].update(producer_loss=0),
    lambda v: v["coverage"].update(malformed=1),
])
def test_closed_schema_rejects_owner_time_vocabulary_and_population_corruption(change):
    value = contribution()
    change(value)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"value":NaN}', b"\xff", b"[" * 1500, b"x" * (hourly.MAX_BYTES + 1),
], ids=["duplicate", "nonfinite", "utf8", "depth", "oversize"])
def test_strict_decoder_rejects_partial_duplicate_nonfinite_and_oversize_data(raw):
    with pytest.raises(ValueError):
        hourly.decode(raw)


def test_delayed_generation_does_not_move_source_observations_to_the_current_hour():
    value = contribution()
    value["generated_at_ms"] += 2 * hourly.HOUR_MS
    result = hourly.validate(
        value, service="agent-dispatch", hour=HOUR, metrics=METRICS,
        now_ms=value["generated_at_ms"],
    )
    assert result["hour"] == HOUR and result["source"]["last_ms"] == STAMP
    assert result["health"] == []


@pytest.mark.parametrize("hour", ["2026-11-09T12:30:00Z", "2026-11-09T12:00:00+00:00", "2026-11-09T12:00:00"])
def test_hour_is_absolute_and_aligned(hour):
    with pytest.raises(ValueError):
        hourly.hour_start(hour)


def test_package_versions_never_substitute_for_explicit_runtime_declarations():
    metadata = {
        "schema": hourly.DECLARATION, "version": 1, "service": "agent-dispatch",
        "operation": "telemetry-hourly", "metrics": METRICS,
    }
    assert hourly.declaration(metadata, "agent-dispatch") == metadata
    for update in ({"version": 2}, {"operation": "ensure"}, {"metrics": ["private-metric"]}):
        with pytest.raises(ValueError):
            hourly.declaration({**metadata, **update}, "agent-dispatch")


def test_performance_admission_uses_completion_hour_not_start_or_emit_time():
    boundary = hourly.hour_start(HOUR) * 1_000_000
    event = {
        "schema": "agent.performance.v1", "kind": "span", "component": "agent-dispatch",
        "stage": "preparation", "outcome": "ok", "start_time_ns": boundary - 10,
        "duration_ns": 10, "ts": STAMP + hourly.HOUR_MS,
    }
    assert observation(event) == (boundary // 1_000_000, "operation-duration", "preparation", "ok", 10)
    assert observation({**event, "duration_ns": 9})[0] == boundary // 1_000_000 - 1
    for update in (
        {"duration_ns": None}, {"duration_ns": True}, {"start_time_ns": -1},
        {"start_time_ns": 2**63 - 1, "duration_ns": 1},
        {"component": "another-service"}, {"stage": "unknown"}, {"outcome": []},
    ):
        assert observation({**event, **update}) is None
