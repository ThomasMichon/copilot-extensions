"""Actor-free qualification of the canonical hourly wire codec."""

from __future__ import annotations

import importlib.util
import json
from contextlib import nullcontext
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
    budget = ModuleType("agent_procutil.owned_budget")
    budget.OwnedExecutionBudget = lambda **fields: SimpleNamespace(
        **fields, execution_deadline=fields["deadline"] - fields["cleanup_reserve"],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("an actor-free contribution test reached a native owner")

    native.run_caller_owned = forbidden
    peer._resolve_peer = forbidden
    telemetry = ModuleType("process_telemetry")
    telemetry.__path__ = []
    for module in (package, peer, process, native, budget, telemetry):
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


@pytest.fixture
def scanner(protocol, monkeypatch):
    report_name = protocol.__package__ + ".report"
    report_spec = importlib.util.spec_from_file_location(
        report_name, ROOT / "libs/process-telemetry/src/process_telemetry/report.py",
    )
    assert report_spec and report_spec.loader
    report = importlib.util.module_from_spec(report_spec)
    monkeypatch.setitem(sys.modules, report.__name__, report)
    report_spec.loader.exec_module(report)
    name = protocol.__package__ + ".hourly_scan"
    source = ROOT / "libs/process-telemetry/src/process_telemetry/hourly_scan.py"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class LogicalPath(str):
    @property
    def name(self):
        return self.rsplit("\\", 1)[-1]

    def __truediv__(self, name):
        return LogicalPath(f"{self}\\{name}")

    def lstat(self):
        return SimpleNamespace()


def directory(scanner, names):
    visited = []
    root = LogicalPath("fixture")

    def entries():
        for name in names:
            visited.append(name)
            yield SimpleNamespace(name=name, path=root / name)

    scanner.Path = LogicalPath
    scanner.os = SimpleNamespace(scandir=lambda _: nullcontext(entries()))
    return root, visited


@pytest.mark.parametrize("saturation", ["archives", "entries"])
def test_current_active_source_survives_archive_and_directory_cutoffs(scanner, saturation):
    component = "agent-dispatch-telemetry"
    if saturation == "archives":
        names = [
            f"{component}.20261109T110000000000Z-{index:032x}.jsonl"
            for index in range(scanner.MAX_FILES + 3)
        ]
    else:
        names = [f"unrelated-{index}" for index in range(scanner.MAX_ENTRIES + 1)]
    root, visited = directory(scanner, names)
    selected, limited = scanner.retained_sources(root, component, hour="2026-11-09T12:00:00Z")
    assert selected[0].name == f"{component}.jsonl" and limited
    assert len(selected) <= scanner.MAX_FILES and len(visited) <= scanner.MAX_ENTRIES + 1


def test_archive_selection_prioritizes_rotation_near_the_requested_hour(scanner):
    component = "agent-dispatch-telemetry"
    names = [
        f"{component}.{stamp}-{'0' * 32}.jsonl"
        for stamp in ("20261109T110000000000Z", "20261109T130000000000Z", "20261109T123000000000Z")
    ]
    root, _ = directory(scanner, names)
    selected, limited = scanner.retained_sources(root, component, hour="2026-11-09T12:00:00Z")
    assert [path.name for path in selected[1:]] == [names[2], names[1], names[0]]
    assert not limited


def test_cleanup_reserve_is_inside_child_and_collection_deadlines(collector, protocol):
    clock = [0.0]
    calls = []
    collector.time = SimpleNamespace(monotonic=lambda: clock[0])

    def select(service):
        clock[0] = max(clock[0], 28.0)
        return collector.SelectedService(service, service, ["native"], {})

    def timeout(*args, **kwargs):
        budget = kwargs["budget"]
        calls.append(budget)
        assert clock[0] < budget.execution_deadline < budget.deadline <= 30 - collector.RESPONSE_MARGIN
        assert budget.deadline <= clock[0] + collector.SERVICE_SECONDS
        clock[0] = budget.deadline
        raise subprocess.TimeoutExpired("owned-runtime", kwargs["timeout"])

    collector.select = select
    collector.run_caller_owned = timeout
    result = collector.collect(
        ["agent-dispatch", "agent-bridge"], "2026-11-09T12:00:00Z",
        now_ms=protocol.hour_start("2026-11-09T12:00:00Z"),
    )
    assert [row["state"] for row in result] == ["timeout", "timeout"] and len(calls) == 1


def test_reverse_snapshot_keeps_fresh_records_and_never_admits_partial_or_oversize_suffixes(
    scanner, protocol, tmp_path,
):
    hour = "2026-11-09T12:00:00Z"
    stamp = protocol.hour_start(hour)
    path = tmp_path / "events.jsonl"
    old = (json.dumps({"ts": stamp - protocol.HOUR_MS}) + "\n").encode()
    current = (json.dumps({"ts": stamp}) + "\n").encode()
    path.write_bytes(old * 3 + current + b'{"partial":')
    scanner.MAX_RECORDS = 3

    def observe(record):
        return record["ts"], "task-lifecycle", "lifecycle", "completed", None

    def reduce():
        return scanner.reduce(
            [path], service="example-service", hour=hour,
            generated_at_ms=stamp + 1000, observation=observe,
        )

    result = reduce()
    assert result["coverage"]["observations"] == 1
    assert result["coverage"]["scan_limited"] and result["coverage"]["partial"] == 1
    assert result["source"]["first_ms"] == stamp
    scanner.MAX_LINE = 128
    path.write_bytes(b"x" * scanner.MAX_LINE + current + current)
    result = reduce()
    assert result["coverage"]["observations"] == 1 and result["coverage"]["malformed"] == 1


@pytest.fixture
def adapter(protocol, scanner, monkeypatch):
    telemetry = ModuleType("process_telemetry")
    telemetry.__path__ = []
    configured = ModuleType("process_telemetry.configured_spool")
    configured.retained_directory = lambda *a: pytest.fail("admission does not discover configuration")
    package = ModuleType("promotion_dispatch")
    package.__path__ = []
    owner = ModuleType(package.__name__ + ".telemetry")
    owner.SINK_ENV_VAR = "AGENT_DISPATCH_TELEMETRY_SINK"
    owner._default_config_path = lambda: pytest.fail("admission does not read native configuration")
    for module in (telemetry, configured, package, owner):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, "process_telemetry.hourly", protocol)
    monkeypatch.setitem(sys.modules, "process_telemetry.hourly_scan", scanner)
    monkeypatch.setitem(
        sys.modules, "process_telemetry.measurement",
        sys.modules[protocol.__package__ + ".measurement"],
    )
    name = package.__name__ + ".hourly_reporting"
    source = ROOT / "plugins/agent-dispatch/src/agent_dispatch/hourly_reporting.py"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def span(start, duration=20, outcome="ok", **fields):
    return {
        "schema": "agent.performance.v1", "kind": "span", "component": "agent-dispatch",
        "stage": "preparation", "start_time_ns": start, "duration_ns": duration,
        "outcome": outcome, **fields,
    }


@pytest.mark.parametrize("fields", [
    {"duration_ns": None}, {"duration_ns": True}, {"start_time_ns": -1},
    {"start_time_ns": 2**63 - 1, "duration_ns": 1}, {"component": "another-service"},
    {"stage": "unknown"}, {"outcome": []}, {"kind": "event"},
], ids=["missing", "boolean", "negative", "overflow", "owner", "stage", "outcome", "kind"])
def test_service_span_admission_refuses_invalid_timing_and_vocabulary(adapter, protocol, fields):
    stamp = protocol.hour_start("2026-11-09T12:00:00Z") * 1_000_000
    assert adapter.observation({**span(stamp), **fields}) is None


def test_service_span_admission_uses_completion_not_emit_time(adapter, protocol):
    stamp = protocol.hour_start("2026-11-09T12:00:00Z")
    value = span(stamp * 1_000_000 - 10, duration=10, ts=stamp + protocol.HOUR_MS)
    assert adapter.observation(value) == (stamp, "operation-duration", "preparation", "ok", 10)
    assert adapter.observation({**value, "duration_ns": 9})[0] == stamp - 1


def test_duration_reduction_preserves_success_population_with_mixed_outcomes(
    scanner, adapter, protocol, tmp_path,
):
    hour = "2026-11-09T12:00:00Z"
    stamp = protocol.hour_start(hour)
    boundary = stamp * 1_000_000
    records = [
        span(boundary - 10, duration=10), span(boundary + 100, duration=30),
        span(boundary + 100, duration=999, outcome="error"),
        span(boundary + 100, duration=50, outcome="cancelled"),
        span(boundary + 100, duration=70, outcome="unavailable"),
        span(boundary - 11, duration=10),
    ]
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"".join((json.dumps(record) + "\n").encode() for record in records))
    result = scanner.reduce(
        [path], service="agent-dispatch", hour=hour,
        generated_at_ms=stamp + 1000, observation=adapter.observation,
    )
    protocol.validate(
        result, service="agent-dispatch", hour=hour,
        metrics=["operation-duration"], now_ms=stamp + 1000,
    )
    group = result["groups"][0]
    assert group["observations"] == 5 and group["successful_durations"] == 2
    assert group["outcomes"] == {"ok": 2, "error": 1, "cancelled": 1, "unavailable": 1}
    assert (group["p50"], group["p95"], group["maximum"]) == (10, 30, 30)
    assert result["coverage"]["outside_hour"] == 1
