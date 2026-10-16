"""Worker identity is independent of a broker/redirector launch handle."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from zdd import diagnostics, process_exit

from agent_bridge.serving_candidate import ServingCandidate, receipt_path, record_worker
from agent_bridge.serving_reconcile import ServingReconcileError


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    health, client = Mock(), Mock()
    value = ServingCandidate(tmp_path, 9012, "1.2.3", health, lambda: client)
    value.launcher = Mock(pid=1111)
    reference = Mock(pid=2222, start_time="123456")
    reference.wait.return_value = False
    monkeypatch.setattr(process_exit, "open_process", Mock(return_value=reference))
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "123456")
    receipt_path(tmp_path, value.launch_id).write_text(json.dumps({
        "launch_id": value.launch_id, "pid": 2222, "process_start_time": "123456",
    }))
    body = {
        "status": "ok", "service": "agent-bridge", "version": "1.2.3", "ready": True,
        "slot": {"pid": 2222, "role": "unknown", "active": None},
    }
    health.return_value = body
    return value, body, reference, client


def test_candidate_uses_retained_actual_worker_not_launcher(candidate):
    value, body, reference, _ = candidate
    value.verify_health(body)
    assert value.pid == 2222
    assert value.launcher.pid == 1111
    assert value.process is reference
    process_exit.open_process.assert_called_once_with(2222, "123456")
    value.verify_health(body)
    process_exit.open_process.assert_called_once()
    value.close(completed=True)
    reference.close.assert_called_once()
    assert not receipt_path(value.root, value.launch_id).exists()


@pytest.mark.parametrize("case", [
    "wrong-launch", "missing-launch", "wrong-pid", "not-ready", "wrong-service",
    "wrong-version", "exited", "changed-birth", "wrong-role", "wrong-route",
])
def test_candidate_refuses_unverified_responder(candidate, monkeypatch, case):
    value, body, reference, client = candidate
    if case == "wrong-launch":
        receipt_path(value.root, value.launch_id).write_text(json.dumps({
            "launch_id": "another-launch", "pid": 2222, "process_start_time": "123456",
        }))
    elif case == "missing-launch":
        receipt_path(value.root, value.launch_id).unlink()
    elif case == "wrong-pid":
        body["slot"]["pid"] = 1111
    elif case == "not-ready":
        body["ready"] = False
    elif case == "wrong-service":
        body["service"] = "other"
    elif case == "wrong-version":
        body["version"] = "1.2.4"
    elif case == "exited":
        reference.wait.return_value = True
    elif case == "changed-birth":
        monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "654321")
    elif case == "wrong-role":
        body["slot"]["role"] = "active"
    elif case == "wrong-route":
        body["slot"]["active"] = {"pid": 2222}
    with pytest.raises(ServingReconcileError):
        value.verify_health(body)
    if case not in ("not-ready", "exited"):
        with pytest.raises(ServingReconcileError):
            value.terminate()
        client.shutdown.assert_not_called()
    value.launcher.terminate.assert_not_called()
    value.close(completed=False)


def test_candidate_cleanup_does_not_require_readiness(candidate):
    value, body, _, client = candidate
    body["ready"] = False
    body["status"] = "failed"
    value.terminate()
    client.shutdown.assert_called_once()
    value.launcher.terminate.assert_not_called()
    value.close(completed=False)
    assert receipt_path(value.root, value.launch_id).exists()


def test_candidate_without_worker_receipt_never_signals_launcher(candidate):
    value, body, _, client = candidate
    receipt_path(value.root, value.launch_id).unlink()
    assert value.pid is None
    with pytest.raises(ServingReconcileError):
        value.verify_health(body)
    with pytest.raises(ServingReconcileError):
        value.terminate()
    process_exit.open_process.assert_not_called()
    value.launcher.terminate.assert_not_called()
    client.shutdown.assert_not_called()


def test_candidate_native_birth_mismatch_is_not_a_pid_fallback(candidate, monkeypatch):
    value, body, _, client = candidate
    monkeypatch.setattr(
        process_exit, "open_process",
        Mock(side_effect=ProcessLookupError("native identity changed")),
    )
    with pytest.raises(ProcessLookupError):
        value.verify_health(body)
    assert value.process is None
    client.shutdown.assert_not_called()
    value.launcher.terminate.assert_not_called()


def test_worker_receipt_records_actual_self_and_is_not_overwritten(tmp_path, monkeypatch):
    from agent_bridge import serving_candidate

    launch_id = "ab" * 16
    monkeypatch.setattr(serving_candidate.os, "getpid", lambda: 2222)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: str(pid + 1000))
    record_worker(tmp_path, launch_id)
    path = receipt_path(tmp_path, launch_id)
    before = path.read_bytes()
    assert json.loads(before) == {
        "launch_id": launch_id, "pid": 2222, "process_start_time": "3222",
    }
    with pytest.raises(FileExistsError):
        record_worker(tmp_path, launch_id)
    assert path.read_bytes() == before
