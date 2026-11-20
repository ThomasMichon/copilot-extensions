"""Ordinary Dispatch producers through the shared, contained command owner."""

from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_dispatch import consumer_telemetry, telemetry
from agent_dispatch.config import Config
from agent_dispatch.coordinator import create_app
from agent_dispatch.install_paths import install_dir
from agent_dispatch.queue import TaskQueue
from agent_dispatch.server import build_app
from process_telemetry import SinkSlot
from process_telemetry.command_config import supports
from process_telemetry.dispatch_projection import offered_fields, project
from process_telemetry.emission import LEGACY

REPO_A = "https://github.com/example/alpha"
REPO_B = "https://github.com/example/beta"
AUTH = {"Authorization": "Bearer fixture-data"}
LITERAL = "literal ; $value $(not-a-command) \u03bb"
_RECEIVER = """\
import json, os, sys
from pathlib import Path
with Path("readback.jsonl").open("a", encoding="utf-8") as output:
    for line in sys.stdin:
        record = {"record": json.loads(line), "cwd": os.getcwd(),
                  "argv": sys.argv[1:], "env": sorted(os.environ), "pid": os.getpid()}
        output.write(json.dumps(record) + "\\n")
        output.flush()
        os.fsync(output.fileno())
Path("eof.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
"""


def receiver(root: Path, source: str = _RECEIVER) -> Path:
    root.mkdir()
    (root / "receiver.py").write_text(source, encoding="utf-8")
    directory = root / ".agent-telemetry"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps({
        "schema_version": 1, "mode": "command",
        "command": {"argv": [sys.executable, "-I", "receiver.py", LITERAL]},
    }), encoding="utf-8")
    return root


def readback(root: Path) -> list[dict[str, Any]]:
    path = root / "readback.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def wait_for(predicate: Callable[[], bool], seconds: float = 6) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("bounded receiver observation did not arrive")


def policy_file(tmp_path: Path, repositories: dict[str, str], service: Path | None = None) -> None:
    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "consumer-telemetry.json").write_text(json.dumps({
        "schema_version": 1, "repositories": repositories,
        **({"service_root": str(service)} if service is not None else {}),
    }), encoding="utf-8")


def test_closed_projection_omits_content_identity_and_unavailable_context() -> None:
    original = telemetry.task_lifecycle_event("task.started", {
        "id": "private-task", "repo": REPO_A, "status": "started", "attempts": 2,
        "owner": "private-owner", "prompt": "private text", "payload": object(),
    })
    original.update(
        unknown=object(), trace_id="not-a-trace", native_session_id="private",
        retryable=object(),
    )
    projected = project(offered_fields(original))
    assert projected == {
        "kind": "state_transition", "name": "task", "event": "task.started",
        "to": "started", "attempts": 2,
    }
    assert original["id"] == "private-task"
    fence = telemetry.producer_fence_event("producer_scope.transitioned", {
        "repo": REPO_A, "active_producer": "private", "from_generation": 1,
        "to_generation": 2, "replayed": True, "fingerprint": "private",
    })
    assert project(offered_fields(fence)) == {
        "kind": "state_transition", "name": "producer_fence",
        "event": "producer_scope.transitioned", "from_generation": 1,
        "to_generation": 2, "replayed": True,
    }


@pytest.mark.parametrize("change", [
    {"to": "arbitrary-sensitive-state"}, {"attempts": True}, {"attempts": -1},
    {"attempts": 2**53}, {"event": "task.heartbeat"}, {"kind": "event"},
    {"event": "not-registered"}, {"name": "emission_trace"},
])
def test_projection_rejects_noncontract_values(change: dict[str, Any]) -> None:
    event = {"kind": "state_transition", "name": "task", "event": "task.created"}
    with pytest.raises(ValueError):
        project({**event, **change})


def test_provider_specific_capability_and_missing_old_metadata(tmp_path: Path) -> None:
    selected = Path(consumer_telemetry.__file__).parent / "metadata"
    assert supports(selected, source_schema="dispatch.lifecycle.v1")
    assert not supports(selected)
    assert not supports(tmp_path, source_schema="dispatch.lifecycle.v1")
    bridge = dict(consumer_telemetry.capability(), source_schemas=["bridge.lifecycle.v1"])
    (tmp_path / "consumer-telemetry-contract.json").write_text(json.dumps(bridge))
    assert supports(tmp_path)
    assert not supports(tmp_path, source_schema="dispatch.lifecycle.v1")
    with pytest.raises(ValueError, match="unregistered"):
        SinkSlot().configure_commands(source="arbitrary")


@pytest.mark.parametrize("change", [
    {"schema_version": 2}, {"versions": [2]},
    {"transports": ["future-transport"]}, {"source_schemas": ["bridge.lifecycle.v1"]},
])
def test_mixed_provider_capability_refuses_incompatible_reader(
    tmp_path: Path, change: dict[str, Any],
) -> None:
    metadata = dict(consumer_telemetry.capability(), **change)
    (tmp_path / "consumer-telemetry-contract.json").write_text(
        json.dumps(metadata), encoding="utf-8",
    )
    assert not supports(tmp_path, source_schema="dispatch.lifecycle.v1")


@pytest.mark.parametrize("mode", [
    "legacy", "disabled", "reading", "slow", "failed", "nonreading", "bridge-only",
])
def test_ordinary_dispatch_create_wall_cpu_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
    record_property: Callable[[str, object], None],
) -> None:
    from agent_dispatch import server

    helper = runpy.run_path(str(
        Path(__file__).resolve().parents[3] / "tools/performance/consumer_telemetry_probe.py"
    ))
    root = receiver(tmp_path / "approved root", helper["RECEIVER"])
    declaration = root / ".agent-telemetry" / "config.json"
    value = json.loads(declaration.read_text(encoding="utf-8"))
    value["command"]["argv"][-1] = mode
    declaration.write_text(json.dumps(
        {"schema_version": 1, "mode": "disabled"} if mode == "disabled" else value
    ), encoding="utf-8")
    if mode != "legacy":
        policy_file(tmp_path, {REPO_A: str(root)})
    monkeypatch.setattr(server, "_owns_active_route", lambda: True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db"), token="fixture-data"))
    consumer = app.state.consumer_telemetry
    with TestClient(app) as client:
        def operation() -> None:
            result = client.post("/tasks", headers=AUTH, json={
                "title": "fixture", "repo": REPO_A,
            })
            assert result.status_code == 200 and result.json()["status"] == "queued"

        cold = helper["measure"](operation, 1)
        measured = helper["measure"](operation, 48)
        if mode == "bridge-only":
            wait_for(lambda: (root / "schema-rejection.json").exists())
        teardown_start = time.perf_counter_ns()
    shutdown_ns = time.perf_counter_ns() - teardown_start
    owner = consumer.owner
    if owner is not None:
        assert owner.writer is None or owner.writer.join(0)
        assert all(item.pipe is None or item.pipe.closed for item in owner.bindings)
    rows = readback(root) if (root / "readback.jsonl").exists() else []
    cost = root / "receiver-cost.json"
    receiver_cost = json.loads(cost.read_text(encoding="utf-8")) if cost.exists() else None
    if receiver_cost is not None:
        assert receiver_cost["records"] == len(rows)
    if mode == "reading":
        assert len(rows) == 49
        assert receiver_cost is not None
    elif mode == "bridge-only":
        assert not rows
        assert json.loads((root / "schema-rejection.json").read_text(encoding="utf-8")) == {
            "observed_source_schema": "dispatch.lifecycle.v1", "rejected": True,
        }
    record_property("consumer_telemetry_http_performance", json.dumps({
        "version": 1, "source": "agent-dispatch", "mode": mode,
        "boundary": "authenticated HTTP task creation; 49 distinct tasks",
        "cold_operation": cold, "measurement": measured, "shutdown_wall_ns": shutdown_ns,
        "readback_records": len(rows),
        "fsync_confirmed_records": receiver_cost["records"] if receiver_cost is not None else None,
        "producer_counters": owner.stats() if owner is not None else {},
        "receiver_cost": receiver_cost,
        "includes_http_database_and_timer": True,
        "http_tail_sample_insufficient_for_tail_qualification": True,
        "not_installed_service": True,
    }, sort_keys=True))


def test_admission_rejects_canonical_collisions_and_excess_routes(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        consumer_telemetry.validate_policy({
            "schema_version": 1, "repositories": {
                REPO_A: str(tmp_path),
                "git@github.com:example/alpha.git": str(tmp_path),
            },
        })
    with pytest.raises(ValueError, match="roots"):
        consumer_telemetry.validate_policy({
            "schema_version": 1,
            "repositories": {
                f"github.com/example/project{index}": str(tmp_path) for index in range(4)
            },
            "service_root": str(tmp_path),
        })


@pytest.mark.parametrize("value", [
    {}, {"schema_version": True}, {"schema_version": 2},
    {"schema_version": 1, "repositories": []},
    {"schema_version": 1, "repositories": {"repo": "relative"}},
    {"schema_version": 1, "service_root": None},
    {"schema_version": 1, "command": ["untrusted"]},
])
def test_invalid_bound_policy_never_calls_legacy(value: Any) -> None:
    seen: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(seen.append)
    consumer = consumer_telemetry.ConsumerTelemetry(value, authenticated=True)
    try:
        consumer.start()
        telemetry.emit_for(SimpleNamespace(_consumer_telemetry=consumer), {
            "kind": "state_transition", "name": "task", "event": "task.created",
        })
        assert consumer.refusal == "invalid_config" and consumer.owner is None
        assert seen == []
        telemetry.emit({"legacy": True})
        assert seen == [{"legacy": True}]
    finally:
        consumer.close()
        telemetry.clear_telemetry_sink()


def test_ordinary_service_policy_interleaves_origins_and_service_receiver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import server

    roots = [receiver(tmp_path / name) for name in ("origin a", "origin b", "service")]
    policy_file(tmp_path, {REPO_A: str(roots[0]), REPO_B: str(roots[1])}, roots[2])
    monkeypatch.setattr(server, "_owns_active_route", lambda: True)
    monkeypatch.setenv("FIXTURE_SECRET", "must-not-reach-child")
    monkeypatch.setenv("HARNESS_POLICY", "must-not-reach-child")
    app = build_app(Config(db_path=str(tmp_path / "tasks.db"), token="fixture-data"))
    consumer = app.state.consumer_telemetry
    assert consumer.status()["counters"] == {}
    elsewhere = tmp_path / "product checkout"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    with TestClient(app) as client:
        assert client.post("/tasks", json={"title": "not admitted", "repo": REPO_A}).status_code == 401
        a = client.post("/tasks", headers=AUTH, json={
            "title": "private title", "repo": REPO_A, "proposed": True,
        })
        b = client.post("/tasks", headers=AUTH, json={"title": "private title", "repo": REPO_B})
        assert a.status_code == b.status_code == 200
        # A later declaration change does not retarget already admitted activity.
        (roots[0] / ".agent-telemetry" / "config.json").write_text(
            '{"schema_version":1,"mode":"disabled"}', encoding="utf-8",
        )
        assert client.post(f"/tasks/{a.json()['id']}/approve", headers=AUTH).status_code == 200
        assert client.post("/claim", headers=AUTH, json={
            "worker_id": "private-worker", "repo": REPO_B,
        }).status_code == 200
        # A real service-owned rejection has no admitted initiating task.
        rejection = client.post("/producer-scopes/handoff", headers=AUTH, json={
            "repo": REPO_A, "source": "private-source",
            "producer_id": "private-producer", "expected_generation": 0,
        })
        assert rejection.status_code == 503
        health = client.get("/health", headers=AUTH).json()
        assert health["consumer_telemetry_contract"]["source_schemas"] == ["dispatch.lifecycle.v1"]
        wait_for(lambda: consumer.owner.stats()["pipe_writes"] == 5)
        wait_for(lambda: all(
            (root / "readback.jsonl").exists()
            and len((root / "readback.jsonl").read_text(encoding="utf-8").splitlines()) == count
            for root, count in zip(roots, (2, 2, 1))
        ))
        assert consumer.owner.stats()["dropped_invalid"] == 0
    assert consumer.owner.closed and consumer.owner.writer.join(0)
    rows = [readback(root) for root in roots]
    assert [[r["record"]["event"]["event"] for r in group] for group in rows] == [
        ["task.proposed", "task.approved"], ["task.created", "task.claimed"],
        ["producer_scope.transition_rejected"],
    ]
    all_rows = [row for group in rows for row in group]
    assert sorted(row["record"]["sequence"] for row in all_rows) == [1, 2, 3, 4, 5]
    assert len({row["record"]["source_instance"] for row in all_rows}) == 1
    assert len({row["record"]["route_generation"] for row in all_rows}) == 3
    for root, group in zip(roots, rows):
        assert (root / "eof.json").exists()
        assert len({row["pid"] for row in group}) == 1
        for row in group:
            assert Path(row["cwd"]) == root
            assert row["argv"] == [LITERAL]
            assert "FIXTURE_SECRET" not in row["env"] and "HARNESS_POLICY" not in row["env"]
            assert row["record"]["source"] == "agent-dispatch"
            assert row["record"]["source_schema"] == "dispatch.lifecycle.v1"
            encoded = json.dumps(row["record"])
            assert "private" not in encoded and REPO_A not in encoded and REPO_B not in encoded
    assert all(binding.pipe.closed for binding in consumer.owner.bindings)


@pytest.mark.parametrize("mode", ["disabled", "invalid", "absent", "local-disable"])
def test_bound_optout_preserves_ordinary_result_without_legacy_or_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    root = receiver(tmp_path / "origin")
    selected = root / ".agent-telemetry" / "config.json"
    if mode == "disabled":
        selected.write_text('{"schema_version":1,"mode":"disabled"}')
    elif mode == "invalid":
        selected.write_text("{")
    elif mode == "absent":
        selected.unlink()
    else:
        (selected.parent / "config.local.json").write_text('{"schema_version":1,"mode":"disabled"}')
    seen: list[dict[str, Any]] = []
    telemetry.set_telemetry_sink(seen.append)
    app = create_app(
        TaskQueue(tmp_path / "tasks.db"), token="fixture-data", enable_mcp=False,
        consumer_telemetry={"schema_version": 1, "repositories": {REPO_A: str(root)}},
    )
    try:
        with TestClient(app) as client:
            assert client.post("/tasks", headers=AUTH, json={"title": "ok", "repo": REPO_A}).status_code == 200
            consumer = app.state.consumer_telemetry
            assert consumer.owner.writer is None and consumer.owner.stats()["offered"] == 1
            assert seen == []
        assert not (root / "readback.jsonl").exists()
        assert all(binding.pipe is None for binding in consumer.owner.bindings)
    finally:
        telemetry.clear_telemetry_sink()


def test_normal_file_loader_normalizes_once_without_admitting_a_different_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import server

    admitted = "https://git.example.test/team/project.git.git"
    unlisted = "https://git.example.test/team/project.git"
    root = receiver(tmp_path / "origin")
    policy_file(tmp_path, {admitted: str(root)})
    monkeypatch.setattr(server, "_owns_active_route", lambda: True)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db"), token="fixture-data"))
    with TestClient(app) as client:
        first = client.post("/tasks", headers=AUTH, json={
            "title": "admitted", "repo": admitted, "proposed": True,
        })
        other = client.post("/tasks", headers=AUTH, json={"title": "unlisted", "repo": unlisted})
        assert first.status_code == other.status_code == 200
        assert first.json()["repo"] == "git.example.test/team/project.git"
        assert other.json()["repo"] == "git.example.test/team/project"
        assert client.post(f"/tasks/{first.json()['id']}/approve", headers=AUTH).status_code == 200
        wait_for(lambda: (root / "readback.jsonl").exists() and len(
            (root / "readback.jsonl").read_text(encoding="utf-8").splitlines()
        ) == 2)
        assert app.state.consumer_telemetry.owner.stats()["dropped_route"] == 1
    records = [row["record"] for row in readback(root)]
    assert [row["event"]["event"] for row in records] == ["task.proposed", "task.approved"]
    assert [row["sequence"] for row in records] == [1, 3]
    assert (root / "eof.json").exists()


def test_unknown_origin_and_unauthenticated_owner_never_borrow_service_route(tmp_path: Path) -> None:
    root = receiver(tmp_path / "service")
    policy = {"schema_version": 1, "service_root": str(root)}
    for authenticated in (True, False):
        consumer = consumer_telemetry.ConsumerTelemetry(policy, authenticated=authenticated)
        try:
            consumer.start()
            consumer.emit(telemetry.task_lifecycle_event("task.created", {
                "repo": REPO_A, "status": "queued",
            }))
            assert consumer.owner is None or consumer.owner.stats()["enqueued"] == 0
            if authenticated:
                assert consumer.owner is not None
                assert consumer.owner.stats()["dropped_route"] == 1
        finally:
            consumer.close()
    assert not (root / "readback.jsonl").exists()


def test_deferred_real_evaluator_keeps_task_origin_after_another_request(tmp_path: Path) -> None:
    from agent_dispatch.identity import canonicalize_remote
    from agent_dispatch.verification import evaluate_submitted_task

    roots = [receiver(tmp_path / name) for name in ("alpha", "beta")]
    queue = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "evaluate.py"
    script.write_text(
        'import json,sys\njson.load(sys.stdin)\n'
        'json.dump({"decision":"confirm","reason":"fixture-confirmed"},sys.stdout)\n',
        encoding="utf-8",
    )
    queue.register_registration("evaluator", {
        "repo": canonicalize_remote(REPO_A), "evaluator_ref": "fixture",
        "evaluator_spec": {"scripts": {"fixture": [sys.executable, str(script)]}},
    }, machine="fixture-host", env="default")
    app = create_app(
        queue, token="fixture-data", enable_mcp=False, verification_interval=0,
        consumer_telemetry={
            "schema_version": 1,
            "repositories": {REPO_A: str(roots[0]), REPO_B: str(roots[1])},
        },
    )
    with TestClient(app) as client:
        created = client.post("/tasks", headers=AUTH, json={
            "title": "alpha", "repo": REPO_A, "require_verification": True,
            "evaluator_ref": "fixture",
        })
        assert created.status_code == 200
        task_id = created.json()["id"]
        assert client.post("/claim", headers=AUTH, json={
            "worker_id": "fixture-worker", "repo": REPO_A,
        }).status_code == 200
        for action in ("start", "complete"):
            assert client.post(f"/tasks/{task_id}/{action}", headers=AUTH, json={
                "worker_id": "fixture-worker",
            }).status_code == 200
        assert client.post("/tasks", headers=AUTH, json={
            "title": "beta", "repo": REPO_B,
        }).status_code == 200
        report = evaluate_submitted_task(
            queue, task_id, bus=app.state.bus, trigger="submitted",
            current_machine="fixture-host", current_env="default",
        )
        assert report.get("applied", []), report
        assert report["applied"][0]["decision"] == "complete"
        wait_for(lambda: all(
            (root / "readback.jsonl").exists()
            and len((root / "readback.jsonl").read_text(encoding="utf-8").splitlines()) == count
            for root, count in zip(roots, (5, 1))
        ))
    a, b = [readback(root) for root in roots]
    assert [row["record"]["event"]["event"] for row in a] == [
        "task.created", "task.claimed", "task.started", "task.submitted", "task.completed",
    ]
    assert [row["record"]["sequence"] for row in a] == [1, 2, 3, 4, 6]
    assert [row["record"]["sequence"] for row in b] == [5]
    assert all((root / "eof.json").exists() for root in roots)


def test_configured_policy_does_not_import_legacy_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_file(tmp_path, {})

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("bound policy imported legacy sink")

    monkeypatch.setattr(telemetry, "load_sink_from_config", forbidden)
    monkeypatch.setattr(telemetry, "load_sink_from_env", forbidden)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db"), token="fixture-data"))
    assert not app.state.consumer_telemetry.legacy


def test_startup_failure_retires_the_owner_without_changing_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import periodic_relevance

    root = receiver(tmp_path / "origin")
    app = create_app(
        TaskQueue(tmp_path / "tasks.db"), token="fixture-data", enable_mcp=False,
        consumer_telemetry={"schema_version": 1, "repositories": {REPO_A: str(root)}},
    )

    def refused(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("original startup failure")

    monkeypatch.setattr(periodic_relevance, "PeriodicRelevanceMaintenance", refused)
    with pytest.raises(RuntimeError, match="original startup failure"), TestClient(app):
        pass
    consumer = app.state.consumer_telemetry
    assert consumer.owner.closed and consumer.owner.writer.join(0)
    assert all(binding.pipe is None for binding in consumer.owner.bindings)


def test_absent_policy_preserves_legacy_and_malformed_policy_refuses(
    tmp_path: Path,
) -> None:
    assert consumer_telemetry.load_policy() is LEGACY
    root = install_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "consumer-telemetry.json").write_text("{")
    assert consumer_telemetry.load_policy() == {}


def test_dangling_policy_is_unreadable_not_absent_and_never_loads_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert consumer_telemetry.load_policy() is LEGACY
    directory = install_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "consumer-telemetry.json"
    path.symlink_to(tmp_path / "absent-policy-target.json")
    assert path.is_symlink() and not path.exists()
    assert consumer_telemetry.load_policy() == {}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dangling policy loaded a legacy sink")

    monkeypatch.setattr(telemetry, "load_sink_from_config", forbidden)
    monkeypatch.setattr(telemetry, "load_sink_from_env", forbidden)
    app = build_app(Config(db_path=str(tmp_path / "tasks.db"), token="fixture-data"))
    assert app.state.consumer_telemetry.refusal == "invalid_config"
    assert not app.state.consumer_telemetry.legacy
    path.unlink()
    assert consumer_telemetry.load_policy() is LEGACY


@pytest.mark.parametrize("behavior", ["crash", "nonreading"])
def test_real_receiver_failure_preserves_work_and_restart_is_a_new_source(
    tmp_path: Path, behavior: str,
) -> None:
    source = (
        'from pathlib import Path\nPath("entered").write_text("started")\n'
        + ("raise SystemExit(3)\n" if behavior == "crash" else "import time\ntime.sleep(60)\n")
    )
    root = receiver(tmp_path / "origin", source)
    policy = {"schema_version": 1, "repositories": {REPO_A: str(root)}}
    database = tmp_path / "tasks.db"

    def app() -> FastAPI:
        return create_app(
            TaskQueue(database), token="fixture-data", enable_mcp=False,
            consumer_telemetry=policy,
        )

    first = app()
    before = time.monotonic()
    with TestClient(first) as client:
        consumer = first.state.consumer_telemetry
        binding = next(iter(consumer.bindings.values()))
        for index in range(350):
            assert client.post("/tasks", headers=AUTH, json={
                "title": f"fixture {index}", "repo": REPO_A,
            }).status_code == 200
            if binding.state != "active":
                break
        wait_for(lambda: binding.state != "active")
        for _ in range(3):
            assert client.post("/tasks", headers=AUTH, json={
                "title": "after failure", "repo": REPO_A,
            }).status_code == 200
        assert binding.state in ("write_failed", "deadline_exceeded", "start_failed")
    assert time.monotonic() - before < 45
    assert (root / "entered").exists()
    assert binding.pipe.closed and consumer.owner.writer.join(0)
    old_instance = consumer.owner.source_instance
    (root / "receiver.py").write_text(_RECEIVER, encoding="utf-8")
    second = app()
    with TestClient(second) as client:
        assert client.post("/tasks", headers=AUTH, json={
            "title": "after restart", "repo": REPO_A,
        }).status_code == 200
        wait_for(lambda: second.state.consumer_telemetry.owner.stats()["pipe_writes"] == 1)
        wait_for(lambda: (root / "readback.jsonl").exists() and (
            root / "readback.jsonl"
        ).stat().st_size > 0)
    row = readback(root)[0]["record"]
    assert row["source_instance"] != old_instance and row["sequence"] == 1
    assert row["route_generation"] != binding.generation
    assert (root / "eof.json").exists()
