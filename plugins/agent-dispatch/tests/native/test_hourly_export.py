"""First-use source consumer; never installs or starts the operator's coordinator."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs
from agent_dispatch import telemetry
from agent_dispatch.__main__ import main
from process_telemetry.hourly import hour_start


def test_actual_retained_producer_events_reach_normal_native_hourly_export(tmp_path, monkeypatch):
    root = tmp_path / "dispatch"
    root.mkdir()
    capture = tmp_path / "capture"
    (capture / "retained").mkdir(parents=True)
    (root / "telemetry.json").write_text(json.dumps({
        "retained_spool": {"directory": str(capture)},
    }))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    stamp = hour_start(hour)
    sink = telemetry.make_retained_spool_sink(capture)
    for verb in ("created", "completed"):
        sink({**telemetry.task_lifecycle_event(f"task.{verb}", {
            "id": "private-task", "repo": "private-repository", "payload": "private-prompt",
        }), "ts": stamp})
    path = capture / "retained" / "agent-dispatch-telemetry.jsonl"
    before = path.read_bytes()
    child = subprocess.run(
        [sys.executable, "-m", "agent_dispatch", "telemetry", "hourly", "--hour", hour],
        capture_output=True, text=True, timeout=20, env=dict(os.environ), **no_window_kwargs(),
    )
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["groups"][0]["outcomes"] == {"completed": 1, "created": 1}
    assert result["coverage"]["observations"] == 2
    assert result["source"]["first_ms"] == stamp
    assert result["health"] == [] and result["coverage"]["producer_loss"] == "unknown"
    assert "private-" not in child.stdout
    assert before == path.read_bytes()
    assert not (root / "rendezvous.json").exists()


def test_absent_configuration_and_old_commands_remain_read_only(tmp_path, monkeypatch, capsys):
    root = tmp_path / "absent"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    assert main(["telemetry", "hourly", "--hour", hour]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["state"] == "unavailable"
    assert result["coverage"]["observations"] == 0 and result["groups"] == []
    assert not root.exists()
    assert main(["telemetry", "capabilities"]) == 0
    assert json.loads(capsys.readouterr().out)["configuration_only"] is True


def test_service_events_selected_runtime_metadata_and_normal_machine_report(
    tmp_path, monkeypatch, capsys,
):
    """Only installation admission is fixture-resolved; export/collection use real parsers."""
    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(repo / "plugins" / "agent-machines" / "src"))
    for path in (repo / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(path))
    from agent_machines import _peer_launch
    from agent_machines.__main__ import main as machine_main

    fixture = tmp_path / "selected-runtime"
    venv.EnvBuilder(with_pip=False).create(fixture)
    if os.name == "nt":
        python = fixture / "Scripts" / "python.exe"
        site = fixture / "Lib" / "site-packages"
    else:
        python = fixture / "bin" / "python"
        site = fixture / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(telemetry.__file__).parent, site / "agent_dispatch", ignore=shutil.ignore_patterns("__pycache__"))
    (site / "fixture-dependencies.pth").write_text(
        "\n".join(str(Path(path).resolve()) for path in sys.path if path and Path(path).is_dir()) + "\n",
        encoding="utf-8",
    )
    root = tmp_path / "dispatch"
    root.mkdir()
    capture = tmp_path / "capture"
    (capture / "retained").mkdir(parents=True)
    (root / "telemetry.json").write_text(json.dumps({
        "retained_spool": {"directory": str(capture)},
    }))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.setenv(_peer_launch.CONTEXT_ENV, "fixture-owner-context")
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    monkeypatch.setattr(_peer_launch, "_resolve_peer", lambda *a, **k: (
        {"pluginRoot": str(root), "installReceipt": str(root / "install.json")},
        dict(os.environ), python,
    ))
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *a: "fixture-retention")
    monkeypatch.setattr(_peer_launch, "retained_runtime_prefix", lambda *a, **k: [
        str(python), "-I", "-B", "-X", "utf8", "-m", "agent_dispatch",
    ])
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H:00:00Z")
    sink = telemetry.make_retained_spool_sink(capture)
    sink({**telemetry.task_lifecycle_event("task.completed", {
        "id": "private-task", "repo": "private-repository",
    }), "ts": hour_start(hour)})
    from process_telemetry.measurement import begin

    telemetry.set_telemetry_sink(sink)
    try:
        measurement = begin(telemetry._slot, "agent-dispatch", "preparation")
        assert measurement is not None
        measurement.wall_ns = hour_start(hour) * 1_000_000 + 100
        measurement.finish("ok")
    finally:
        telemetry.clear_telemetry_sink()
    before = (capture / "retained" / "agent-dispatch-telemetry.jsonl").read_bytes()
    arguments = [
        "health", "report", "--service", "agent-dispatch",
        "--service", "duplicate=agent-dispatch", "--hour", hour,
    ]
    chart = tmp_path / "services.svg"
    assert machine_main([*arguments, "--chart", str(chart)]) == 0
    report = json.loads(capsys.readouterr().out)
    result = report["services"][0]
    assert len(report["hours"]) == 168 and result["state"] == "observed"
    groups = result["contribution"]["groups"]
    assert next(group for group in groups if group["metric"] == "task-lifecycle")["outcomes"] == {"completed": 1}
    timing = next(group for group in groups if group["metric"] == "operation-duration")
    assert timing["successful_durations"] == 1 and timing["p95"] >= 0
    assert result["contribution"]["coverage"]["producer_loss"] == "unknown"
    assert report["services"][1]["state"] == "duplicate"
    assert "agent-dispatch/agent-dispatch" in chart.read_text()
    assert "task-lifecycle/lifecycle: 1 observed events" in chart.read_text()
    assert "operation-duration/preparation: 1 observed events" in chart.read_text()
    assert "private-" not in json.dumps(report)
    assert before == (capture / "retained" / "agent-dispatch-telemetry.jsonl").read_bytes()
    metadata = site / "agent_dispatch" / "hourly-reporting.json"
    support = json.loads(metadata.read_text())
    metadata.write_text(json.dumps({**support, "version": 2}))
    assert machine_main(arguments) == 0
    skew = json.loads(capsys.readouterr().out)
    assert skew["services"][0]["state"] == "unsupported"
    metadata.unlink()
    assert machine_main(arguments) == 0
    rollback = json.loads(capsys.readouterr().out)
    assert rollback["services"][0]["state"] == "unsupported"
    assert not (root / "rendezvous.json").exists()


def test_scan_bounds_partial_tail_and_repeated_file_remain_explicit(tmp_path):
    from agent_dispatch.hourly_reporting import observation
    from process_telemetry import hourly_scan

    hour = "2026-11-09T12:00:00Z"
    stamp = hour_start(hour)
    path = tmp_path / "events.jsonl"
    event = {
        "kind": "state_transition", "name": "task", "event": "task.completed",
        "ts": stamp,
    }
    path.write_bytes((json.dumps(event) + "\n").encode() + b'{"partial":')
    result = hourly_scan.reduce(
        [path, path], service="agent-dispatch", hour=hour,
        generated_at_ms=stamp + 1000, observation=observation,
    )
    assert result["coverage"]["state"] == "partial"
    assert result["coverage"]["observations"] == 1
    assert result["coverage"]["partial"] == 1 and result["coverage"]["duplicates"] == 1
    assert result["health"] == []


@pytest.mark.parametrize("history", ["archives", "active-prefix"])
def test_saturated_retained_history_cannot_hide_current_hour_events(
    tmp_path, monkeypatch, capsys, history,
):
    from process_telemetry import hourly_scan
    from process_telemetry.hourly import HOUR_MS

    root = tmp_path / "dispatch"
    root.mkdir()
    capture = tmp_path / "capture"
    retained = capture / "retained"
    retained.mkdir(parents=True)
    (root / "telemetry.json").write_text(json.dumps({
        "retained_spool": {"directory": str(capture)},
    }))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    stamp = hour_start(hour)
    old = {
        "kind": "state_transition", "name": "task", "event": "task.created",
        "ts": stamp - HOUR_MS,
    }
    data = (json.dumps(old) + "\n").encode() * hourly_scan.MAX_RECORDS
    component = "agent-dispatch-telemetry"
    if history == "active-prefix":
        (retained / f"{component}.jsonl").write_bytes(data)
    sink = telemetry.make_retained_spool_sink(capture)
    sink({**telemetry.task_lifecycle_event("task.completed", {}), "ts": stamp})
    if history == "archives":
        rotated = datetime.fromtimestamp((stamp - HOUR_MS) / 1000, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for index in range(hourly_scan.MAX_FILES + 1):
            archive = retained / f"{component}.{rotated}-{index:032x}.jsonl"
            archive.write_bytes(data if index == 0 else b"")
    assert main(["telemetry", "hourly", "--hour", hour]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["coverage"]["observations"] == 1
    assert result["coverage"]["scan_limited"]
    assert result["groups"][0]["outcomes"] == {"completed": 1}
    assert result["source"]["last_ms"] == stamp


def test_reverse_snapshot_does_not_parse_a_suffix_of_an_oversized_record(tmp_path):
    from agent_dispatch.hourly_reporting import observation
    from process_telemetry import hourly_scan

    hour = "2026-11-09T12:00:00Z"
    stamp = hour_start(hour)
    record = (json.dumps({
        "kind": "state_transition", "name": "task", "event": "task.completed", "ts": stamp,
    }) + "\n").encode()
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"x" * hourly_scan.MAX_LINE + record + record)
    result = hourly_scan.reduce(
        [path], service="agent-dispatch", hour=hour,
        generated_at_ms=stamp + 1000, observation=observation,
    )
    assert result["coverage"]["observations"] == 1
    assert result["coverage"]["malformed"] == 1


def test_normal_export_reduces_mixed_performance_outcomes_in_the_completion_hour(
    tmp_path, monkeypatch, capsys,
):
    root = tmp_path / "dispatch"
    root.mkdir()
    capture = tmp_path / "capture"
    (capture / "retained").mkdir(parents=True)
    (root / "telemetry.json").write_text(json.dumps({
        "retained_spool": {"directory": str(capture)},
    }))
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    stamp = hour_start(hour)
    boundary = stamp * 1_000_000
    sink = telemetry.make_retained_spool_sink(capture)
    telemetry.set_telemetry_sink(sink)
    try:
        for start, duration, outcome in (
            (boundary - 10, 10, "ok"), (boundary + 100, 30, "ok"),
            (boundary + 100, 999, "error"), (boundary + 100, 50, "cancelled"),
            (boundary + 100, 70, "unavailable"), (boundary - 11, 10, "ok"),
        ):
            telemetry.emit({
                "schema": "agent.performance.v1", "kind": "span",
                "component": "agent-dispatch", "stage": "preparation",
                "start_time_ns": start, "duration_ns": duration, "outcome": outcome,
                "trace_id": "private-correlation",
            })
    finally:
        telemetry.clear_telemetry_sink()
    assert main(["telemetry", "hourly", "--hour", hour]) == 0
    result = json.loads(capsys.readouterr().out)
    group = result["groups"][0]
    assert group["metric"] == "operation-duration" and group["unit"] == "ns"
    assert group["observations"] == 5 and group["successful_durations"] == 2
    assert group["outcomes"] == {"ok": 2, "error": 1, "cancelled": 1, "unavailable": 1}
    assert (group["p50"], group["p95"], group["maximum"]) == (10, 30, 30)
    assert result["coverage"]["outside_hour"] == 1
    assert result["source"]["first_ms"] == stamp
    assert "private-correlation" not in json.dumps(result)
