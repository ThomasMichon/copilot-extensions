"""Drain diagnostics preserve the real control path and expose blocking stages."""

from __future__ import annotations

import json
import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import status_monitor_drain as diagnostics
from agent_worktrees.status_monitor_cutover import ControlClient, ControlServer

pytestmark = pytest.mark.guard


def _events(directory: Path) -> list[dict]:
    path = directory / "lifecycle.log"
    return [
        json.loads(line) for line in path.read_text().splitlines(keepends=True)
        if line.endswith("\n")
    ] if path.exists() else []


class Clock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.value

    monotonic = time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


@pytest.mark.parametrize("reason", diagnostics.BUSY_REASONS)
@pytest.mark.parametrize("force", [False, True])
def test_timeout_logs_exact_busy_gate_without_changing_force_semantics(
    tmp_path, monkeypatch, reason, force,
):
    clock = Clock()
    monkeypatch.setattr(diagnostics, "time", clock)
    result = diagnostics.drain_with_diagnostics(
        state_lock=threading.RLock(), enter=lambda: None, cleanup=lambda: None,
        busy=lambda: [reason], observe=lambda: {reason: 2, "lifecycle": 1},
        directory=tmp_path, port=12345, timeout=2.0, poll=0.01, force=force,
    )
    assert result == {
        "drained": force, "clean": False, "forced": force, "busy_sessions": [reason],
    }
    assert set(clock.sleeps) == {0.05}
    events = _events(tmp_path)
    last = events[-1]
    assert last["action"] == "monitor-drain" and last["outcome"] == "fail"
    assert last["detail"]["phase"] == "timeout"
    assert last["detail"]["busy_reasons"] == [reason]
    assert last["detail"]["observed_counts"] == {reason: 2, "lifecycle": 1}
    assert last["detail"]["remaining_ms"] == 0
    assert last["detail"]["requested_force"] is force
    assert last["detail"]["receipt"] == {
        "drained": force, "clean": False, "forced": force,
    }
    assert len({event["detail"]["trace_id"] for event in events}) == 1


def test_progress_is_bounded_and_completion_keeps_the_existing_receipt(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(diagnostics, "time", clock)
    result = diagnostics.drain_with_diagnostics(
        state_lock=threading.RLock(), enter=lambda: None, cleanup=lambda: None,
        busy=lambda: ["sweep"] if clock.value < 112.0 else [],
        observe=lambda: {"sweep": int(clock.value < 112.0), "lifecycle": 1},
        directory=tmp_path, port=12345, timeout=20.0, poll=0.1, force=False,
    )
    assert result == {"drained": True, "clean": True, "forced": False, "busy_sessions": []}
    checks = [
        event["detail"]["elapsed_ms"] for event in _events(tmp_path)
        if event["detail"]["phase"] == "busy-check"
    ]
    assert len(checks) == 3
    assert all(b - a >= 5000 for a, b in zip(checks, checks[1:]))
    assert _events(tmp_path)[-1]["detail"]["phase"] == "complete"
    assert _events(tmp_path)[-1]["detail"]["observed_counts"]["lifecycle"] == 1


def test_clock_still_starts_after_admission_and_cleanup_failure_is_visible(
    tmp_path, monkeypatch,
):
    clock = Clock()
    monkeypatch.setattr(diagnostics, "time", clock)

    def enter():
        clock.value += 20

    result = diagnostics.drain_with_diagnostics(
        state_lock=threading.RLock(), enter=enter, cleanup=lambda: None,
        busy=lambda: ["hook"], observe=lambda: {"hook": 1},
        directory=tmp_path, port=12345, timeout=1, poll=1, force=False,
    )
    assert result["drained"] is False
    assert clock.value == 121
    assert _events(tmp_path)[-1]["detail"]["elapsed_ms"] == 21000

    def cleanup():
        raise RuntimeError("fixture failure must propagate")

    with pytest.raises(RuntimeError, match="fixture failure must propagate"):
        diagnostics.drain_with_diagnostics(
            state_lock=threading.RLock(), enter=lambda: None, cleanup=cleanup,
            busy=lambda: [], observe=lambda: {},
            directory=tmp_path, port=12345, timeout=1, poll=1, force=False,
        )
    assert _events(tmp_path)[-1]["detail"]["phase"] == "aborted"
    assert "fixture failure" not in (tmp_path / "lifecycle.log").read_text()


def test_log_failure_warns_once_without_changing_a_successful_drain(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(diagnostics.lifecycle, "record", lambda *args, **kwargs: None)
    assert diagnostics.drain_with_diagnostics(
        state_lock=threading.RLock(), enter=lambda: None, cleanup=lambda: None,
        busy=lambda: [], observe=lambda: {},
        directory=tmp_path, port=12345, timeout=1, poll=1, force=False,
    )["clean"] is True
    assert capsys.readouterr().err.count("log-write-failed") == 1


@pytest.mark.parametrize("error_type", [ValueError, BrokenPipeError])
def test_log_and_warning_failures_preserve_the_control_response(
    tmp_path, monkeypatch, error_type,
):
    class FailedWarningStream(io.StringIO):
        attempts = 0

        def write(self, text):
            self.attempts += 1
            raise error_type("fixture unavailable warning stream")

    stream = FailedWarningStream()
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(stderr=stream))
    monkeypatch.setattr(diagnostics.lifecycle, "record", lambda *args, **kwargs: None)
    assert diagnostics.drain_with_diagnostics(
        state_lock=threading.RLock(), enter=lambda: None, cleanup=lambda: None,
        busy=lambda: [], observe=lambda: {},
        directory=tmp_path, port=12345, timeout=1, poll=1, force=False,
    ) == {"drained": True, "clean": True, "forced": False, "busy_sessions": []}
    assert stream.attempts == 1


def test_failed_warning_does_not_replace_the_original_lifecycle_exception(tmp_path, monkeypatch):
    stream = io.StringIO()
    stream.close()
    monkeypatch.setattr(diagnostics, "sys", SimpleNamespace(stderr=stream))
    monkeypatch.setattr(diagnostics.lifecycle, "record", lambda *args, **kwargs: None)
    trace = diagnostics.DrainTrace(tmp_path, role="daemon", port=12345, action="promote")
    with pytest.raises(RuntimeError, match="fixture original lifecycle failure"):
        with trace.stage("activate-publisher"):
            raise RuntimeError("fixture original lifecycle failure")


def test_trace_omits_unrecognized_content_and_nonfinite_budgets(tmp_path):
    trace = diagnostics.DrainTrace(
        tmp_path, role="daemon", port=12345, timeout=float("inf"), poll=float("nan"),
        observe=lambda: {
            "hook": 1, "classify": "private fixture content", "payload": "private fixture content",
        },
    )
    trace.emit("busy-check", reasons=["hook", "private fixture content"])
    detail = _events(tmp_path)[0]["detail"]
    assert detail["timeout_s"] is detail["poll_s"] is None
    assert detail["observed_counts"] == {"hook": 1}
    assert detail["invalid_count_fields"] == ["classify"]
    assert detail["busy_reasons"] == ["hook"] and detail["unknown_busy_reason_count"] == 1
    assert "private fixture content" not in (tmp_path / "lifecycle.log").read_text()


def test_real_control_request_logs_before_the_state_lock_can_be_acquired(tmp_path):
    lock = threading.RLock()
    entered = threading.Event()
    result: dict = {}
    errors: list[BaseException] = []

    def handle(action, payload):
        assert action == "drain"
        return diagnostics.drain_with_diagnostics(
            state_lock=lock, enter=entered.set, cleanup=lambda: None,
            busy=lambda: [], observe=lambda: {"hook": 1, "lifecycle": 1},
            directory=tmp_path, port=server.port,
            timeout=float(payload["timeout"]), poll=float(payload["poll"]),
            force=bool(payload["force"]),
        )

    def send():
        try:
            result.update(ControlClient(
                f"http://127.0.0.1:{server.port}", config_dir=tmp_path, timeout=5,
            ).drain(timeout=1, poll=0.1, force=False))
        except BaseException as error:
            errors.append(error)

    server = ControlServer(handle)
    lock.acquire()
    server.start()
    worker = threading.Thread(target=send)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if any(event["detail"]["role"] == "daemon" for event in _events(tmp_path)):
                break
            time.sleep(0.02)
        daemon = [event for event in _events(tmp_path) if event["detail"]["role"] == "daemon"]
        assert daemon[0]["detail"]["phase"] == "state-lock"
        assert daemon[0]["outcome"] == "begin" and daemon[0]["detail"]["remaining_ms"] is None
        assert daemon[0]["detail"]["observed_counts"]["lifecycle"] == 1
        assert not entered.is_set() and worker.is_alive()
    finally:
        lock.release()
        worker.join(timeout=5)
        server.close()
    assert not errors and not worker.is_alive()
    assert result == {"drained": True, "clean": True, "forced": False, "busy_sessions": []}


def test_successor_promotion_failure_is_logged_before_any_predecessor_rpc(
    tmp_path, monkeypatch,
):
    from agent_worktrees import status_monitor_cutover as cutover

    client = ControlClient("http://127.0.0.1:12345", config_dir=tmp_path, token="fixture")
    monkeypatch.setattr(
        cutover.routing, "read_active_endpoint",
        lambda *args, **kwargs: SimpleNamespace(base_url="http://127.0.0.1:12346"),
    )
    requests = []

    def request(self, action, payload=None):
        requests.append((self.port, action))
        raise TimeoutError("private fixture detail")

    monkeypatch.setattr(ControlClient, "_request", request)
    with pytest.raises(TimeoutError):
        client.drain(timeout=300, poll=1, force=False)
    assert requests == [(12346, "promote")]
    events = _events(tmp_path)
    assert events[-1]["detail"]["phase"] == "promote-successor"
    assert events[-1]["outcome"] == "fail"
    assert events[-1]["detail"]["rpc_timeout_s"] == 5.0
    assert events[-1]["detail"]["timeout_s"] == 300
    assert not any(event["detail"]["phase"] == "request-predecessor" for event in events)
    assert "private fixture detail" not in (tmp_path / "lifecycle.log").read_text()


def test_client_timeout_does_not_cancel_the_daemon_drain_callback(tmp_path, monkeypatch):
    busy = threading.Event()
    busy.set()
    entered = threading.Event()
    finished = threading.Event()

    def handle(action, payload):
        assert action == "drain"
        entered.set()
        try:
            return diagnostics.drain_with_diagnostics(
                state_lock=threading.RLock(), enter=lambda: None, cleanup=lambda: None,
                busy=lambda: ["hook"] if busy.is_set() else [],
                observe=lambda: {"hook": int(busy.is_set())},
                directory=tmp_path, port=server.port,
                timeout=float(payload["timeout"]), poll=float(payload["poll"]),
                force=bool(payload["force"]),
            )
        finally:
            finished.set()

    server = ControlServer(handle)
    server.start()
    try:
        client = ControlClient(
            f"http://127.0.0.1:{server.port}", config_dir=tmp_path, timeout=0.5,
        )
        request = client._request

        def short_request(action, payload=None, **kwargs):
            kwargs["read_timeout"] = client.timeout
            return request(action, payload, **kwargs)

        # Inject early caller loss; public drain normally allows its full budget.
        monkeypatch.setattr(client, "_request", short_request)
        with pytest.raises(TimeoutError):
            client.drain(timeout=2, poll=0.05, force=False)
        assert entered.is_set() and not finished.is_set()
        busy.clear()
        assert finished.wait(timeout=2)
    finally:
        busy.clear()
        server.close()
    events = _events(tmp_path)
    assert any(
        event["detail"]["role"] == "client"
        and event["detail"]["phase"] == "request-predecessor"
        and event["outcome"] == "fail"
        for event in events
    )
    assert any(
        event["detail"]["role"] == "daemon"
        and event["detail"]["phase"] == "complete"
        and event["outcome"] == "ok"
        for event in events
    )
