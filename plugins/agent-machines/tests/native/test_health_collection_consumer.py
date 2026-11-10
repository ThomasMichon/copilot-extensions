"""Fixture producers through normal collection, confirmed slots and report."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import venv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs
from agent_machines import _peer_launch, health_collect, health_hourly, health_slots, service_reports
from agent_machines.__main__ import main
from test_health_observe_consumer import QUALIFYING_BODY, listener, response


@pytest.mark.parametrize("flow", ["manual", "one-shot"])
def test_real_service_and_listener_reach_distinct_slots_and_read_only_report(
    tmp_path, monkeypatch, capsys, flow,
):
    repo = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(repo / "plugins" / "agent-dispatch" / "src"))
    for path in (repo / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(path))
    from agent_dispatch import telemetry
    from process_telemetry.hourly import hour_start

    fixture = tmp_path / "selected-runtime"
    venv.EnvBuilder(with_pip=False).create(fixture)
    if os.name == "nt":
        python, site = fixture / "Scripts" / "python.exe", fixture / "Lib" / "site-packages"
    else:
        python = fixture / "bin" / "python"
        site = fixture / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    selected_package = site / "agent_dispatch"
    selected_package.mkdir()
    # Keep the real early CLI/export closure without copying coordinator code.
    for name in (
        "__init__.py", "__main__.py", "source_build.py", "early_cli.py",
        "delegated_waiter_contract.py", "telemetry_cli.py", "telemetry.py",
        "install_paths.py", "hourly_reporting.py", "hourly-reporting.json",
    ):
        shutil.copyfile(Path(telemetry.__file__).parent / name, selected_package / name)
    (site / "fixture-dependencies.pth").write_text(
        "\n".join(str(Path(path).resolve()) for path in sys.path if path and Path(path).is_dir()) + "\n",
        encoding="utf-8",
    )
    producer = tmp_path / "dispatch"
    producer.mkdir()
    capture = tmp_path / "capture"
    (capture / "retained").mkdir(parents=True)
    (producer / "telemetry.json").write_text(json.dumps({
        "retained_spool": {"directory": str(capture)},
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(producer))
    monkeypatch.delenv(telemetry.SINK_ENV_VAR, raising=False)
    monkeypatch.setenv(_peer_launch.CONTEXT_ENV, "fixture-owner-context")
    monkeypatch.setattr(_peer_launch, "_resolve_peer", lambda *a, **k: (
        {"pluginRoot": str(producer), "installReceipt": str(producer / "install.json")},
        dict(os.environ), python,
    ))
    monkeypatch.setattr(_peer_launch, "retained_runtime_identity", lambda *a: "fixture-retention")
    monkeypatch.setattr(_peer_launch, "retained_runtime_prefix", lambda *a, **k: [
        str(python), "-I", "-B", "-X", "utf8", "-m", "agent_dispatch",
    ])
    now = datetime.now(timezone.utc)
    prior = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z")
    sink = telemetry.make_retained_spool_sink(capture)
    sink({
        **telemetry.task_lifecycle_event("task.completed", {
            "id": "private-task", "repo": "private-repository", "payload": "private-prompt",
        }), "ts": hour_start(prior),
    })
    retained = capture / "retained" / "agent-dispatch-telemetry.jsonl"
    producer_bytes = retained.read_bytes()
    store = tmp_path / "store"
    store.mkdir()
    with listener(response(QUALIFYING_BODY), count=1 if flow == "manual" else 2) as (port, requests):
        (store / "policy.json").write_text(json.dumps({
            "schema": health_slots.POLICY_SCHEMA, "version": 2,
            "publisher": "example-worker", "continuity": "example-generation",
            "collection": {"bridge_port": port, "services": ["queue=agent-dispatch"]},
        }), encoding="utf-8")
        (store / "collection.lock").write_bytes(b"\0")
        source_ms = int(now.timestamp() * 1000)
        retained_domains = {
            "ssh_authenticated": {
                "state": "ok", "evidence": "authenticated-session", "reason": "none",
                "observed_at_ms": source_ms - 2000, "direction": "outbound",
            },
            "logical_agents": {
                "state": "ok", "evidence": "session-owner", "reason": "none",
                "observed_at_ms": source_ms - 1000, "count": 2,
            },
        }
        seeded = health_hourly.validate_record({
            "schema": health_hourly.SCHEMA, "version": 1,
            "publisher": "example-worker", "continuity": "example-generation",
            "hour": source_ms // health_slots.HOUR_MS, "revision": 1, "parent": None,
            "observed_at_ms": source_ms, "observation": {
                "schema": health_slots.SCHEMA, "version": 1,
                "publisher": "example-worker", "continuity": "example-generation",
                "hour": source_ms // health_slots.HOUR_MS, "revision": 1, "parent": None,
                "observed_at_ms": source_ms, "domains": retained_domains,
            },
            "services": [], "coverage": {"observation": "retained", "services": "not-requested"},
        })
        (store / f"{seeded['hour'] % health_slots.SLOTS:03d}.json").write_text(
            json.dumps(seeded), encoding="utf-8",
        )
        original = {path.name: path.read_bytes() for path in store.iterdir()}
        if flow == "manual":
            assert main(["health", "collect", "--store-dir", str(store)]) == 0
            current = json.loads(capsys.readouterr().out)
        else:
            assert main(["health", "run", "--store-dir", str(store)]) == 0
            plan = json.loads(capsys.readouterr().out)
            assert {path.name: path.read_bytes() for path in store.iterdir()} == original
            arguments = [
                "health", "run", "--store-dir", str(store), "--apply", "--expect-policy", plan["policy_token"],
            ]
            assert main(arguments) == 0
            completed = json.loads(capsys.readouterr().out)
            assert completed["status"] == "completed"
            assert all(row["applied"] for row in completed["targets"])
            current_path = store / f"{seeded['hour'] % health_slots.SLOTS:03d}.json"
            historical_path = store / f"{(seeded['hour'] - 1) % health_slots.SLOTS:03d}.json"
            current = json.loads(current_path.read_text(encoding="utf-8"))
            historical = json.loads(historical_path.read_text(encoding="utf-8"))
            historical_bytes = historical_path.read_bytes()
            real_collect = service_reports.collect

            def unavailable(*a, **k):
                raise OSError("fixture second target unavailable")

            monkeypatch.setattr(service_reports, "collect", unavailable)
            assert main(arguments) == 2
            partial = json.loads(capsys.readouterr().out)
            assert partial["status"] == "partial"
            assert partial["targets"][0]["applied"] and partial["targets"][0]["changed"]
            assert partial["targets"][1]["collection"] == "failed"
            assert historical_path.read_bytes() == historical_bytes
            confirmed = json.loads(current_path.read_text(encoding="utf-8"))
            assert confirmed["revision"] == current["revision"] + 1
            current = confirmed
            monkeypatch.setattr(service_reports, "collect", real_collect)
            assert (store / "collection.lock").read_bytes() == b"\0"
    assert len(requests) == (1 if flow == "manual" else 2)
    if flow == "manual":
        assert {path.name: path.read_bytes() for path in store.iterdir()} == original
        assert main(["health", "collect", "--store-dir", str(store), "--hour", prior]) == 0
        historical = json.loads(capsys.readouterr().out)
    assert historical["observation"] is None and historical["hour"] == current["hour"] - 1
    contribution = historical["services"][0]["contribution"]
    assert contribution["groups"][0]["outcomes"] == {"completed": 1}
    if flow == "manual":
        for index, candidate in enumerate((current, historical)):
            source = tmp_path / f"candidate-{index}.json"
            source.write_text(json.dumps(candidate), encoding="utf-8")
            arguments = ["health", "record", "--store-dir", str(store), "--input", str(source)]
            assert main(arguments) == 0
            preview = json.loads(capsys.readouterr().out)
            assert main([*arguments, "--apply", "--expect", preview["preview_token"]]) == 0
            assert json.loads(capsys.readouterr().out)["applied"]
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    chart = tmp_path / "collected.svg"
    assert main(["health", "report", "--store-dir", str(store), "--chart", str(chart)]) == 0
    report = json.loads(capsys.readouterr().out)
    view = report["health"]["view"]
    assert view["observed_hours"] == 2
    assert view["hours"][-1]["sample"]["domains"]["local_listener"]["state"] == "ok"
    for domain, evidence in retained_domains.items():
        sample = view["hours"][-1]["sample"]["domains"][domain]
        assert all(sample[key] == value for key, value in evidence.items())
    assert view["hours"][-2]["sample"]["domains"]["local_listener"]["state"] == "unknown"
    assert view["hours"][-2]["sample"]["services"][0]["contribution"] == contribution
    assert report["coverage"]["accepted_records"] == 0
    assert "queue/agent-dispatch: observed" in chart.read_text(encoding="utf-8")
    assert "private-" not in json.dumps(report)
    assert retained.read_bytes() == producer_bytes
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    assert not (producer / "rendezvous.json").exists()
    assert not list(store.glob("*.tmp.*"))
    metadata = site / "agent_dispatch" / "hourly-reporting.json"
    support = json.loads(metadata.read_text(encoding="utf-8"))
    metadata.write_text(json.dumps({**support, "version": 2}), encoding="utf-8")
    assert main(["health", "collect", "--store-dir", str(store), "--hour", prior]) == 0
    rollback = json.loads(capsys.readouterr().out)
    assert rollback["coverage"]["services"] == "partial"
    assert rollback["services"][0]["contribution"] == contribution
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    metadata.write_text(json.dumps(support), encoding="utf-8")
    (producer / "telemetry.json").unlink()
    empty = service_reports.collect(["queue=agent-dispatch"], prior, now_ms=int(now.timestamp() * 1000))[0]
    assert "contribution" in empty and not empty["contribution"]["groups"]
    assert not empty["contribution"]["health"] and not empty["contribution"]["diagnostics"]
    assert main(["health", "collect", "--store-dir", str(store), "--hour", prior]) == 0
    recollected = json.loads(capsys.readouterr().out)
    assert recollected["coverage"]["services"] == "partial"
    assert recollected["services"][0]["contribution"] == contribution
    forged = {**recollected, "services": [empty]}
    source = tmp_path / "empty-replacement.json"
    source.write_text(json.dumps(forged), encoding="utf-8")
    assert main(["health", "record", "--store-dir", str(store), "--input", str(source)]) == 2
    refusal = json.loads(capsys.readouterr().err)
    assert refusal["error"] == "hourly-service-replacement-refused"
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    with health_collect.admission(store):
        pass


@pytest.mark.parametrize("flow", ["collect", "run"])
def test_unconfirmed_exporter_fence_survives_its_cli_owner_exit(tmp_path, flow):
    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 2,
        "publisher": "example-worker", "continuity": "example-generation",
        "collection": {"bridge_port": None, "services": ["queue=agent-dispatch"]},
    }), encoding="utf-8")
    (store / "collection.lock").write_bytes(b"\0")
    prior = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z")
    code = """
import sys
from pathlib import Path
from agent_machines import service_reports
from agent_machines.__main__ import main
from agent_machines.health_run import run
def unconfirmed(*args, **kwargs):
    raise service_reports.OwnedCleanupError(None)
service_reports.collect = unconfirmed
arguments = ["health", sys.argv[3], "--store-dir", sys.argv[1]]
if sys.argv[3] == "run":
    arguments += ["--apply", "--expect-policy", run(Path(sys.argv[1]))["policy_token"]]
else:
    arguments += ["--hour", sys.argv[2]]
raise SystemExit(main(arguments))
"""
    child = subprocess.run(
        [sys.executable, "-I", "-c", code, str(store), prior, flow],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert child.returncode == 2
    if flow == "collect":
        assert not child.stdout and json.loads(child.stderr)["error"] == "collection-cleanup-unconfirmed"
    else:
        result = json.loads(child.stdout)
        assert not child.stderr
        assert result["qualification"] == "collection-cleanup-unconfirmed"
        assert not result["targets"][1]["applied"]
    assert (store / "collection.lock").read_bytes() == b"\1"
    with pytest.raises(health_slots.HealthError, match="collection-cleanup-unconfirmed"):
        health_collect.collect(store, prior)
    assert not list(store.glob("[0-9][0-9][0-9].json"))


@pytest.mark.parametrize("release", ["normal", "crash"])
def test_native_run_lock_refuses_a_second_process_and_releases_after_owner_exit(
    tmp_path, monkeypatch, release,
):
    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 2,
        "publisher": "example-worker", "continuity": "example-generation",
        "collection": {"bridge_port": None, "services": []},
    }), encoding="utf-8")
    (store / "collection.lock").write_bytes(b"\0")
    held, signal = tmp_path / "held", tmp_path / "release"
    code = """
import os, sys, time
from pathlib import Path
from agent_machines.health_collect import admission
store, held, signal = map(Path, sys.argv[1:4])
with admission(store):
    held.write_text("admitted")
    deadline = time.monotonic() + 8
    while not signal.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    if not signal.exists():
        raise SystemExit(3)
    if sys.argv[4] == "crash":
        os._exit(19)
"""
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", code, str(store), str(held), str(signal), release],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 5
        while not held.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert held.exists(), "fixture did not acquire the run lock"
        with pytest.raises(health_slots.HealthError, match="collection-overlap"):
            health_collect.collect(store)
        signal.write_text("release", encoding="utf-8")
        _stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == (19 if release == "crash" else 0), stderr
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    with health_collect.admission(store):
        pass
