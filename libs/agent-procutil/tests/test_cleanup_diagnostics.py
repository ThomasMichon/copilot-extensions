"""Recorded diagnostics exercise substituted owners, never native cleanup."""

from __future__ import annotations

import errno
import io
import json
import subprocess
import threading
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from agent_procutil import owned_process as owned
from agent_procutil.cleanup_diagnostics import CleanupDiagnostics
from test_windows_observation_spawn import launch as launch


def test_legacy_error_retains_custody_and_snapshot_does_not_follow_retry() -> None:
    process: Any = object()
    job, resource = object(), object()
    events: list[str] = []
    error = owned.OwnedCleanupError(
        process, job, cleanup_resources=(("output", resource),),
        cleanup_retry=lambda deadline: events.append("retry"),
        on_settled=lambda: events.append("settled"),
    )
    before = error.diagnostics.public_fields()
    assert str(error) == "native child cleanup did not complete"
    assert error.process is process and error.job is job
    assert error.cleanup_resources == (("output", resource),)
    assert before == {
        "schema": 1, "phase": "unknown", "native_error_kind": "unknown",
        "native_error_code": None, "process_settlement": "unknown",
        "job_settlement": "unknown", "output_settlement": "unknown",
        "process_retained": True, "job_retained": True, "resources_retained": True,
        "cleanup_complete": False,
    }
    assert events == []
    error.retry_cleanup(deadline=time.monotonic() + 10)
    error.retry_cleanup(deadline=time.monotonic() + 10)
    assert events == ["retry", "settled"]
    assert error.diagnostics.public_fields() == before
    before["phase"] = "changed"
    assert error.diagnostics.phase == "unknown"
    with pytest.raises(FrozenInstanceError):
        setattr(error.diagnostics, "phase", "changed")


@pytest.mark.parametrize("code", [None, "5", True, 2**40])
def test_public_fields_drop_non_codes_and_arbitrary_text(code: Any) -> None:
    private: Any = "https://user:password@example.com/private?token=fake-secret"
    snapshot = CleanupDiagnostics(
        phase=private, native_error_kind=private, native_error_code=code,
        process_settlement=private, job_settlement=private, output_settlement=private,
        process_retained=private, job_retained=private, resources_retained=private,
        cleanup_complete=private,
    )
    text = json.dumps(snapshot.public_fields())
    assert private not in text and private not in repr(snapshot)
    assert snapshot.native_error_code is None
    assert snapshot.process_retained is None
    assert snapshot.phase == snapshot.process_settlement == "unknown"


def test_native_code_is_recorded_without_rendering_or_traversing_exceptions() -> None:
    class SensitiveError(OSError):
        def __str__(self) -> str:
            raise AssertionError("must not render an exception")

        def __repr__(self) -> str:
            raise AssertionError("must not render an exception")

    setattr(SensitiveError, "errno", property(
        lambda self: pytest.fail("diagnostics must use the recorded native member"),
    ))
    cause = SensitiveError(errno.EACCES, "password=fake-secret", r"C:\private\input")
    cause.__cause__ = RuntimeError("token=fake-secret")
    error = owned.OwnedCleanupError(None, cause=cause, phase="job-retire")
    assert error.diagnostics.native_error_kind == "errno"
    assert error.diagnostics.native_error_code == errno.EACCES
    assert "private" not in json.dumps(error.diagnostics.public_fields())
    assert error.__cause__ is None
    unknown = owned.OwnedCleanupError(None, cause=cause.__cause__)
    assert unknown.diagnostics.native_error_code is None
    assert unknown.diagnostics.process_retained is False


class _Process:
    pid = 123
    returncode: int | None = None
    stdin: Any = None
    stdout: Any = None
    stderr: Any = None

    def __init__(self, fail_at: str, cause: BaseException) -> None:
        self.fail_at = fail_at
        self.cause = cause
        self.calls: list[str] = []

    def retire_job(self, job: object, timeout: float) -> None:
        self.calls.append("retire")
        if self.fail_at == "job-retire":
            raise self.cause

    def wait(self, timeout: float) -> int:
        self.calls.append("wait")
        if self.fail_at == "process-wait":
            raise self.cause
        self.returncode = 0
        return 0

    def poll(self) -> int:
        return 0

    def close(self) -> None:
        self.calls.append("close")
        if self.fail_at == "process-handle-close":
            raise self.cause


@pytest.mark.parametrize("phase", ["job-retire", "process-wait"])
def test_windows_bounded_failure_keeps_first_cause_and_recorded_settlement(phase: str) -> None:
    cause = OSError(errno.EIO, "token=fake-secret")
    process = _Process(phase, cause)
    job = SimpleNamespace(_native_retirement=SimpleNamespace(confirmed=phase == "job-retire"))
    with pytest.raises(owned.OwnedCleanupError) as caught:
        owned._communicate_windows_bounded(
            process, job, input_text=None, deadline=time.monotonic() + 10,
            argv=["private-argument"], timeout=10, capture_limits=(100, 100),
        )
    error = caught.value
    assert error.__cause__ is cause
    assert error.process is process and error.job is job
    assert process.calls == ["retire", "wait"]
    assert error.diagnostics.phase == phase
    assert error.diagnostics.native_error_code == errno.EIO
    assert error.diagnostics.job_settlement == "confirmed"
    assert error.diagnostics.process_settlement == (
        "confirmed" if phase == "job-retire" else "unconfirmed"
    )
    assert error.diagnostics.output_settlement == "confirmed"


@pytest.mark.parametrize("phase", ["pipe-completion", "pipe-join"])
def test_windows_pipe_failure_retains_workers_without_probing_for_diagnostics(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    class Event:
        def __init__(self) -> None:
            self.value = False

        def set(self) -> None:
            self.value = True

        def is_set(self) -> bool:
            return self.value

        def wait(self, timeout: float) -> bool:
            return self.value

    class Worker:
        def __init__(self, *, target: Any, args: tuple[Any, ...], daemon: bool) -> None:
            self.args = args
            self.checks = 0

        def start(self) -> None:
            self.args[1].set()
            if phase == "pipe-join":
                self.args[2].set()

        def join(self, timeout: float) -> None:
            pass

        def is_alive(self) -> bool:
            self.checks += 1
            return self.checks > 1

    monkeypatch.setattr(owned, "threading", SimpleNamespace(
        Event=Event, Thread=Worker, Lock=threading.Lock,
    ))
    process = _Process("", RuntimeError())
    process.stdout = io.BytesIO(b"token=fake-secret")
    job = object()
    with pytest.raises(owned.OwnedCleanupError) as caught:
        owned._communicate_windows_bounded(
            process, job, input_text=None, deadline=time.monotonic() + 10,
            argv=["private-argument"], timeout=10, capture_limits=(100, 100),
        )
    error = caught.value
    assert error.diagnostics.phase == phase
    assert error.diagnostics.output_settlement == "unconfirmed"
    assert error.diagnostics.process_settlement == error.diagnostics.job_settlement == "confirmed"
    assert error.diagnostics.native_error_code is None
    assert [kind for kind, _ in error.cleanup_resources] == ["pipe-worker", "pipe-worker-start"]
    worker = error.cleanup_resources[0][1][0]
    assert worker.checks == (1 if phase == "pipe-completion" else 2)
    assert process.calls == ["retire", "wait"]
    assert "fake-secret" not in json.dumps(error.diagnostics.public_fields())


@pytest.mark.parametrize("phase", ["job-retire", "process-wait", "stream-close", "process-handle-close"])
def test_windows_outer_cleanup_phase_preserves_chain_and_resources(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    cause = OSError(errno.EIO, "password=fake-secret")
    process = _Process(phase, cause)
    job = SimpleNamespace(handle=1)

    class Stream:
        def close(self) -> None:
            raise cause

    if phase == "stream-close":
        process.stdout = Stream()
    from agent_procutil import _windows_caller_owned as native

    monkeypatch.setattr(native, "spawn", lambda *args, **kwargs: (process, job))
    monkeypatch.setattr(owned, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(owned, "_communicate_windows_bounded", lambda *args, **kwargs: ("", ""))
    with pytest.raises(owned.OwnedCleanupError) as caught:
        owned._run_owned(["fixture"], timeout=10, capture_limits=(100, 100))
    assert caught.value.__cause__ is cause
    assert caught.value.process is process and caught.value.job is job
    assert caught.value.diagnostics.phase == phase
    assert caught.value.diagnostics.native_error_code == errno.EIO
    assert "close" not in process.calls or phase == "process-handle-close"


@pytest.mark.parametrize("phase", ["job-retire", "process-wait", "stream-close", "process-handle-close"])
def test_suspended_spawn_cleanup_records_its_own_cause_without_changing_original_chain(
    launch: Any, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    from agent_procutil import _windows_caller_owned as native

    cause = OSError(errno.EIO, "password=fake-secret")
    operation = KeyboardInterrupt("private-argument")

    def fail(*args: Any) -> Any:
        raise cause

    with monkeypatch.context() as patch:
        def resume(handle: object) -> None:
            if phase == "stream-close":
                patch.setattr(launch.streams[0], "close", fail)
            raise operation

        patch.setattr(launch.kernel, "ResumeThread", resume)
        if phase == "job-retire":
            launch.kernel.cancel_cleanup = cause
        elif phase == "process-wait":
            patch.setattr(native._Process, "wait", fail)
        elif phase == "process-handle-close":
            patch.setattr(native._Process, "close", fail)
        with pytest.raises(owned.OwnedCleanupError) as caught:
            native.spawn(
                ["fixture"], cleanup_deadline=110, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        error = caught.value
        assert error.__cause__ is error.operation_error is operation
        assert error.cleanup_error is cause
        assert error.process is not None and error.job is launch.jobs[0]
        assert error.diagnostics.phase == phase
        assert error.diagnostics.native_error_code == errno.EIO
        assert error.diagnostics.job_settlement == (
            "unknown" if phase == "job-retire" else "confirmed"
        )
        assert error.diagnostics.process_settlement == (
            "confirmed" if phase in {"stream-close", "process-handle-close"} else "unknown"
        )
        assert error.diagnostics.output_settlement == (
            "confirmed" if phase == "process-handle-close" else "unknown"
        )
        assert not any(event == "resume" for event in launch.calls)
        assert "fake-secret" not in json.dumps(error.diagnostics.public_fields())


def test_precreation_job_close_failure_records_numeric_cause_and_no_process(
    launch: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _windows_caller_owned as native

    launch.kernel.deny_attribute = True
    monkeypatch.setattr(launch.kernel, "CloseHandle", lambda handle: False)
    with pytest.raises(owned.OwnedCleanupError) as caught:
        native.spawn(
            ["fixture"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    error = caught.value
    assert error.process is None and error.job is launch.jobs[0]
    assert error.diagnostics.phase == "spawn-job-close"
    assert error.diagnostics.native_error_code == 5
    assert error.diagnostics.process_retained is False
    assert error.diagnostics.job_retained is True
    assert error.diagnostics.job_settlement == "unknown"
