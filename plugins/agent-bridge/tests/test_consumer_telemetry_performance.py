"""Native, contained source measurements, not installed-service performance."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import platform
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from process_telemetry import SinkSlot


def probe() -> Any:
    path = Path(__file__).resolve().parents[3] / "tools/performance/consumer_telemetry_probe.py"
    spec = importlib.util.spec_from_file_location("consumer_telemetry_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("source", ["agent-bridge", "agent-dispatch"])
@pytest.mark.parametrize("mode", [
    "legacy", "disabled", "reading", "slow", "nonreading", "failed", "fsync-failed",
])
def test_native_offer_wall_cpu_tail_and_loss(
    tmp_path: Path, source: str, mode: str,
    record_property: Callable[[str, object], None],
) -> None:
    helper = probe()
    root = tmp_path / "approved root"
    root.mkdir()
    directory = root / ".agent-telemetry"
    directory.mkdir()
    (root / "receiver.py").write_text(helper.RECEIVER, encoding="utf-8")
    (directory / "config.json").write_text(json.dumps(
        {"schema_version": 1, "mode": "disabled"} if mode == "disabled" else {
            "schema_version": 1, "mode": "command",
            "command": {"argv": [
                sys.executable, "-I", "receiver.py", mode,
                *(["--expected-file"] if mode == "reading" else []),
            ]},
        }
    ), encoding="utf-8")
    slot = SinkSlot()
    owner = None
    binding = None
    start = time.perf_counter_ns()
    if mode != "legacy":
        owner = slot.configure_commands(source=source)
        binding = owner.admit(str(root))
    admission_ns = time.perf_counter_ns() - start
    event: dict[str, Any] = (
        {"kind": "state_transition", "name": "session",
         "event": "session_state_changed", "to": "idle"}
        if source == "agent-bridge" else
        {"kind": "state_transition", "name": "task",
         "event": "task.created", "to": "queued", "attempts": 0}
    )

    def offer() -> None:
        if mode == "legacy":
            slot.emit(event)
        else:
            slot.emit(event, binding=binding)

    started = time.perf_counter_ns()
    cpu = time.process_time_ns()
    try:
        cold = helper.measure(offer, 1)
        # A finite unpaced burst intentionally exercises contention and overflow.
        burst = helper.measure(offer, 4096)
        if mode == "reading":
            assert owner is not None
            target = root / "expected-records.pending"
            target.write_text(json.dumps({"records": owner.stats()["enqueued"]}), encoding="utf-8")
            target.replace(root / "expected-records.json")
        if owner is not None and owner.writer is not None:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                stats = owner.stats()
                if stats["pipe_writes"] >= stats["enqueued"] or (
                    stats["start_failed"] + stats["write_failed"] + stats["receiver_exited"]
                ):
                    break
                time.sleep(.01)
            if mode == "reading":
                while not (root / "receiver-cost.json").exists() and time.monotonic() < deadline:
                    time.sleep(.005)
                assert (root / "receiver-cost.json").exists(), owner.stats()
    finally:
        close_start = time.perf_counter_ns()
        if owner is not None:
            owner.close()
        close_ns = time.perf_counter_ns() - close_start
    total_wall = time.perf_counter_ns() - started
    total_cpu = time.process_time_ns() - cpu
    assert close_ns < 2_000_000_000
    rows_path = root / "readback.jsonl"
    rows = [
        json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()
    ] if rows_path.exists() else []
    sequences = [row["sequence"] for row in rows]
    assert sequences == sorted(set(sequences))
    assert all(row["source"] == source for row in rows)
    stats = owner.stats() if owner is not None else {}
    if owner is not None:
        assert stats["offered"] == 4097
        assert owner.writer is None or owner.writer.join(0)
        assert all(item.pipe is None or item.pipe.closed for item in owner.bindings)
    cost_path = root / "receiver-cost.json"
    receiver_cost = json.loads(cost_path.read_text(encoding="utf-8")) if cost_path.exists() else None
    if receiver_cost is not None:
        assert receiver_cost["records"] == len(rows)
    if mode in ("legacy", "disabled"):
        assert not rows and (owner is None or owner.writer is None)
    if mode == "reading":
        assert owner is not None
        assert rows and receiver_cost is not None, (
            stats, [(item.state, item.started) for item in owner.bindings],
        )
        assert receiver_cost["records"] == stats["enqueued"]
        assert stats["start_failed"] == stats["write_failed"] == 0
    confirmed = receiver_cost["records"] if receiver_cost is not None else None
    report: dict[str, Any] = {
        "version": 1, "source": source, "mode": mode,
        "platform": platform.system(), "python": platform.python_version(),
        "boundary": "shared producer offer; finite unpaced burst",
        "admission_wall_ns": admission_ns, "cold_offer": cold, "burst": burst,
        "close_wall_ns": close_ns, "total_wall_ns": total_wall,
        "total_process_cpu_ns": total_cpu, "receiver_cost": receiver_cost,
        "producer_counters": stats, "readback_records": len(rows),
        "fsync_confirmed_records": confirmed,
        "not_read_back": 4097 - len(rows) if owner is not None else None,
        "not_fsync_confirmed": 4097 - confirmed if confirmed is not None else None,
        "sequence_gaps": (
            max(sequences) - min(sequences) + 1 - len(sequences) if sequences else None
        ),
        "receiver_cpu_excludes_startup": True,
        "process_cpu_excludes_receiver": True,
        "receiver_cost_unavailable": receiver_cost is None,
        "loss_counters_are_not_additive": True,
        "delivery_qualification": (
            "not-qualified-under-pressure" if confirmed != 4097 else "complete-finite-readback"
        ) if mode not in ("legacy", "disabled") else "not-applicable",
    }
    if mode == "fsync-failed":
        assert report["readback_records"] > 0
        assert report["fsync_confirmed_records"] is None
        assert report["not_fsync_confirmed"] is None
    record_property("consumer_telemetry_performance", json.dumps(report, sort_keys=True))


@pytest.mark.parametrize("mode", ["legacy", "disabled", "reading", "slow", "failed", "nonreading"])
def test_ordinary_bridge_stop_wall_cpu_tail(
    tmp_path: Path, mode: str, record_property: Callable[[str, object], None],
) -> None:
    from fastapi.testclient import TestClient
    from agent_bridge.app_routes import configure_serving_app
    from agent_bridge.consumer_telemetry import close
    from agent_bridge.db import Database
    from agent_bridge.models import ServiceConfig
    from agent_bridge.session_runtime import session_manager_from_config

    helper = probe()
    root = tmp_path / "approved root"
    root.mkdir()
    (root / "receiver.py").write_text(helper.RECEIVER, encoding="utf-8")
    directory = root / ".agent-telemetry"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps(
        {"schema_version": 1, "mode": "disabled"} if mode == "disabled" else {
            "schema_version": 1, "mode": "command",
            "command": {"argv": [
                sys.executable, "-I", "receiver.py", mode,
                *(["98"] if mode == "reading" else []),
            ]},
        }
    ), encoding="utf-8")
    db = Database(tmp_path / "sessions.db")
    for index in range(49):
        db.create_session(
            session_id=f"fixture-{index}", name="fixture", agent_name="fixture-agent",
            caller_id="fixture-caller", target_dir=str(tmp_path), target_type="local",
            status="stopped", now=time.time(),
        )
    cfg = ServiceConfig(
        db_path=str(db.db_path),
        consumer_telemetry_harnesses=(
            None if mode == "legacy" else {"fixture-caller": str(root)}
        ),
    )
    manager = session_manager_from_config(
        db, cfg, session_host_state_dir=str(tmp_path / "hosts"),
    )
    app = configure_serving_app(cfg, "fixture-bearer", lifespan=None)
    app.state.session_manager = manager
    app.state.db = db
    identifiers = iter(range(49))
    settlement_wait_ns = 0
    try:
        with TestClient(app) as client:
            def operation() -> None:
                response = client.post(
                    f"/api/v1/sessions/fixture-{next(identifiers)}/stop",
                    headers={"Authorization": "Bearer fixture-bearer"},
                )
                assert response.status_code == 204 and response.content == b""

            cold = helper.measure(operation, 1)
            measured = helper.measure(operation, 48)
            if mode == "reading":
                settlement_start = time.perf_counter_ns()
                deadline = time.monotonic() + 5.0
                witness = root / "settled.json"
                settled: dict[str, Any] | None = None
                read_error: str | None = None
                while time.monotonic() < deadline:
                    try:
                        settled = json.loads(witness.read_text(encoding="utf-8"))
                        break
                    except (FileNotFoundError, PermissionError) as error:
                        read_error = type(error).__name__
                    time.sleep(.005)
                settlement_wait_ns = time.perf_counter_ns() - settlement_start
                owner = manager._consumer_telemetry.owner
                assert settled is not None, json.dumps({
                    "owner_stats": owner.stats() if owner is not None else "no receiver owner",
                    "read_error": read_error,
                }, sort_keys=True)
                assert settled["records"] == 98
    finally:
        start = time.perf_counter_ns()
        close(manager)
        shutdown_ns = time.perf_counter_ns() - start
        db.close()
    owner = manager._consumer_telemetry.owner
    if owner is not None:
        assert owner.writer is None or owner.writer.join(0)
        assert all(item.pipe is None or item.pipe.closed for item in owner.bindings)
    output = root / "readback.jsonl"
    rows = output.read_text(encoding="utf-8").splitlines() if output.exists() else []
    cost = root / "receiver-cost.json"
    receiver_cost = json.loads(cost.read_text(encoding="utf-8")) if cost.exists() else None
    if receiver_cost is not None:
        assert receiver_cost["records"] == len(rows)
    if mode == "reading":
        assert len(rows) == 98
        assert receiver_cost is not None
    record_property("consumer_telemetry_http_performance", json.dumps({
        "version": 1, "source": "agent-bridge", "mode": mode,
        "boundary": "authenticated stopped-session HTTP stop; 49 distinct sessions",
        "cold_operation": cold, "measurement": measured, "shutdown_wall_ns": shutdown_ns,
        "receiver_settlement_wait_ns": settlement_wait_ns,
        "receiver_settlement_outside_producer_measurement": True,
        "readback_records": len(rows),
        "fsync_confirmed_records": receiver_cost["records"] if receiver_cost is not None else None,
        "producer_counters": owner.stats() if owner is not None else {},
        "receiver_cost": receiver_cost,
        "includes_http_database_and_timer": True,
        "http_tail_sample_insufficient_for_tail_qualification": True,
        "not_live_session_stop_or_installed_service": True,
    }, sort_keys=True))
