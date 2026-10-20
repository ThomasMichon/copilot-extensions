"""Native bounded-capture contracts; no application, service or credential effects."""

from __future__ import annotations

import inspect
import io
import json
import os
from pathlib import Path
from contextlib import nullcontext
import errno
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_procutil import owned_process as ownership
from agent_procutil import _caller_owned as protocol
from agent_procutil.owned_process import (
    OutputLimitExceeded,
    OwnedCleanupError,
    run_caller_owned,
    run_owned,
)

native_execution = pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="caller-owned native execution supports Windows and Linux",
)


def _run(code: str, *, timeout: float = 5, stdout: int = 4096, stderr: int = 4096, **kwargs):
    return run_caller_owned(
        [sys.executable, "-I", "-c", code], timeout=timeout,
        max_stdout_bytes=stdout, max_stderr_bytes=stderr, **kwargs,
    )


@pytest.mark.parametrize("capture", [True, False])
@native_execution
def test_separate_utf8_streams_input_and_real_returncode(capture):
    result = _run(
        "import sys; data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(data+b'\\r\\n'); "
        "sys.stderr.buffer.write('diagnostic\\u00e9'.encode('utf-8')); sys.exit(7)",
        input_text="value\u00e9", capture=capture,
    )
    assert result.returncode == 7
    assert result.stdout == ("value\u00e9\n" if capture else None)
    assert result.stderr == ("diagnostic\u00e9" if capture else None)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@native_execution
def test_raw_byte_limit_accepts_exact_boundary(stream):
    result = _run(
        f"import sys; sys.{stream}.buffer.write(('\\u00e9'*8).encode('utf-8'))",
        stdout=16, stderr=16,
    )
    assert result.returncode == 0
    assert getattr(result, stream) == "\u00e9" * 8


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@native_execution
def test_raw_byte_overflow_is_typed_prompt_and_nondisclosing(stream):
    started = time.monotonic()
    with pytest.raises(OutputLimitExceeded) as caught:
        _run(
            f"import sys,time; sys.{stream}.buffer.write(b'private-value-123'); "
            f"sys.{stream}.buffer.flush(); time.sleep(60)",
            timeout=5, stdout=16, stderr=16,
        )
    assert time.monotonic() - started < 4
    assert caught.value.stream == stream
    assert caught.value.limit == 16
    assert "private-value" not in str(caught.value)
    assert not hasattr(caught.value, "stdout")
    assert not hasattr(caught.value, "stderr")


@native_execution
def test_both_streams_are_drained_concurrently():
    result = _run(
        "import sys,threading; "
        "t=threading.Thread(target=lambda:sys.stderr.buffer.write(b'e'*131072)); "
        "t.start(); sys.stdout.buffer.write(b'o'*131072); t.join()",
        stdout=131072, stderr=131072,
    )
    assert result.returncode == 0
    assert result.stdout == "o" * 131072
    assert result.stderr == "e" * 131072


@native_execution
def test_blocked_input_obeys_absolute_timeout_and_original_command():
    code = "import time; time.sleep(60)"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(code, timeout=0.5, input_text="x" * 524288)
    assert time.monotonic() - started < 4
    assert caught.value.cmd == [sys.executable, "-I", "-c", code]
    assert caught.value.timeout == 0.5


@native_execution
def test_child_closing_stdin_early_preserves_result():
    result = _run(
        "import os,sys;os.close(0);sys.stdout.write('closed');sys.exit(7)",
        input_text="x" * 524288,
    )
    assert result.returncode == 7
    assert result.stdout == "closed"


@pytest.mark.parametrize("number", [errno.EPIPE, errno.EINVAL, errno.EIO])
@pytest.mark.parametrize("boundary", ["write", "close"])
def test_windows_input_errors_distinguish_early_closure_from_failure(number, boundary):
    class Input:
        def write(self, data):
            if boundary == "write":
                raise OSError(number, "synthetic stdin failure")
            return len(data)

        def close(self):
            if boundary == "close":
                raise OSError(number, "synthetic stdin failure")

    process = SimpleNamespace(
        stdin=Input(), stdout=io.BytesIO(b"closed"), stderr=io.BytesIO(),
        poll=lambda: 7, wait=lambda **kwargs: 7,
    )
    job = SimpleNamespace(terminate_and_wait=lambda timeout: None)

    def communicate():
        return ownership._communicate_windows_bounded(
            process, job, input_text="input", deadline=time.monotonic() + 1,
            argv=["fixture"], timeout=1, capture_limits=(32, 32),
        )

    if number == errno.EIO:
        with pytest.raises(OSError) as caught:
            communicate()
        assert caught.value.errno == errno.EIO
    else:
        assert communicate() == ("closed", "")


@native_execution
def test_output_overflow_settles_a_concurrently_blocked_input_writer():
    started = time.monotonic()
    with pytest.raises(OutputLimitExceeded) as caught:
        _run(
            "import sys,time;sys.stdout.buffer.write(b'x'*17);"
            "sys.stdout.buffer.flush();time.sleep(60)",
            timeout=5, stdout=16, input_text="x" * 524288,
        )
    assert time.monotonic() - started < 4
    assert caught.value.stream == "stdout"


@native_execution
def test_invalid_utf8_is_not_returned_as_success():
    with pytest.raises(UnicodeError):
        _run("import sys; sys.stdout.buffer.write(b'\\xff')")


@native_execution
@pytest.mark.parametrize("capture", [True, False])
def test_absolute_deadline_retires_target_after_output_closes(capture):
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(
            "import os,time;os.close(1);os.close(2);time.sleep(60)",
            timeout=0.3, capture=capture,
        )
    assert caught.value.timeout == 0.3
    assert time.monotonic() - started < 4


@pytest.mark.parametrize("platform", ["darwin", "freebsd14"])
def test_unsupported_posix_platform_refuses_before_spawn(monkeypatch, platform):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported platform reached native spawn")

    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(protocol, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(protocol, "_run_owned", forbidden)
    with pytest.raises(RuntimeError, match="requires Linux"):
        _run("pass")


def test_invalid_utf8_input_refuses_before_spawn(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid text reached native spawn")

    monkeypatch.setattr(ownership, "spawn_sync_in_kill_on_close_job", forbidden)
    with pytest.raises(UnicodeError):
        _run("pass", input_text="\ud800")


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "1"])
def test_invalid_timeout_refuses_before_spawn(monkeypatch, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached native spawn")

    monkeypatch.setattr(ownership, "spawn_sync_in_kill_on_close_job", forbidden)
    with pytest.raises(ValueError):
        _run("pass", timeout=value)


@pytest.mark.parametrize("field", ["stdout", "stderr"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, None])
def test_invalid_capture_limit_refuses_before_spawn(monkeypatch, field, value):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached native spawn")

    monkeypatch.setattr(ownership, "spawn_sync_in_kill_on_close_job", forbidden)
    with pytest.raises(ValueError):
        _run("pass", **{field: value})


@pytest.mark.parametrize("argv", [
    [], "not-an-argv-list", [123], ["command\0suffix"],
    ["command", "input\0suffix", "--dry-run"],
])
def test_invalid_argv_refuses_before_spawn(monkeypatch, argv):
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid request reached native spawn")

    monkeypatch.setattr(protocol, "_run_owned", forbidden)
    with pytest.raises(ValueError):
        run_caller_owned(argv, timeout=1, max_stdout_bytes=10, max_stderr_bytes=10)


def test_existing_run_owned_signature_remains_unchanged():
    assert list(inspect.signature(run_owned).parameters) == [
        "argv", "timeout", "input_text", "capture", "inherit_process_group",
    ]


@native_execution
def test_existing_run_owned_unbounded_default_remains_unchanged():
    result = run_owned(
        [sys.executable, "-I", "-c", "import sys;sys.stdout.write('x'*131072)"],
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == "x" * 131072


@native_execution
def test_missing_program_does_not_fabricate_a_completed_result(tmp_path: Path):
    with pytest.raises(OSError):
        run_caller_owned(
            [str(tmp_path / "missing-program")], timeout=5,
            max_stdout_bytes=4096, max_stderr_bytes=4096,
        )


def test_oversized_request_refuses_before_spawn(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("oversized request reached native spawn")

    monkeypatch.setattr(ownership, "spawn_sync_in_kill_on_close_job", forbidden)
    with pytest.raises(ValueError, match="frame limit"):
        _run("pass", input_text="x" * protocol._MAX_REQUEST)


@pytest.mark.parametrize("reply,returncode", [
    (None, -9),
    ({"version": True, "kind": "timeout"}, -9),
    ({"version": 1, "kind": "completed", "returncode": 0, "stdout": "", "stderr": ""}, 0),
    ({"version": 1, "kind": "completed", "returncode": True, "stdout": "", "stderr": ""}, -9),
    ({"version": 1, "kind": "completed", "returncode": 0, "stdout": None, "stderr": ""}, -9),
    ({"version": 1, "kind": "output-limit", "stream": "other", "limit": 10}, -9),
    ({"version": 1, "kind": "output-limit", "stream": "stdout", "limit": True}, -9),
    ({"version": 1, "kind": "output-limit", "stream": "stdout", "limit": 11}, -9),
    ({"version": 1, "kind": "timeout", "unexpected": "data"}, -9),
])
def test_invalid_control_completion_is_not_accepted(monkeypatch, reply, returncode):
    monkeypatch.setattr(protocol, "signal", SimpleNamespace(SIGKILL=9))
    request = {"argv": ["command"], "timeout": 1, "capture": True, "limits": [10, 10]}
    with pytest.raises(RuntimeError):
        protocol._decode(reply, request, returncode)


def test_windows_cleanup_failure_is_explicit_and_not_retried():
    class Process:
        stdin = None
        stdout = io.BytesIO(b"partial")
        stderr = io.BytesIO()

        def poll(self):
            return 0

        def wait(self, timeout):
            return 0

    class Job:
        attempts = 0

        def terminate_and_wait(self, timeout):
            self.attempts += 1
            raise OSError("synthetic Job cleanup failure")

    job = Job()
    with pytest.raises(OwnedCleanupError, match="cleanup did not complete") as caught:
        ownership._communicate_windows_bounded(
            Process(), job, input_text=None, deadline=time.monotonic() + 1,
            argv=["fixture"], timeout=1, capture_limits=(32, 32),
        )
    assert job.attempts == 1
    assert "partial" not in str(caught.value)


@pytest.mark.parametrize("exits", [True, False])
def test_group_confirmation_waits_for_exit_without_releasing_root(monkeypatch, exits):
    process = SimpleNamespace(pid=10, returncode=None)
    observations = []
    closed = []
    clock = [0.0]
    checks = [0]

    def ready(*args):
        checks[0] += 1
        return ([7] if exits and checks[0] > 1 else [], [], [])

    def exited(root):
        observations.append(root)
        return object()

    monkeypatch.setattr(protocol, "_observe_unreaped", exited)
    monkeypatch.setattr(protocol, "_pidfd_pid", lambda fd: 20)
    monkeypatch.setattr(protocol, "_pidfd_exited", lambda fd: bool(ready()[0]))
    monkeypatch.setattr(protocol, "os", SimpleNamespace(
        scandir=lambda path: nullcontext([SimpleNamespace(name="20")]),
        getpgid=lambda pid: 10, pidfd_open=lambda pid: 7, close=closed.append,
    ))
    monkeypatch.setattr(protocol, "time", SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda delay: clock.__setitem__(0, clock[0] + (0.01 if exits else 1.1)),
    ))
    if exits:
        protocol._confirm_group(process)
        assert checks[0] == 3
    else:
        with pytest.raises(OwnedCleanupError) as caught:
            protocol._confirm_group(process)
        assert caught.value.process is process
        assert isinstance(caught.value.__cause__, TimeoutError)
    assert process.returncode is None
    assert observations and all(root is process for root in observations)
    assert len(closed) == checks[0]


def test_live_root_cannot_confirm_an_otherwise_empty_group(monkeypatch):
    process = SimpleNamespace(pid=10, returncode=None)
    clock = [0.0]
    monkeypatch.setattr(protocol, "_observe_unreaped", lambda root: None)
    monkeypatch.setattr(protocol, "os", SimpleNamespace(
        scandir=lambda path: nullcontext([]),
    ))
    monkeypatch.setattr(protocol, "time", SimpleNamespace(
        monotonic=lambda: clock[0],
        sleep=lambda delay: clock.__setitem__(0, clock[0] + 1.1),
    ))
    with pytest.raises(OwnedCleanupError) as caught:
        protocol._confirm_group(process)
    assert caught.value.process is process
    assert process.returncode is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native high-numbered Linux pidfd")
def test_pidfd_exit_observation_accepts_high_descriptors():
    import fcntl
    import resource

    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1024:
        pytest.skip("native descriptor limit cannot allocate a high-numbered pidfd")
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", "import sys;sys.stdin.read(1)"],
        stdin=subprocess.PIPE,
    )
    original = high = None
    try:
        original = os.pidfd_open(process.pid)
        high = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 1024)
        assert high >= 1024
        assert not protocol._pidfd_exited(high)
        process.stdin.close()
        deadline = time.monotonic() + 3
        while ownership._observe_unreaped(process) is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert protocol._pidfd_exited(high)
        assert process.returncode is None
    finally:
        if high is not None:
            os.close(high)
        if original is not None:
            os.close(original)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


@pytest.mark.parametrize("boundary", ["retire", "confirm", "wait"])
def test_all_caller_owned_cleanup_failures_retain_process(monkeypatch, boundary):
    process = SimpleNamespace(stdin=None, stdout=None, stderr=None, returncode=None)
    failure = (
        subprocess.TimeoutExpired(["guard"], 2) if boundary == "wait"
        else OSError("synthetic cleanup failure")
    )

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(protocol, "_require_posix_support", lambda: None)
    monkeypatch.setattr(protocol, "os", SimpleNamespace(
        name="posix", pipe=os.pipe, set_inheritable=os.set_inheritable, close=os.close,
    ))
    monkeypatch.setattr(ownership, "os", SimpleNamespace(
        name="posix", waitid=object(), P_PID=1, WEXITED=1, WNOHANG=1, WNOWAIT=1,
    ))
    monkeypatch.setattr(ownership, "signal", SimpleNamespace(
        SIGCHLD=1, SIG_DFL=0, getsignal=lambda value: 0,
    ))
    process.wait = fail if boundary == "wait" else lambda **kwargs: None
    monkeypatch.setattr(ownership, "spawn_sync_in_kill_on_close_job", lambda *a, **k: (process, None))
    monkeypatch.setattr(ownership, "_communicate_unreaped", lambda *a, **k: ("", ""))
    monkeypatch.setattr(
        ownership, "_retire_posix_group", fail if boundary == "retire" else lambda *a, **k: None,
    )
    monkeypatch.setattr(
        protocol, "_confirm_group", fail if boundary == "confirm" else lambda *a: None,
    )
    with pytest.raises(OwnedCleanupError) as caught:
        _run("pass")
    assert caught.value.process is process
    assert caught.value.__cause__ is failure
    assert process.returncode is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux group ownership")
def test_guard_owns_the_group_before_target_execution():
    result = _run(
        "import json,os; "
        "print(json.dumps({'parent':os.getppid(),'group':os.getpgrp(),'session':os.getsid(0)}))",
    )
    observation = json.loads(result.stdout)
    assert observation["parent"] == observation["group"] == observation["session"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux setup refusal")
def test_closed_lifetime_pipe_refuses_before_target_launch(tmp_path):
    reader, writer = os.pipe()
    os.close(writer)
    marker = tmp_path / "must-not-launch"
    request = {
        "argv": [sys.executable, "-I", "-c", f"open({str(marker)!r},'w').write('bad')"],
        "timeout": 2, "input_text": None, "capture": True, "limits": [4096, 4096],
    }
    try:
        result = subprocess.run(
            [
                sys.executable, "-I", "-S", "-c", protocol._BOOTSTRAP,
                str(Path(protocol.__file__).resolve().parents[1]), str(reader),
            ],
            input=json.dumps(request) + "\n", text=True, capture_output=True,
            pass_fds=(reader,), start_new_session=True, timeout=5,
        )
    finally:
        os.close(reader)
    assert result.returncode == -9
    assert not marker.exists()
