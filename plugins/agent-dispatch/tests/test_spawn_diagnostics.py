"""Real application output and synthetic call ordering; no child or network."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from agent_dispatch import logging_setup, supervise_cli
from agent_dispatch.client import DispatchError
from agent_dispatch.ordinary_bridge_diagnostics import run
from agent_dispatch.spawn_diagnostics import SpawnDiagnostics
from agent_dispatch.supervisor import Supervisor


@pytest.fixture
def sink(tmp_path: Path):
    package = logging.getLogger("agent-dispatch")
    previous = package.handlers[:], package.level, package.propagate
    package.handlers = []
    path = logging_setup.configure_file_logging("supervisor", install_dir=tmp_path)

    def read() -> list[dict]:
        if not path.exists():
            return []
        return [
            {**row, "stage_record": json.loads(row["message"])}
            for line in path.read_text(encoding="utf-8").splitlines()
            if (row := json.loads(line))["logger"] == "agent-dispatch.spawn"
        ]

    yield read
    for handler in package.handlers:
        handler.close()
    package.handlers, package.level, package.propagate = previous


def lane(monkeypatch: pytest.MonkeyPatch) -> Supervisor:
    supervisor = Supervisor(Mock(), spawn_fn=Mock(return_value=(True, {"session": "body-a"})),
                            machine="synthetic", supervisor_id="lane-a",
                            heartbeat=False, publish_activity=False, recover=False,
                            nudge=False, consistency_sweep=False)
    for name in (
        "reconcile", "reconcile_reserving", "release_requested_bodies",
        "bind_headless_owner_sessions", "renew_restricted_grants", "nudge_idle_headless_tasks",
        "cool_dormant_bodies", "release_resumed_cold_tasks", "recover_stranded_cold_reservations",
        "recover_stranded_releasing_reservations", "redrive_unclaimed_spawns",
        "_sync_event_subscriptions",
    ):
        monkeypatch.setattr(supervisor, name, Mock())
    monkeypatch.setattr(supervisor, "_failed_spawn_counts", lambda: {})
    monkeypatch.setattr(supervisor, "_eligible", lambda _: [{"id": "task-a", "generation": 4}])
    monkeypatch.setattr(supervisor, "_active_reservations", lambda: [])
    monkeypatch.setattr(supervisor, "_log_dead_lettered", lambda *_: set())
    monkeypatch.setattr(supervisor, "_prepare_spawn_task", lambda task, _: task)
    monkeypatch.setattr(supervisor, "_recheck_governance", lambda _: None)
    supervisor.client.reserve_spawn.return_value = {
        "reserved": True, "reservation": {"key": "reservation-a", "creation_key": "creation-a"},
    }
    return supervisor


def test_lane_configures_its_own_sink_before_scope_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(supervise_cli, "configure_file_logging", lambda name: calls.append(name))

    def scope(_args: argparse.Namespace) -> None:
        assert calls == ["supervisor"]
        return None

    monkeypatch.setattr(supervise_cli, "_scope_repo", scope)
    monkeypatch.setattr(supervise_cli, "_resolve_cli_module", lambda: Mock(_REPO_UNRESOLVED="no repo"))
    assert supervise_cli._run_supervise(argparse.Namespace()) == 2


def test_entry_is_retained_before_blocking_stage_and_failure_has_no_success(sink, monkeypatch) -> None:
    supervisor = lane(monkeypatch)
    failure = RuntimeError("synthetic database busy")

    def blocked() -> None:
        rows = sink()
        assert [(r["stage_record"]["stage"], r["stage_record"]["outcome"]) for r in rows] == [
            ("cycle", "entered"), ("reconcile", "entered"),
        ]
        raise failure

    monkeypatch.setattr(supervisor, "reconcile", blocked)
    with pytest.raises(RuntimeError) as caught:
        supervisor.poll_once()
    assert caught.value is failure
    rows = sink()
    events = [r["stage_record"] for r in rows]
    assert [(e["stage"], e["outcome"]) for e in events[-2:]] == [
        ("reconcile", "error"), ("cycle", "error"),
    ]
    assert events[-2]["error"] == "RuntimeError: synthetic database busy"
    assert events[1]["call_id"] == events[-2]["call_id"]
    assert events[-2]["elapsed_seconds"] >= 0
    assert len({e["cycle_id"] for e in events}) == 1
    assert all(r["pid"] and r["ts"].endswith("+00:00") and r["runtime_version"] for r in rows)
    assert all("process_start_token" in r and "runtime_prefix" in r for r in rows)
    supervisor.spawn_fn.assert_not_called()


@pytest.mark.parametrize("decision", ["capacity", "governance", "contention", "concurrency", "retry-budget"])
def test_scheduling_explains_no_backend(sink, monkeypatch, decision: str) -> None:
    supervisor = lane(monkeypatch)
    expected = {
        "capacity": "no_capacity", "governance": "governance_before_reserve",
        "contention": "reservation_contended", "concurrency": "concurrency_limit",
    }
    if decision == "capacity":
        supervisor.capacity_gate = lambda _: False
    elif decision == "governance":
        monkeypatch.setattr(supervisor, "_recheck_governance", lambda _: {"status": "refused"})
    elif decision == "contention":
        supervisor.client.reserve_spawn.return_value = {"reserved": False}
    elif decision == "concurrency":
        monkeypatch.setattr(supervisor, "_active_reservations", lambda: ["active"])
    else:
        monkeypatch.setattr(supervisor, "_log_dead_lettered", lambda *_: {"task-a"})
    assert supervisor.poll_once() == []
    events = [row["stage_record"] for row in sink()]
    if decision == "retry-budget":
        assert any(e.get("exhausted_count") == 1 for e in events)
    else:
        assert any(e.get("reason") == expected[decision] for e in events)
    supervisor.spawn_fn.assert_not_called()


def test_admitted_spawn_and_bookkeeping_are_correlated_once(sink, monkeypatch) -> None:
    supervisor = lane(monkeypatch)
    def backend(_task: dict) -> tuple[bool, dict]:
        records = [r["stage_record"] for r in sink() if r["stage_record"]["stage"] == "backend"]
        assert [r["outcome"] for r in records] == ["entered"]
        return True, {"session": "body-a"}

    supervisor.spawn_fn.side_effect = backend
    assert supervisor.poll_once() == ["task-a"]
    supervisor.spawn_fn.assert_called_once()
    supervisor.client.record_spawn.assert_called_once()
    events = [row["stage_record"] for row in sink()]
    for stage in ("prepare", "backend", "record_spawn"):
        records = [e for e in events if e["stage"] == stage]
        assert [e["outcome"] for e in records] == ["entered", "returned"]
        assert all(e["task_id"] == "task-a" and e["generation"] == 4 and
                   e["reservation_key"] == "reservation-a" and e["creation_key"] == "creation-a"
                   for e in records)
    assert events[-1]["spawned_count"] == 1


def test_failed_backend_sanitizes_application_records_not_primary_detail(sink, monkeypatch) -> None:
    monkeypatch.setenv("SYNTHETIC_API_TOKEN", "sensitive-fixture-value")
    supervisor = lane(monkeypatch)
    detail = "connection refused allocation-a sensitive-fixture-value"
    supervisor.spawn_fn.return_value = False, {"error": detail}
    assert supervisor.poll_once() == []
    supervisor.client.fail_spawn.assert_called_once_with("reservation-a", detail=detail)
    text = logging.getLogger("agent-dispatch").handlers[0].path.read_text(encoding="utf-8")
    assert "connection refused allocation-a" in text
    assert "sensitive-fixture-value" not in text


def test_malformed_prompt_is_not_copied_into_diagnostics(sink, monkeypatch) -> None:
    supervisor = lane(monkeypatch)
    supervisor.capacity_gate = lambda _: False
    monkeypatch.setattr(supervisor, "_eligible", lambda _: [{
        "id": "task-a", "generation": {"prompt": "opaque malformed generation"},
        "prompt": {"environment": "opaque malformed task"},
    }])
    assert supervisor.poll_once() == []
    text = json.dumps(sink())
    assert "opaque malformed" not in text
    assert any(row["stage_record"].get("reason") == "no_capacity" for row in sink())


def test_serve_does_not_reintroduce_unfiltered_cycle_traceback(sink, monkeypatch) -> None:
    monkeypatch.setenv("SYNTHETIC_API_TOKEN", "sensitive-fixture-value")
    supervisor = lane(monkeypatch)
    monkeypatch.setattr(supervisor, "_eligible", lambda _: [{
        "id": "task-a", "generation": 4, "prompt": "synthetic private prompt",
    }])
    error = RuntimeError("launch allocation-a sensitive-fixture-value synthetic private prompt")
    supervisor.spawn_fn.side_effect = error
    monkeypatch.setattr(supervisor, "wait_for_turn_end", Mock(side_effect=KeyboardInterrupt))
    supervisor.serve()
    text = logging.getLogger("agent-dispatch").handlers[0].path.read_text(encoding="utf-8")
    assert "launch allocation-a" in text and "supervision cycle failed (RuntimeError)" in text
    assert "sensitive-fixture-value" not in text and "synthetic private prompt" not in text
    assert str(error) == "launch allocation-a sensitive-fixture-value synthetic private prompt"


def test_internally_caught_reservation_error_is_filtered_by_application_owner(sink, monkeypatch) -> None:
    monkeypatch.setenv("SYNTHETIC_API_TOKEN", "sensitive-fixture-value")
    supervisor = lane(monkeypatch)
    error = DispatchError(503, "reservation-read allocation-a sensitive-fixture-value")
    supervisor.client.list_reservations.side_effect = error
    assert supervisor._list_reservations_safe() == []
    text = logging.getLogger("agent-dispatch").handlers[0].path.read_text(encoding="utf-8")
    assert "reservation-read allocation-a" in text
    assert "sensitive-fixture-value" not in text
    assert "sensitive-fixture-value" in str(error)


def test_internally_caught_cold_resume_traceback_keeps_cause_not_credential(sink, monkeypatch) -> None:
    monkeypatch.setenv("SYNTHETIC_API_TOKEN", "sensitive-fixture-value")
    supervisor = lane(monkeypatch)
    monkeypatch.setattr(supervisor, "_pool_reservations", lambda **_: [{
        "key": "reservation-a", "task_id": "task-a", "startup_complete": True,
        "session_handle": "local-body:body-a",
    }])
    supervisor.client.get.return_value = {"id": "task-a", "owner": "worker-a", "status": "suspended"}
    supervisor.local_body_target_dir_fn = lambda _: None
    error = RuntimeError("resume allocation-a sensitive-fixture-value")
    supervisor.local_resume_fn = Mock(side_effect=error)
    assert Supervisor.release_resumed_cold_tasks(supervisor, now=1000) == 0
    assert supervisor._resume_retry_after["reservation-a"] > 1000
    text = logging.getLogger("agent-dispatch").handlers[0].path.read_text(encoding="utf-8")
    assert "RuntimeError: resume allocation-a" in text
    assert "sensitive-fixture-value" not in text
    assert "sensitive-fixture-value" in str(error)


@pytest.mark.parametrize("explicit", [False, True])
def test_application_exception_keeps_sanitized_cause_chain(sink, monkeypatch, explicit) -> None:
    monkeypatch.setenv("SYNTHETIC_API_TOKEN", "sensitive-fixture-value")
    cause = PermissionError(13, "permission allocation-a sensitive-fixture-value")
    wrapper = RuntimeError("companion command unavailable")
    try:
        try:
            raise cause
        except PermissionError:
            if explicit:
                raise wrapper from cause
            raise wrapper
    except RuntimeError:
        logging.getLogger("agent-dispatch.worker").exception("lifecycle failed")
    handler = logging.getLogger("agent-dispatch").handlers[0]
    event = json.loads(handler.path.read_text(encoding="utf-8").splitlines()[-1])
    assert "RuntimeError: companion command unavailable" in event["exception"]
    assert "PermissionError: [Errno 13] permission allocation-a" in event["exception"]
    assert "sensitive-fixture-value" not in json.dumps(event)
    assert wrapper.__context__ is cause
    assert wrapper.__cause__ is (cause if explicit else None)


@pytest.mark.parametrize("backoff", [False, True])
def test_standalone_lane_maintains_expired_logs_before_work_or_backoff(sink, monkeypatch, backoff) -> None:
    supervisor = lane(monkeypatch)
    handler = logging.getLogger("agent-dispatch").handlers[0]
    now = datetime.now(timezone.utc)
    handler.clock = lambda: now
    logging.getLogger("agent-dispatch.worker").info("expired synthetic evidence")
    assert "expired synthetic evidence" in handler.path.read_text(encoding="utf-8")
    now += timedelta(days=31)
    if backoff:
        monkeypatch.setattr(supervisor, "_recheck_governance", lambda _: {"status": "refused"})
    monkeypatch.setattr(supervisor, "wait_for_turn_end", Mock(side_effect=KeyboardInterrupt))
    supervisor.serve()
    assert handler._last_maintenance == now
    assert "expired synthetic evidence" not in handler.path.read_text(encoding="utf-8")


def test_sink_degradation_is_visible_without_changing_stage_result(sink, monkeypatch, capsys) -> None:
    handler = logging.getLogger("agent-dispatch").handlers[0]

    def unavailable(*_args) -> None:
        raise OSError("synthetic sink unavailable")

    monkeypatch.setattr(handler, "encoder", unavailable)
    with SpawnDiagnostics().stage("synthetic_call") as result:
        result["ok"] = True
    assert handler.last_error == "OSError"
    assert "diagnostics degraded" in capsys.readouterr().err


def test_bridge_failure_keeps_benign_cause_not_credentials_or_prompt(sink, monkeypatch) -> None:
    monkeypatch.setenv("SYNTHETIC_API_TOKEN", "sensitive-fixture-value")
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "0")
    diagnostics = SpawnDiagnostics(task_id="task-a", reservation_key="reservation-a",
                                   secrets={"STARTUP_PROMPT_SECRET": "private model prompt"})
    runner = Mock(return_value=subprocess.CompletedProcess(
        [], 69, '{"error":"database busy at allocation-a","prompt":"private model prompt"}',
        "connection refused allocation-a sensitive-fixture-value private model prompt",
    ))
    result = run(["bridge"], ["--json", "create", "example"], diagnostics=diagnostics,
                 runner=runner, timeout=5, prompt_input="private model prompt")
    assert result.returncode == 69
    runner.assert_called_once()
    records = [row["stage_record"] for row in sink()]
    assert [e["outcome"] for e in records] == ["entered", "returned"]
    assert records[-1]["returncode"] == 69
    serialized = json.dumps(records)
    assert "sensitive-fixture-value" not in serialized
    assert "private model prompt" not in serialized
    assert "database busy at allocation-a" in serialized
    assert "connection refused allocation-a" in serialized


def test_timeout_does_not_emit_argv_or_invent_exit(sink, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_BRIDGE_CLI_TRACE", "0")
    timeout = subprocess.TimeoutExpired(["bridge", "opaque argument"], 5, stderr=b"database busy")
    runner = Mock(side_effect=timeout)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run(["bridge"], ["--json", "send", "body-a"], diagnostics=SpawnDiagnostics(),
            runner=runner, timeout=5)
    assert caught.value is timeout
    events = [row["stage_record"] for row in sink()]
    assert events[-1]["outcome"] == "error"
    assert events[-1]["error_type"] == "TimeoutExpired"
    assert all("returncode" not in e for e in events)
    assert "opaque argument" not in json.dumps(events)
    assert "database busy" in json.dumps(events)
