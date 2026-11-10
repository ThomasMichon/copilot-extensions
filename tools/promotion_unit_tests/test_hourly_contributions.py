"""Actor-free qualification of the canonical hourly wire codec."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "libs/process-telemetry/src/process_telemetry/hourly.py"


@pytest.fixture
def protocol(monkeypatch):
    package = ModuleType("promotion_hourly_protocol")
    package.__path__ = []
    vocabulary = ModuleType(package.__name__ + ".measurement")
    vocabulary.STAGES = frozenset({"preparation"})
    vocabulary.OUTCOMES = frozenset({"ok", "error", "cancelled", "unavailable"})
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, vocabulary.__name__, vocabulary)
    spec = importlib.util.spec_from_file_location(package.__name__ + ".hourly", ROOT / SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def sample(protocol):
    hour = "2026-11-09T12:00:00Z"
    stamp = protocol.hour_start(hour)
    return {
        "schema": protocol.SCHEMA, "version": 1, "service": "example-service",
        "hour": hour, "generated_at_ms": stamp + 1000,
        "source": {"first_ms": stamp, "last_ms": stamp, "continuity": None},
        "coverage": {
            **dict.fromkeys(protocol.COUNTERS, 0), "files": 1, "records": 3, "observations": 3,
            "state": "observed", "reason": "retained-events", "scan_limited": False,
            "expected_events": None, "producer_loss": "unknown",
        },
        "groups": [{
            "metric": "operation-duration", "stage": "preparation", "unit": "ns",
            "aggregation": "distribution", "scope": "observed-events", "observations": 3,
            "outcomes": {"ok": 2, "error": 1}, "successful_durations": 2,
            "p50": 10, "p95": 30, "maximum": 30,
        }],
        "health": [], "diagnostics": [],
    }


def validate(protocol, value):
    return protocol.validate(
        value, service="example-service", hour="2026-11-09T12:00:00Z",
        metrics=["operation-duration"], now_ms=value["generated_at_ms"] + 1,
    )


@pytest.mark.parametrize("change", ["owner", "hour", "unit", "population", "partial", "extra"])
def test_wire_owner_hour_units_population_and_partial_coverage_are_not_coerced(protocol, change):
    value = sample(protocol)
    assert validate(protocol, protocol.decode(json.dumps(value).encode())) == value
    if change == "owner":
        value["service"] = "another-service"
    elif change == "hour":
        value["source"]["last_ms"] += protocol.HOUR_MS
    elif change == "unit":
        value["groups"][0]["unit"] = "ms"
    elif change == "population":
        value["groups"][0]["successful_durations"] = 3
    elif change == "partial":
        value["coverage"]["scan_limited"] = True
    else:
        value["groups"][0]["private-label"] = "not-admitted"
    with pytest.raises(ValueError):
        validate(protocol, value)


def test_native_health_and_diagnostic_evidence_keep_their_source_time_and_qualification(protocol):
    value = sample(protocol)
    stamp = value["source"]["first_ms"]
    value["health"] = [{
        "domain": "service", "state": "degraded", "evidence": "native-owner",
        "observed_at_ms": stamp,
    }]
    value["diagnostics"] = [{
        "component": "example-service", "reason": "operation-error",
        "qualification": "hypothesis", "first_ms": stamp, "last_ms": stamp, "count": 1,
    }]
    assert validate(protocol, value)["diagnostics"][0]["qualification"] == "hypothesis"
    value["health"][0]["observed_at_ms"] += protocol.HOUR_MS
    with pytest.raises(ValueError):
        validate(protocol, value)


def test_missing_durations_are_not_successful_zeroes_or_averaged_percentiles(protocol):
    value = sample(protocol)
    group = value["groups"][0]
    group.update(outcomes={"error": 3}, successful_durations=0, p50=None, p95=None, maximum=None)
    assert validate(protocol, value)["groups"][0]["p95"] is None
    group["p95"] = 0
    with pytest.raises(ValueError):
        validate(protocol, value)


def test_wire_decoder_and_declaration_refuse_unsupported_or_corrupt_data(protocol):
    for raw in (b'{"version":1,"version":2}', b'{"value":1e999}', b"\xff", b"[" * 1500):
        with pytest.raises(ValueError):
            protocol.decode(raw)
    support = {
        "schema": protocol.DECLARATION, "version": 1, "service": "example-service",
        "operation": "telemetry-hourly", "metrics": ["operation-duration"],
    }
    assert protocol.declaration(support, "example-service") == support
    for update in ({"version": 2}, {"operation": "ensure"}, {"metrics": ["unknown"]}):
        with pytest.raises(ValueError):
            protocol.declaration({**support, **update}, "example-service")


@pytest.fixture
def collector(protocol, monkeypatch):
    package = ModuleType("promotion_machines")
    package.__path__ = []
    peer = ModuleType(package.__name__ + "._peer_launch")
    peer.CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"
    peer.PEERS = {"agent-dispatch": "agent_dispatch"}
    process = ModuleType("agent_procutil")
    process.__path__ = []
    native = ModuleType("agent_procutil.owned_process")
    native.OwnedCleanupError = type("OwnedCleanupError", (subprocess.SubprocessError,), {})

    def forbidden(*args, **kwargs):
        raise AssertionError("an actor-free contribution test reached a native owner")

    native.run_caller_owned = forbidden
    peer._resolve_peer = forbidden
    telemetry = ModuleType("process_telemetry")
    telemetry.__path__ = []
    for module in (package, peer, process, native, telemetry):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, "process_telemetry.hourly", protocol)
    name = package.__name__ + ".service_reports"
    source = ROOT / "plugins/agent-machines/src/agent_machines/service_reports.py"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    stamp = protocol.hour_start("2026-11-09T12:00:00Z")
    module.time = SimpleNamespace(monotonic=lambda: 0, time=lambda: stamp / 1000 + 1)
    return module


def test_collection_preserves_independent_gaps_and_never_averages_namespaces(collector, protocol):
    identities = iter(("first-installation", "second-installation"))
    collector.select = lambda service: collector.SelectedService(next(identities), service, ["native"], {})
    collector.run_caller_owned = lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=b'{"status":"unsupported"}',
    )
    result = collector.collect(
        ["first=agent-dispatch", "second=agent-dispatch"], "2026-11-09T12:00:00Z",
        now_ms=protocol.hour_start("2026-11-09T12:00:00Z"),
    )
    assert [row["instance"] for row in result] == ["first", "second"]
    assert [row["state"] for row in result] == ["unsupported", "unsupported"]
    assert "first-installation" not in json.dumps(result)


def test_collection_de_duplicates_actual_installation_instead_of_presentation_alias(collector, protocol):
    calls = []
    collector.select = lambda service: collector.SelectedService("one-installation", service, ["native"], {})
    collector.run_caller_owned = lambda *a, **k: (
        calls.append(a) or SimpleNamespace(returncode=0, stdout=b'{"status":"unsupported"}')
    )
    result = collector.collect(
        ["first=agent-dispatch", "again=agent-dispatch"], "2026-11-09T12:00:00Z",
        now_ms=protocol.hour_start("2026-11-09T12:00:00Z"),
    )
    assert result[1]["state"] == "duplicate" and len(calls) == 1


def test_projected_task_authority_never_reaches_fleet_service_selection(collector):
    collector.os = SimpleNamespace(environ={
        "AGENT_RT_ROOT": "fixture-root",
        collector._peer_launch.CONTEXT_ENV: "fixture-context",
        "AGENT_DISPATCH_TASK_FUTURE": "projected-authority",
    })
    with pytest.raises(ValueError, match="task authority preserved"):
        collector.select("agent-dispatch")


def test_collection_cannot_hide_unsettled_native_custody_as_an_independent_gap(collector, protocol):
    error = collector.OwnedCleanupError()
    collector.select = lambda service: collector.SelectedService("one-installation", service, ["native"], {})

    def unsettled(*args, **kwargs):
        raise error

    collector.run_caller_owned = unsettled
    with pytest.raises(collector.OwnedCleanupError) as caught:
        collector.collect(
            ["agent-dispatch"], "2026-11-09T12:00:00Z",
            now_ms=protocol.hour_start("2026-11-09T12:00:00Z"),
        )
    assert caught.value is error
