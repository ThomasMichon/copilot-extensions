from __future__ import annotations

import io
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import pytest

from agent_machines import health_chart
from agent_machines import health_report as health

NOW = datetime(2026, 11, 5, 12, 30, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp()) * 1_000_000_000


def span(*, duration=20, outcome="ok", component="example", **extra):
    return {
        "schema": "agent.performance.v1", "kind": "span", "component": component,
        "stage": "preparation", "start_time_ns": STAMP - 1_000_000_000,
        "duration_ns": duration, "outcome": outcome, **extra,
    }


def report(records, *, now=NOW):
    raw = b"".join(json.dumps(record).encode() + b"\n" for record in records)
    builder = health.HourlyReport(now)
    builder.scan(io.BytesIO(raw), len(raw))
    return builder.result(1)


def native_bridge_records(events, *, stamp=STAMP // 1_000_000):
    source = (
        Path(__file__).resolve().parents[3] / "plugins" / "agent-bridge"
        / "src" / "agent_bridge" / "telemetry.py"
    )
    spec = importlib.util.spec_from_file_location("health_report_bridge_producer", source)
    assert spec and spec.loader
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    reducer = producer.SessionTraceReducer(
        "not-output-session", worktree_id="not-output-worktree", source="represented",
    )
    records = []
    for event_id, (event, data) in enumerate(events, 1):
        for record in reducer.observe(event, data, event_id=event_id):
            # The persisted sink adds the timestamp after reduction.
            records.append({**record, "ts": stamp})
    return records


def test_actual_bridge_native_transitions_are_separate_counts_without_latency_or_identifiers():
    records = native_bridge_records([
        ("user_message", {"prompt": "not-output"}),
        ("agent_message", {"text": "not-output"}),
        ("tool_call_start", {"tool_call_id": "not-output-tool"}),
        ("tool_call_update", {"tool_call_id": "not-output-tool", "status": "failed"}),
        ("turn_complete", {"stop_reason": "end_turn"}),
    ])
    result = report(records)
    assert result["coverage"]["accepted_records"] == len(records)
    groups = {group["kind"]: group for group in result["hours"][-1]["groups"]}
    assert groups["bridge-conversation"]["outcomes"] == {
        "sending": 1, "responding": 1, "end-turn": 1, "idle": 1,
    }
    assert groups["bridge-tool"]["outcomes"] == {"running": 1, "error": 1}
    assert not any("p95_ns" in group or "successful_durations" in group for group in groups.values())
    assert "not-output" not in json.dumps(result)
    assert result["coverage"]["expected_events"] is None


def test_actual_bridge_cancellation_remains_a_transition_not_a_transport_error():
    result = report(native_bridge_records([
        ("user_message", {}),
        ("tool_call_start", {"tool_call_id": "fixture-tool"}),
        ("turn_complete", {"stop_reason": "interrupted"}),
    ]))
    groups = {group["kind"]: group for group in result["hours"][-1]["groups"]}
    assert groups["bridge-conversation"]["outcomes"]["cancelled"] == 1
    assert groups["bridge-tool"]["outcomes"]["cancelled"] == 1
    assert all("error" not in group["outcomes"] for group in groups.values())


@pytest.mark.parametrize("change", [
    {"event": "future-event"},
    {"to": "private-unknown-state"},
    {"event": "user_message", "to": "error"},
    {"to": []},
    {"from": {}},
    {"schema": "future.v2"},
])
def test_native_transition_refusals_do_not_erase_independent_valid_records(change):
    valid = {
        "kind": "state_transition", "name": "conversation", "event": "turn_complete",
        "to": "idle", "ts": STAMP // 1_000_000,
    }
    result = report([valid, {**valid, **change}])
    assert result["coverage"]["accepted_records"] == 1
    assert result["coverage"]["unsupported_records"] == 1


def test_real_schema_families_keep_counts_durations_and_absence_distinct():
    result = report([
        span(duration=10), span(duration=30), span(outcome="error"),
        {"kind": "error", "name": "session_health", "event": "connect_failed",
         "ts": STAMP // 1_000_000, "session_id": "not-output"},
        {"kind": "state_transition", "name": "task", "event": "task.abandoned",
         "ts": STAMP // 1_000_000, "payload": "not-output"},
    ])
    assert len(result["hours"]) == 168
    assert all(hour["state"] == "no-observation" for hour in result["hours"][:-1])
    groups = result["hours"][-1]["groups"]
    timing = next(group for group in groups if group["kind"] == "performance")
    assert timing["observations"] == 3
    assert timing["successful_durations"] == 2
    assert (timing["p50_ns"], timing["p95_ns"], timing["max_ns"]) == (10, 30, 30)
    assert next(group for group in groups if group["kind"] == "session")["outcomes"] == {
        "error": 1,
    }
    task = next(group for group in groups if group["kind"] == "task")
    assert task["outcomes"] == {"abandoned": 1}
    assert "p95_ns" not in task
    assert "not-output" not in json.dumps(result)
    assert result["coverage"]["expected_events"] is None
    assert result["coverage"]["producer_loss"] == "unknown"
    assert result["coverage"]["p99_qualified"] is False


def test_absolute_hour_window_and_source_completion_time_not_file_time():
    first = NOW.replace(minute=0) - timedelta(hours=167)
    first_ns = int(first.timestamp()) * 1_000_000_000
    result = report([
        span(start_time_ns=first_ns - 1, duration=1),
        span(start_time_ns=first_ns - 2, duration=1),
        span(start_time_ns=STAMP, duration=1),
        span(start_time_ns=STAMP - 1, duration=1),
    ])
    assert result["hours"][0]["state"] == "observed"
    assert result["hours"][-1]["state"] == "observed"
    assert result["coverage"]["expired_records"] == 1
    assert result["coverage"]["future_records"] == 1
    assert result["coverage"]["accepted_records"] == 2


@pytest.mark.parametrize("line", [
    b'{"kind":"event","kind":"error"}\n',
    b'{"value":NaN}\n',
    b'{"value":1e999}\n',
    b'\xff\n',
])
def test_malformed_records_are_counted_without_echoing_content(line):
    builder = health.HourlyReport(NOW)
    builder.scan(io.BytesIO(line), len(line))
    result = builder.result(1)
    assert result["coverage"]["malformed_records"] == 1
    assert result["coverage"]["accepted_records"] == 0


def test_missing_duration_unknown_schema_wrong_types_and_partial_tail_are_not_zeroes():
    records = [
        span(duration=None), span(outcome=[]),
        span(component="<script>"),
        {"schema": "future.v2", "kind": "state_transition", "name": "task",
         "event": "task.completed", "ts": STAMP // 1_000_000},
        {"kind": "state_transition", "name": "task", "event": "task.completed", "ts": True},
    ]
    result = report(records)
    assert result["coverage"]["unsupported_records"] == len(records)
    assert all(hour["state"] == "no-observation" for hour in result["hours"])
    builder = health.HourlyReport(NOW)
    line = json.dumps(span()).encode()
    builder.scan(io.BytesIO(line), len(line))
    assert builder.result(1)["coverage"]["partial_records"] == 1


def test_record_byte_and_group_limits_remain_explicit(monkeypatch):
    raw = json.dumps(span()).encode() + b"\n"
    monkeypatch.setattr(health, "MAX_RECORDS", 1)
    builder = health.HourlyReport(NOW)
    builder.scan(io.BytesIO(raw * 2), len(raw) * 2)
    assert builder.result(1)["coverage"]["scan_limited"] is True
    assert builder.result(1)["coverage"]["records_seen"] == 1
    monkeypatch.setattr(health, "MAX_RECORDS", 100)
    monkeypatch.setattr(health, "MAX_BYTES", len(raw) - 1)
    builder = health.HourlyReport(NOW)
    builder.scan(io.BytesIO(raw), len(raw))
    assert builder.result(1)["coverage"]["bytes_read"] == len(raw) - 1
    assert builder.result(1)["coverage"]["scan_limited"] is True
    monkeypatch.setattr(health, "MAX_BYTES", 10000)
    monkeypatch.setattr(health, "MAX_GROUPS", 1)
    result = report([span(), span(component="other")])
    assert result["coverage"]["accepted_records"] == 1
    assert result["coverage"]["unrepresented_records"] == 1


def test_oversized_record_suffix_cannot_be_parsed_as_another_event(monkeypatch):
    monkeypatch.setattr(health, "MAX_LINE", 256)
    raw = b"x" * 300 + json.dumps(span()).encode() + b"\n" + json.dumps(span()).encode() + b"\n"
    builder = health.HourlyReport(NOW)
    builder.scan(io.BytesIO(raw), len(raw))
    result = builder.result(1)
    assert result["coverage"]["oversized_records"] == 1
    assert result["coverage"]["accepted_records"] == 1
    assert result["coverage"]["records_seen"] == 2


def test_static_chart_separates_latency_groups_and_does_not_bridge_empty_hours():
    result = report([
        span(duration=0),
        span(start_time_ns=STAMP - 2 * health._HOUR_NS, component="example"),
        span(duration=90, component="other"),
    ])
    svg = health_chart.render(result)
    document = ElementTree.fromstring(svg)
    assert document.tag.endswith("svg")
    assert "example/preparation" in svg and "other/preparation" in svg
    assert svg.count("<circle ") == 3
    assert "<script" not in svg and "href=" not in svg
    assert "No p99 or uptime claim" in svg
    assert "Scan limited: false" in svg


def test_observation_time_requires_timezone_and_normalizes_to_utc():
    with pytest.raises(ValueError):
        health.HourlyReport(NOW.replace(tzinfo=None))
    equivalent = NOW.astimezone(timezone(timedelta(hours=3)))
    assert report([], now=equivalent)["generated_at"] == NOW.isoformat()


def test_truncated_snapshot_is_reported_without_reopening_or_inventing_records():
    raw = json.dumps(span()).encode() + b"\n"
    builder = health.HourlyReport(NOW)
    builder.scan(io.BytesIO(raw), len(raw) * 2)
    result = builder.result(1)
    assert result["coverage"]["changed_inputs"] == 1
    assert result["coverage"]["accepted_records"] == 1
    assert result["coverage"]["producer_loss"] == "unknown"


def test_standalone_chart_distinguishes_invalid_and_partial_input_from_absence():
    builder = health.HourlyReport(NOW)
    raw = b"{bad-json}\n" + json.dumps(span()).encode()
    builder.scan(io.BytesIO(raw), len(raw))
    result = builder.result(1)
    result["coverage"].update(oversized_records=2, unsupported_records=3, changed_inputs=4)
    chart = health_chart.render(result)
    assert chart != health_chart.render(report([]))
    assert "malformed 1, partial 1, oversized 2, unsupported 3; changed inputs 4" in chart
    ElementTree.fromstring(chart)


@pytest.mark.parametrize("event", ["submitted", "approved", "reopened", "reset"])
def test_dispatch_lifecycle_transitions_are_observations_without_metadata_events(event):
    result = report([
        {"kind": "state_transition", "name": "task", "event": "task." + event,
         "to": "queued", "ts": STAMP // 1_000_000, "id": "not-output"},
        {"kind": "state_transition", "name": "task", "event": "task.heartbeat",
         "ts": STAMP // 1_000_000},
    ])
    assert result["hours"][-1]["state"] == "observed"
    assert result["hours"][-1]["groups"][0]["outcomes"] == {event: 1}
    assert result["coverage"]["unsupported_records"] == 1
    assert "not-output" not in json.dumps(result)


def test_standalone_chart_identifies_absolute_window_and_report_freshness():
    current = report([])
    later = report([], now=NOW + timedelta(days=7))
    chart = health_chart.render(current)
    assert chart != health_chart.render(later)
    assert current["generated_at"] in chart
    assert current["hours"][0]["hour"] in chart
    assert current["hours"][-1]["hour"] in chart
    assert current["hours"][24]["hour"][5:16].replace("T", " ") + "Z" in chart
