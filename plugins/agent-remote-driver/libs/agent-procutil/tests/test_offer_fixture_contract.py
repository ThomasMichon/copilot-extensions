"""Portable checks of the native fixture's measurement boundary, not Win32 proof."""

from __future__ import annotations

import ast
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import agent_procutil._daemon_instance_identity as identity
import agent_procutil._windows_parent_job as parent_job
import test_shell_caller_owned_native as subject


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *arguments):
        return self.callback(*arguments)


@pytest.fixture
def fixture_native(monkeypatch, tmp_path):
    state = SimpleNamespace(
        process=None, counts=[], fixture_handles=0, job=False,
        extra_handle=0, leak_on_subject=False, inherit_flags=0,
        job_closes=0, process_closed=False,
        invalid_handle_error=6, abort_retains_handle=False, exit_override=None, stdout_extra=b"",
    )

    def count(_process, output):
        process = state.process
        assert process is not None, "counter started before fixture allocation"
        assert process.stdin.closed, "input pipe state changed inside measurement"
        assert process.output.closed is False and process.errors.closed is False
        if not state.counts:
            assert process.gate.read_bytes() == b"0", "SDK ran before the baseline"
        value = 100 + state.fixture_handles + int(state.job) + state.extra_handle
        output._obj.value = value
        state.counts.append(value)
        return True

    def information(handle, flags):
        assert handle == 333
        flags._obj.value = state.inherit_flags
        return state.job

    def close(handle):
        assert handle == 333 and state.job
        state.job = False
        state.job_closes += 1
        return True

    api = SimpleNamespace(
        GetCurrentProcess=Function(lambda: -1),
        GetProcessHandleCount=Function(count),
        GetHandleInformation=Function(information),
        CloseHandle=Function(close),
    )

    class Input(io.BytesIO):
        def close(self):
            if not self.closed:
                state.decision = self.getvalue()
            super().close()

    class Process:
        def __init__(self, arguments, **options):
            assert options["stdin"] == subprocess.PIPE
            assert options["stdout"] != subprocess.PIPE and options["stderr"] != subprocess.PIPE
            assert arguments[-1] == str(os.getpid())
            assert arguments[5].endswith(subject.OFFER_BODY)
            gate = ast.parse(arguments[5].splitlines()[0]).body[0]
            self.gate = Path(ast.literal_eval(gate.value))
            self.mode = arguments[-2]
            self.stdin = Input()
            self.output, self.errors = options["stdout"], options["stderr"]
            self.returncode = None
            self._handle = SimpleNamespace(Close=self.close_process)
            state.fixture_handles = 9
            state.process = self

        def close_process(self):
            state.process_closed = True
            state.fixture_handles = 0

        def wait(self, timeout):
            assert timeout in (3, 5)
            assert self.gate.read_bytes() == b"1"
            assert state.counts == [109]
            nonce = "a" * 32
            expected = {
                "no-permission": b"",
                "wrong-permission": b"wrong\n",
                "abort": ("offer:" + nonce + "\nabort:" + nonce + "\n").encode(),
                "write-abort": ("offer:" + nonce + "\nabort:" + nonce + "\n").encode(),
                "write-eof": ("offer:" + nonce + "\n").encode(),
                "wrong-ack": ("offer:" + nonce + "\nwrong\n").encode(),
                "missing-ack": ("offer:" + nonce + "\n").encode(),
                "adopted": ("offer:" + nonce + "\n" + nonce + "\n").encode(),
            }
            assert state.decision == expected[self.mode]
            state.extra_handle = int(state.leak_on_subject)
            receipt = json.dumps({
                "version": 1, "scope": "windows-job", "nonce": nonce, "owner_pid": os.getpid(),
                "job": {"parent_pid": os.getpid(), "parent_birth": "windows:12",
                        "parent_handle": 333},
            }).encode() + b"\n"
            self.returncode = 1
            if self.mode in ("abort", "write-abort"):
                self.returncode = 0
            if self.mode in ("no-permission", "wrong-permission"):
                self.errors.write(b"RuntimeError: original caller did not acknowledge lifetime custody\n")
            elif self.mode.startswith("write-"):
                self.errors.write(b"receipt:" + receipt)
            else:
                self.output.write(receipt)
            if self.mode in ("write-eof", "wrong-ack", "missing-ack"):
                self.errors.write(b"Job adoption is unconfirmed\n")
            if self.mode not in ("no-permission", "wrong-permission", "abort", "write-abort"):
                state.job = True
            elif self.mode in ("abort", "write-abort") and state.abort_retains_handle:
                state.job = True
            if self.mode == "adopted":
                self.returncode = 7
                self.output.write(b"admitted\n\n__COPILOT_CALLER_EXIT_" + nonce.encode() + b"=7\n")
            if state.exit_override is not None:
                self.returncode = state.exit_override
            self.output.write(state.stdout_extra)
            self.output.flush()
            self.errors.flush()
            return self.returncode

        def poll(self):
            return self.returncode

        def communicate(self, *_arguments, **_options):
            pytest.fail("the handle fixture must not allocate communicate reader threads")

    monkeypatch.setattr(parent_job, "_api", lambda: api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state.invalid_handle_error, raising=False)
    monkeypatch.setattr(identity, "birth_identity", lambda _pid: "windows:12")
    monkeypatch.setattr(subject.subprocess, "Popen", Process)
    return state


@pytest.mark.parametrize("mode", subject.OFFER_MODES)
def test_fixture_holds_setup_handles_across_exact_job_balance(mode, fixture_native, tmp_path):
    subject.test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path)
    retained = mode not in ("no-permission", "wrong-permission", "abort", "write-abort")
    assert fixture_native.counts == [109, 109 + int(retained), 109]
    assert fixture_native.job_closes == int(retained)
    assert fixture_native.process_closed
    assert fixture_native.process.output.closed and fixture_native.process.errors.closed
    assert (tmp_path / "offer-fixture.gate").read_bytes() == b"1"


@pytest.mark.parametrize("mode", subject.OFFER_MODES)
def test_fixture_does_not_tolerate_an_extra_native_handle(mode, fixture_native, tmp_path):
    fixture_native.leak_on_subject = True
    with pytest.raises(AssertionError, match="unexpected native handle balance"):
        subject.test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path)
    retained = mode not in ("no-permission", "wrong-permission", "abort", "write-abort")
    assert fixture_native.job_closes == int(retained)
    assert fixture_native.process_closed


def test_fixture_refuses_an_inheritable_offered_handle(fixture_native, tmp_path):
    fixture_native.inherit_flags = 1
    with pytest.raises(AssertionError):
        subject.test_native_offer_failure_preserves_parent_handle_custody("missing-ack", tmp_path)
    assert fixture_native.job_closes == 1
    assert fixture_native.process_closed


@pytest.mark.parametrize("mode", [
    "no-permission", "wrong-permission", "write-eof", "wrong-ack", "missing-ack",
])
def test_zero_exit_never_qualifies_unacknowledged_custody(
    mode: str, fixture_native: SimpleNamespace, tmp_path: Path,
) -> None:
    fixture_native.exit_override = 0
    with pytest.raises(AssertionError, match="offer failure unexpectedly exited successfully"):
        subject.test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path)
    assert fixture_native.process_closed


@pytest.mark.parametrize("mode", ["abort", "write-abort"])
def test_aborted_offer_requires_its_exact_caller_handle_to_be_closed(
    mode: str, fixture_native: SimpleNamespace, tmp_path: Path,
) -> None:
    fixture_native.abort_retains_handle = True
    with pytest.raises(AssertionError, match="aborted offer left its caller handle valid"):
        subject.test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path)
    assert fixture_native.process_closed and fixture_native.job_closes == 0


@pytest.mark.parametrize("mode", ["abort", "write-abort"])
def test_aborted_offer_refuses_unknown_handle_query_failures(
    mode: str, fixture_native: SimpleNamespace, tmp_path: Path,
) -> None:
    fixture_native.invalid_handle_error = 5
    with pytest.raises(AssertionError, match="aborted offer handle closure is unconfirmed"):
        subject.test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path)
    assert fixture_native.process_closed and fixture_native.job_closes == 0


@pytest.mark.parametrize("mode", ["abort", "write-abort"])
@pytest.mark.parametrize("output", [b"admitted\n", b"__COPILOT_CALLER_EXIT_"])
def test_aborted_offer_cannot_publish_application_admission_or_completion(
    mode: str, output: bytes, fixture_native: SimpleNamespace, tmp_path: Path,
) -> None:
    fixture_native.stdout_extra = output
    with pytest.raises(AssertionError):
        subject.test_native_offer_failure_preserves_parent_handle_custody(mode, tmp_path)
    assert fixture_native.process_closed and fixture_native.job_closes == 0


@pytest.mark.skipif(sys.platform != "linux", reason="bounded portable gate process control")
def test_fixture_gate_keeps_the_subject_cold(tmp_path):
    gate_path = tmp_path / "start"
    done = tmp_path / "subject-started"
    body = (
        f"_offer_gate = {str(gate_path)!r}\n"
        + subject.OFFER_GATE
        + f"\nPath({str(done)!r}).write_text('started')\n"
    )
    with gate_path.open("w+b") as gate:
        gate.write(b"0")
        gate.flush()
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", "-B", "-c", body],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.1)
            assert not done.exists()
            gate.seek(0)
            gate.write(b"1")
            gate.flush()
            stdout, stderr = process.communicate(timeout=2)
            assert process.returncode == 0, (stdout, stderr)
            assert done.read_text() == "started"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(2)
            process.stdout.close()
            process.stderr.close()
