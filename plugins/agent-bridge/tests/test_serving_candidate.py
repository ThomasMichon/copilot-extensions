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
    "wrong-version", "exited", "changed-birth", "wrong-role", "wrong-route", "draining",
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
    elif case == "draining":
        body["draining"] = True
    with pytest.raises(ServingReconcileError):
        value.verify_health(body)
    if case not in ("not-ready", "exited", "draining"):
        with pytest.raises(ServingReconcileError):
            value.terminate()
        client.shutdown.assert_not_called()
    value.launcher.terminate.assert_not_called()
    value.close(completed=False)


def test_candidate_cleanup_does_not_require_readiness(candidate):
    value, body, _, client = candidate
    body["ready"] = False
    body["status"] = "failed"
    body["draining"] = True
    value.terminate()
    client.shutdown.assert_called_once()
    client.drain.assert_not_called()
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


@pytest.mark.parametrize("drained", [
    {"drained": True, "clean": True, "forced": False},
    {"drained": False, "clean": False, "forced": False},
    {"drained": True, "clean": True, "forced": True},
    {"drained": True}, None,
])
def test_published_candidate_requires_nonforcing_clean_drain(candidate, drained):
    from zdd.routing import Endpoint

    value, body, _, client = candidate
    body["draining"] = True
    value.mark_published(Endpoint("127.0.0.1", 9012, 2222, "1.2.3", 8, "123456"))
    client.drain.return_value = drained
    if drained == {"drained": True, "clean": True, "forced": False}:
        value.terminate()
        assert [call[0] for call in client.method_calls] == ["drain", "shutdown"]
    else:
        with pytest.raises(ServingReconcileError, match="did not drain"):
            value.terminate()
        client.shutdown.assert_not_called()
    client.drain.assert_called_once_with(timeout=10.0, poll=1.0, force=False)
    value.launcher.terminate.assert_not_called()
    value.close(completed=False)
    assert receipt_path(value.root, value.launch_id).exists()


def test_published_candidate_retains_worker_if_drain_gate_reopens(candidate):
    from zdd.routing import Endpoint

    value, body, _, client = candidate
    endpoint = Endpoint("127.0.0.1", 9012, 2222, "1.2.3", 8, "123456")
    (value.root / "active.json").write_text(json.dumps({"active": endpoint.to_dict()}))
    body["slot"].update(role="active", active=endpoint.to_dict())
    value.mark_published(endpoint)
    client.drain.return_value = {"drained": True, "clean": True, "forced": False}
    body["draining"] = False
    with pytest.raises(ServingReconcileError, match="drain gate changed"):
        value.terminate()
    client.shutdown.assert_not_called()
    client.undrain.assert_not_called()
    value.close(completed=False)


@pytest.mark.parametrize("case", [
    "callback", "busy", "manual-drain", "unknown-drain", "owner-changed",
    "generation-changed", "native-exited", "native-birth-changed", "already-open",
    "undrain-refused", "undrain-malformed", "undrain-health-closed", "undrain-owner-changed",
])
def test_failed_cleanup_only_reopens_its_own_active_admission(candidate, monkeypatch, case):
    from zdd.routing import Endpoint

    value, body, reference, client = candidate
    endpoint = Endpoint("127.0.0.1", 9012, 2222, "1.2.3", 8, "123456")
    active = endpoint.to_dict()
    (value.root / "active.json").write_text(json.dumps({"active": active}))
    body["slot"].update(role="active", active=active.copy())
    body["draining"] = True if case == "manual-drain" else None if case == "unknown-drain" else False
    value.mark_published(endpoint)
    failure = RuntimeError("predecessor no longer available")

    def drain(**_kwargs):
        body["draining"] = True
        if case == "busy":
            return {"drained": False, "clean": False, "forced": False}
        return {"drained": True, "clean": True, "forced": False}

    def refuse_shutdown():
        if case in ("owner-changed", "generation-changed"):
            changed = {**active, "generation": 9}
            if case == "owner-changed":
                changed.update(pid=3333, process_start_time="999999")
            (value.root / "active.json").write_text(json.dumps({"active": changed}))
            body["slot"].update(active=changed, role="passive" if case == "owner-changed" else "active")
        elif case == "native-exited":
            reference.wait.return_value = True
        elif case == "native-birth-changed":
            monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "654321")
        elif case == "already-open":
            body["draining"] = False
        raise failure

    def undrain():
        if case == "undrain-refused":
            return {"draining": True}
        if case == "undrain-malformed":
            return None
        body["draining"] = case == "undrain-health-closed"
        if case == "undrain-owner-changed":
            changed = {**active, "generation": 9}
            (value.root / "active.json").write_text(json.dumps({"active": changed}))
            body["slot"]["active"] = changed
        return {"draining": False}

    value._before_shutdown = refuse_shutdown
    client.drain.side_effect = drain
    client.undrain.side_effect = undrain
    with pytest.raises((ServingReconcileError, RuntimeError)) as caught:
        value.terminate()
    compensated = case in (
        "callback", "busy", "undrain-refused", "undrain-malformed",
        "undrain-health-closed", "undrain-owner-changed",
    )
    assert client.undrain.call_count == int(compensated)
    if case in ("callback", "manual-drain", "unknown-drain", "already-open"):
        assert caught.value is failure, "successful compensation hid the original failure"
    elif case != "busy":
        assert "admission compensation failed" in str(caught.value)
        assert caught.value.__cause__ is failure
    if case in ("callback", "busy", "already-open"):
        assert body["draining"] is False
    if case in ("manual-drain", "unknown-drain"):
        assert body["draining"] is True
    client.shutdown.assert_not_called()
    value.launcher.terminate.assert_not_called()
    assert receipt_path(value.root, value.launch_id).exists()


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


@pytest.mark.parametrize(("status", "ready", "service"), [
    ("ok", True, "agent-bridge"), ("ok", False, "agent-bridge"),
    ("failed", False, "agent-bridge"), ("ok", False, "other"),
])
def test_identity_probe_preserves_default_health_readiness(
    monkeypatch, status, ready, service,
):
    import urllib.request
    from unittest.mock import MagicMock
    from agent_bridge.service_process_state import _service_health_on_port

    body = {"status": status, "ready": ready, "service": service, "slot": {"pid": 2222}}
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(body).encode()
    monkeypatch.setattr(urllib.request, "urlopen", Mock(return_value=response))
    assert _service_health_on_port(9012) == (
        body if status == "ok" and ready and service == "agent-bridge" else None
    )
    assert _service_health_on_port(9012, require_ready=False) == (
        body if service == "agent-bridge" else None
    )
