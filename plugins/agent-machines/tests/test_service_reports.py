from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_machines import service_reports as reports
from process_telemetry import hourly

HOUR = "2026-11-09T12:00:00Z"
STAMP = hourly.hour_start(HOUR)


def selected(service, *, identity="local-installation"):
    return reports.SelectedService(identity, service, ["qualified-runtime"], {})


def response(service="agent-dispatch"):
    return {
        "status": "collected",
        "declaration": {
            "schema": hourly.DECLARATION, "version": 1, "service": service,
            "operation": "telemetry-hourly", "metrics": ["task-lifecycle"],
        },
        "contribution": {
            "schema": hourly.SCHEMA, "version": 1, "service": service,
            "hour": HOUR, "generated_at_ms": STAMP + 100,
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
        },
    }


@pytest.mark.parametrize("bad", [
    {"status": "unsupported"}, {"status": "invalid"}, {"status": "unavailable"},
])
def test_missing_old_metadata_and_invalid_exports_are_explicit_independent_gaps(monkeypatch, bad):
    calls = []
    monkeypatch.setattr(reports, "select", lambda service: selected(service, identity=service))

    def run(command, **kwargs):
        calls.append((command, kwargs))
        value = bad if len(calls) == 1 else response("agent-bridge")
        return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())

    monkeypatch.setattr(reports, "run_caller_owned", run)
    result = reports.collect(["agent-dispatch", "agent-bridge"], HOUR, now_ms=STAMP + 1000)
    assert result[0]["state"] == bad["status"]
    assert result[1]["contribution"]["coverage"]["observations"] == 1
    assert calls[0][0] == ["qualified-runtime", HOUR]
    assert calls[0][1]["max_stdout_bytes"] == 64 * 1024
    assert "local-installation" not in json.dumps(result)


@pytest.mark.parametrize("mutation", [
    lambda value: value["contribution"].update(service="agent-bridge"),
    lambda value: value["contribution"].update(hour="2026-11-09T13:00:00Z"),
    lambda value: value["declaration"].update(version=2),
    lambda value: value["contribution"]["groups"][0].update(metric="unknown-metric"),
])
def test_qualified_service_cannot_forge_another_owner_or_hour(monkeypatch, mutation):
    value = response()
    mutation(value)
    monkeypatch.setattr(reports, "select", selected)
    monkeypatch.setattr(reports, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(value).encode(),
    ))
    result = reports.collect(["agent-dispatch"], HOUR, now_ms=STAMP + 1000)
    assert result == [{"instance": "agent-dispatch", "service": "agent-dispatch", "hour": HOUR, "state": "invalid"}]


def test_duplicate_owner_identity_is_not_executed_or_counted_twice(monkeypatch):
    calls = []
    monkeypatch.setattr(reports, "select", selected)
    monkeypatch.setattr(reports, "run_caller_owned", lambda *a, **k: (
        calls.append(a) or SimpleNamespace(returncode=0, stdout=b'{"status":"unsupported"}')
    ))
    result = reports.collect(["agent-dispatch", "again=agent-dispatch"], HOUR, now_ms=STAMP)
    assert result[1]["state"] == "duplicate" and len(calls) == 1


def test_timeout_and_partial_stdout_do_not_become_empty_success(monkeypatch):
    monkeypatch.setattr(reports, "select", selected)
    monkeypatch.setattr(reports, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b'{"status":',
    ))
    assert reports.collect(["agent-dispatch"], HOUR, now_ms=STAMP)[0]["state"] == "invalid"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("owned-runtime", 10)

    monkeypatch.setattr(reports, "run_caller_owned", timeout)
    assert reports.collect(["agent-dispatch"], HOUR, now_ms=STAMP)[0]["state"] == "timeout"


def test_absent_explicit_installation_context_does_not_invoke_a_payload(monkeypatch):
    monkeypatch.delenv(reports._peer_launch.CONTEXT_ENV, raising=False)
    monkeypatch.setattr(reports, "run_caller_owned", lambda *a, **k: pytest.fail("unqualified launch"))
    assert reports.collect(["agent-dispatch"], HOUR, now_ms=STAMP)[0]["state"] == "unavailable"


def test_cleanup_failure_retains_owned_exception_instead_of_reporting_a_gap(monkeypatch):
    error = reports.OwnedCleanupError(None)
    monkeypatch.setattr(reports, "select", selected)

    def unsettled(*args, **kwargs):
        raise error

    monkeypatch.setattr(reports, "run_caller_owned", unsettled)
    with pytest.raises(reports.OwnedCleanupError) as caught:
        reports.collect(["agent-dispatch"], HOUR, now_ms=STAMP)
    assert caught.value is error


def test_same_named_installations_keep_their_own_namespace_without_percentile_merging(monkeypatch):
    identities = iter(("first-installation", "second-installation"))
    monkeypatch.setattr(reports, "select", lambda service: selected(service, identity=next(identities)))
    monkeypatch.setattr(reports, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(response()).encode(),
    ))
    result = reports.collect(["first=agent-dispatch", "second=agent-dispatch"], HOUR, now_ms=STAMP)
    assert [row["instance"] for row in result] == ["first", "second"]
    assert [row["contribution"]["coverage"]["observations"] for row in result] == [1, 1]
    assert "first-installation" not in json.dumps(result)


@pytest.mark.parametrize("requests,identities", [
    (["agent-dispatch", "agent-bridge"], ["one-owner", "one-owner"]),
    (["agent-dispatch", "agent-dispatch"], ["one-owner", "another-owner"]),
])
def test_ambiguous_owner_or_presentation_namespace_is_not_silently_replaced(
    monkeypatch, requests, identities,
):
    remaining = iter(identities)
    calls = []
    monkeypatch.setattr(reports, "select", lambda service: selected(service, identity=next(remaining)))
    monkeypatch.setattr(reports, "run_caller_owned", lambda *a, **k: (
        calls.append(a) or SimpleNamespace(returncode=0, stdout=b'{"status":"unsupported"}')
    ))
    result = reports.collect(requests, HOUR, now_ms=STAMP)
    assert result[1]["state"] == "invalid" and len(calls) == 1


def test_late_timeout_reserves_cleanup_inside_both_deadlines_without_renewal(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(reports, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=lambda: STAMP / 1000,
    ))

    def select(service):
        if clock[0] == 0:
            clock[0] = 28.0
        return selected(service, identity=service)

    def timeout(*args, **kwargs):
        budget = kwargs["budget"]
        calls.append(budget)
        assert budget.deadline <= 30 - reports.RESPONSE_MARGIN
        assert budget.deadline <= clock[0] + reports.SERVICE_SECONDS
        assert 0 < budget.cleanup_reserve < budget.deadline - clock[0]
        assert budget.execution_deadline < budget.deadline
        clock[0] = budget.deadline
        raise subprocess.TimeoutExpired("owned-runtime", kwargs["timeout"])

    monkeypatch.setattr(reports, "select", select)
    monkeypatch.setattr(reports, "run_caller_owned", timeout)
    result = reports.collect(["agent-dispatch", "agent-bridge"], HOUR, now_ms=STAMP)
    assert [row["state"] for row in result] == ["timeout", "timeout"] and len(calls) == 1
