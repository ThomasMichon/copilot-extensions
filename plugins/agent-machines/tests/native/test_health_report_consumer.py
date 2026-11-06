"""Opt-in real local-file/CLI consumer; no production service or carrier."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from process_telemetry import SinkSlot
from process_telemetry.configured_spool import install_retained
from process_telemetry.measurement import begin
from process_telemetry.retained_spool import RetainedSpool

from agent_procutil import no_window_kwargs
from agent_machines import cli_health
from agent_machines.__main__ import main


def test_existing_configured_sink_to_normal_cli_report_and_chart(tmp_path, capsys):
    base = tmp_path / "capture"
    (base / "retained").mkdir(parents=True)
    slot = SinkSlot()

    def factory(directory):
        return RetainedSpool(
            directory, "example-telemetry", service="example", custody=nullcontext,
        )

    writer = install_retained(
        {"retained_spool": {"directory": str(base)}},
        slot, factory, logging.getLogger("test.health"),
    )
    assert writer is not None
    try:
        for outcome in ("ok", "error"):
            measurement = begin(slot, "example", "preparation")
            assert measurement is not None
            measurement.finish(outcome)
        deadline = time.monotonic() + 5
        path = base / "retained" / "example-telemetry.jsonl"
        while time.monotonic() < deadline:
            if path.exists() and len(path.read_text().splitlines()) == 2:
                break
            time.sleep(.01)
        else:
            raise AssertionError("independent retained-file readback did not complete")
    finally:
        slot.set(None)
        assert writer.join(2)
    chart = tmp_path / "health.svg"
    observed_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    child = subprocess.run([
        sys.executable, "-m", "agent_machines", "health", "report", "--input", str(path),
        "--now", observed_at.isoformat(), "--chart", str(chart),
    ], capture_output=True, text=True, timeout=20, **no_window_kwargs())
    assert child.returncode == 0, child.stderr
    report = json.loads(child.stdout)
    assert report["coverage"]["accepted_records"] == 2
    assert chart.read_text().startswith("<svg")
    assert "example/preparation" in chart.read_text()
    before = path.read_bytes()
    assert main(["health", "report", "--input", str(path), "--chart", str(path)]) == 2
    assert path.read_bytes() == before


def test_input_discovery_never_creates_files_or_counts_the_same_file_twice(tmp_path, capsys):
    missing = tmp_path / "missing.jsonl"
    path = tmp_path / "events.jsonl"
    record = {
        "kind": "state_transition", "name": "task", "event": "task.completed",
        "ts": int(time.time() * 1000),
    }
    path.write_text(json.dumps(record) + "\n")
    assert main([
        "health", "report", "--input", str(missing), "--input", str(path),
        "--input", str(path),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["unavailable_inputs"] == 1
    assert report["coverage"]["duplicate_inputs"] == 1
    assert report["coverage"]["accepted_records"] == 1
    assert not missing.exists()


def test_normal_cli_hourly_slot_roundtrip_uses_explicit_local_inputs(tmp_path):
    from agent_machines import health_slots

    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 1,
        "publisher": "example-worker", "continuity": "example-generation",
    }), encoding="utf-8")
    source = tmp_path / "observation.json"
    now = datetime.now(timezone.utc)
    stamp = int(now.timestamp() * 1000)
    source.write_text(json.dumps({
        "schema": health_slots.SCHEMA, "version": 1,
        "publisher": "example-worker", "continuity": "example-generation",
        "hour": stamp // health_slots.HOUR_MS, "observed_at_ms": stamp,
        "revision": 1, "parent": None, "domains": {},
    }), encoding="utf-8")
    command = [sys.executable, "-m", "agent_machines", "health"]

    def call(arguments):
        child = subprocess.run(
            [*command, *arguments], capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        assert child.returncode == 0, child.stderr
        return json.loads(child.stdout)

    arguments = [
        "record", "--input", str(source), "--store-dir", str(store), "--now", now.isoformat(),
    ]
    preview = call(arguments)
    assert set(path.name for path in store.iterdir()) == {"policy.json"}
    recorded = call([*arguments, "--apply", "--expect", preview["preview_token"]])
    assert recorded["applied"] and recorded["changed"]
    view = call(["inspect", "--store-dir", str(store), "--now", now.isoformat()])
    assert view["observed_hours"] == 1 and len(view["hours"]) == 168
    domains = view["hours"][-1]["sample"]["domains"]
    assert all(item["state"] == "unknown" for item in domains.values())
    assert domains["logical_agents"]["count"] is None
    assert domains["ssh_authenticated"]["direction"] is None
    assert all(item["observed_at_ms"] is None and item["evidence"] == "not-observed" for item in domains.values())
    assert view["carrier_receipt"] == "not-observed" and view["probe_execution"] == "not-performed"


def test_closed_stdout_after_confirmed_record_keeps_unknown_acknowledgement(tmp_path):
    from agent_machines import health_slots

    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 1,
        "publisher": "example-worker", "continuity": "example-generation",
    }), encoding="utf-8")
    source = tmp_path / "observation.json"
    now = datetime.now(timezone.utc)
    stamp = int(now.timestamp() * 1000)
    source.write_text(json.dumps({
        "schema": health_slots.SCHEMA, "version": 1,
        "publisher": "example-worker", "continuity": "example-generation",
        "hour": stamp // health_slots.HOUR_MS, "observed_at_ms": stamp,
        "revision": 1, "parent": None, "domains": {},
    }), encoding="utf-8")
    preview = health_slots.record(store, source, now_ms=stamp)
    reader, writer = os.pipe()
    os.close(reader)
    try:
        child = subprocess.run([
            sys.executable, "-I", "-m", "agent_machines", "health", "record",
            "--input", str(source), "--store-dir", str(store), "--now", now.isoformat(),
            "--apply", "--expect", preview["preview_token"],
        ], stdout=writer, stderr=subprocess.PIPE, text=True, timeout=20, **no_window_kwargs())
    finally:
        os.close(writer)
    assert child.returncode == 2, child.stderr
    result = json.loads(child.stderr)
    assert result["error"] == "response-output-unavailable" and result["applied"] is None
    assert health_slots.inspect(store, now_ms=stamp)["observed_hours"] == 1


def test_replaced_input_is_unavailable_without_reading_the_new_identity(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "events.jsonl"
    path.write_text('{"original":"evidence"}\n')
    original = path.read_bytes()
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text('{"replacement":"not an admitted source"}\n')
    opened = cli_health.os.open

    def replace_before_open(candidate, flags):
        if candidate == path:
            path.rename(tmp_path / "archived.jsonl")
            replacement.replace(path)
        return opened(candidate, flags)

    monkeypatch.setattr(cli_health.os, "open", replace_before_open)
    assert main(["health", "report", "--input", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["coverage"]["unavailable_inputs"] == 1
    assert report["coverage"]["opened_inputs"] == 0
    assert (tmp_path / "archived.jsonl").read_bytes() == original
