"""Actor-free composition; native observation and storage stay in their lane."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 9, 12, 30, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)


@pytest.fixture
def owners(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("an actor-free composition test reached a native owner")

    def package(name):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
        return module

    def load(name, source):
        spec = importlib.util.spec_from_file_location(name, ROOT / source)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    telemetry = package("process_telemetry")
    vocabulary = package(telemetry.__name__ + ".measurement")
    vocabulary.STAGES = frozenset({"preparation"})
    vocabulary.OUTCOMES = frozenset({"ok", "error", "cancelled", "unavailable"})
    load(telemetry.__name__ + ".report", "libs/process-telemetry/src/process_telemetry/report.py")
    machines = package("promotion_health_composition")
    lock = package(machines.__name__ + ".killswitch_mutation")
    lock.mutation_lock = forbidden
    sources = "plugins/agent-machines/src/agent_machines/"
    modules = {
        name: load(machines.__name__ + "." + name, sources + name + ".py")
        for name in ("health_slots", "health_report", "health_chart", "cli_health")
    }
    services = package(machines.__name__ + ".service_reports")
    services.collect = lambda names, hour, **fields: [
        {"instance": name, "service": name, "hour": hour, "state": "unsupported"}
        for name in names
    ]
    hourly = package(telemetry.__name__ + ".hourly")
    hourly.hour_start = lambda hour: int(datetime.fromisoformat(hour.replace("Z", "+00:00")).timestamp() * 1000)
    monkeypatch.setattr(modules["health_slots"], "inspect", forbidden)
    parser = argparse.ArgumentParser(allow_abbrev=False)
    modules["cli_health"].add_parser(parser.add_subparsers(required=True))
    return modules, parser


def view(slots):
    record = slots.validate_record({
        "schema": slots.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": STAMP // slots.HOUR_MS,
        "revision": 1, "parent": None, "observed_at_ms": STAMP,
        "domains": {"local_listener": {
            "state": "ok", "evidence": "listener-check", "reason": "none",
            "observed_at_ms": STAMP - 2 * slots.HOUR_MS,
        }},
    })
    hours = [
        {"hour": hour, "state": "missing"}
        for hour in range(record["hour"] - 167, record["hour"] + 1)
    ]
    hours[-1].update(state="observed", sample=slots._sample(record, STAMP))
    hours[-2]["state"] = "invalid"
    return {
        "schema": "agent-machines.health.view", "version": 1, "generated_at_ms": STAMP,
        "publisher": record["publisher"], "continuity": record["continuity"],
        "hours": hours, "observed_hours": 1, "view": "last-known",
        "probe_execution": "not-performed", "carrier_receipt": "not-observed",
        "observation_qualification": "input-supplied-not-verified",
    }


def test_normal_report_composes_actual_age_projection_without_promoting_health_to_events(
    owners, monkeypatch, capsys,
):
    modules, parser = owners
    slots = modules["health_slots"]
    expected = view(slots)
    selected = Path("explicit-local-store")
    calls = []

    def inspect(store, *, now_ms):
        calls.append((store, now_ms))
        return expected

    monkeypatch.setattr(slots, "inspect", inspect)
    args = parser.parse_args(["health", "report", "--store-dir", str(selected), "--now", NOW.isoformat()])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [(selected, STAMP)]
    assert result["health"] == {"state": "available", "view": expected}
    assert result["coverage"]["accepted_records"] == 0
    assert all(hour["state"] == "no-observation" for hour in result["hours"])
    sample = result["health"]["view"]["hours"][-1]["sample"]
    assert sample["domains"]["local_listener"]["state"] == "stale"
    assert sample["domains"]["local_listener"]["observed_state"] == "ok"
    assert sample["domains"]["ssh_authenticated"]["direction"] is None
    assert sample["domains"]["logical_agents"]["count"] is None
    chart = modules["health_chart"].render(result)
    ElementTree.fromstring(chart)
    assert "1/168 observed slots" in chart and "input-supplied-not-verified" in chart
    assert "observed ok; source age ms 7200000" in chart
    assert "slot invalid" in chart and "slot missing" in chart
    assert "<script" not in chart and "href=" not in chart


@pytest.mark.parametrize("failure", ["invalid", "unavailable"])
def test_store_failure_is_a_visible_gap_without_suppressing_independent_service_report(
    owners, monkeypatch, capsys, failure,
):
    modules, parser = owners
    slots = modules["health_slots"]

    def inspect(*args, **kwargs):
        raise slots.HealthError("private-input") if failure == "invalid" else OSError("private-path")

    monkeypatch.setattr(slots, "inspect", inspect)
    args = parser.parse_args([
        "health", "report", "--store-dir", "explicit-store", "--service", "agent-dispatch",
        "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 0
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result["health"] == {"state": "unavailable", "reason": "local-store-unavailable"}
    assert result["services"][0]["state"] == "unsupported"
    assert "private" not in raw
    chart = modules["health_chart"].render(result)
    ElementTree.fromstring(chart)
    assert "Local health store unavailable" in chart
    assert "agent-dispatch/agent-dispatch" in chart


def test_old_report_selection_is_unchanged_and_unknown_new_syntax_is_rejected(owners, capsys):
    modules, parser = owners
    args = parser.parse_args(["health", "report", "--service", "agent-dispatch", "--now", NOW.isoformat()])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert "health" not in result and result["services"][0]["state"] == "unsupported"
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["health", "report", "--store-directory", "not-supported"])
    assert error.value.code == 2


def test_chart_inside_selected_store_refuses_before_reading_or_writing(owners, monkeypatch, capsys):
    _modules, parser = owners
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    args = parser.parse_args([
        "health", "report", "--store-dir", "store", "--chart", str(Path("store") / "new.svg"),
        "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "health report unavailable" in captured.err


def test_empty_health_history_is_visible_without_fabricating_domain_rows(owners, monkeypatch, capsys):
    modules, parser = owners
    slots = modules["health_slots"]
    empty = view(slots)
    empty["hours"] = [{"hour": hour["hour"], "state": "missing"} for hour in empty["hours"]]
    empty["observed_hours"] = 0
    monkeypatch.setattr(slots, "inspect", lambda *args, **kwargs: empty)
    args = parser.parse_args(["health", "report", "--store-dir", "explicit-store", "--now", NOW.isoformat()])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    chart = modules["health_chart"].render(result)
    ElementTree.fromstring(chart)
    assert "0/168 observed slots" in chart and "slot missing" in chart
    assert "local listener</text>" not in chart
    assert "observed ok;" not in chart


@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
def test_unresolvable_store_preserves_independent_json_and_external_chart(
    owners, monkeypatch, capsys, error_type,
):
    modules, parser = owners
    store = Path("unresolvable-store")
    output = io.StringIO()

    class Chart:
        def __fspath__(self):
            return "external.svg"

        def resolve(self):
            return Path("external.svg")

        def open(self, mode, encoding):
            assert mode == "x" and encoding == "utf-8"
            return nullcontext(output)

    def resolve(path):
        assert path == store
        raise error_type("private-resolution-detail")

    def inspect(*args, **kwargs):
        raise modules["health_slots"].HealthError("path-unavailable")

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(modules["health_slots"], "inspect", inspect)
    args = parser.parse_args([
        "health", "report", "--store-dir", str(store), "--service", "agent-dispatch",
        "--now", NOW.isoformat(),
    ])
    args.chart = Chart()
    assert args.func(args) == 0
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result["health"] == {"state": "unavailable", "reason": "local-store-unavailable"}
    assert result["services"][0]["state"] == "unsupported"
    assert "private" not in raw
    assert "Local health store unavailable" in output.getvalue()
    ElementTree.fromstring(output.getvalue())


def test_unresolvable_chart_is_a_bounded_output_refusal(owners, monkeypatch, capsys):
    _modules, parser = owners

    def resolve(path):
        raise RuntimeError("private-chart-detail")

    monkeypatch.setattr(Path, "resolve", resolve)
    args = parser.parse_args([
        "health", "report", "--store-dir", "store", "--chart", "external.svg",
        "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "private" not in captured.err
