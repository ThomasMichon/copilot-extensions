"""Native bounded-capture contracts; no application, service or credential effects."""

from __future__ import annotations

import inspect
import io
import json
import os
from pathlib import Path
from contextlib import nullcontext
import errno
import signal
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
    os.name != "nt" and not sys.platform.startswith("linux") and sys.platform != "darwin",
    reason="caller-owned native execution supports Windows, Linux and macOS",
)


def _run(code: str, *, timeout: float = 5, stdout: int = 4096, stderr: int = 4096, **kwargs):
    return run_caller_owned(
        [sys.executable, "-I", "-c", code], timeout=timeout,
        max_stdout_bytes=stdout, max_stderr_bytes=stderr, **kwargs,
    )


@pytest.mark.parametrize("contained", [False, True])
def test_installation_exception_preserves_contained_tests_and_strict_tools(monkeypatch, contained):
    observed = []
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1" if contained else "0")

    def execute(argv, **kwargs):
        observed.append(kwargs)
        return subprocess.CompletedProcess(argv, 7, "installer output", "")

    monkeypatch.setattr(protocol, "run", execute)
    options = dict(timeout=5, max_stdout_bytes=4096, max_stderr_bytes=4096)
    result = ownership.run_installation_owned(["installer"], **options)
    assert result.returncode == 7 and result.stdout == "installer output"
    assert observed[-1]["allow_daemon_breakaway"] is not contained
    run_caller_owned(["ordinary-tool"], **options)
    assert observed[-1].get("allow_daemon_breakaway", False) is False


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


@native_execution
def test_caller_owned_uses_explicit_child_context_without_mutating_caller(tmp_path):
    cwd = Path.cwd()
    environment = {**os.environ, "CALLER_OWNED_CONTEXT_FIXTURE": "selected"}
    before = dict(environment)
    result = _run(
        "import json,os; print(json.dumps([os.getcwd(),os.environ['CALLER_OWNED_CONTEXT_FIXTURE']]))",
        cwd=tmp_path, env=environment,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == [str(tmp_path), "selected"]
    assert Path.cwd() == cwd
    assert environment == before
    assert os.environ.get("CALLER_OWNED_CONTEXT_FIXTURE") is None


def test_windows_explicit_environment_is_unicode_sorted_and_double_terminated():
    from agent_procutil._windows_caller_owned import _environment_block

    assert _environment_block(None) is None
    assert _environment_block({})[:].startswith("\0\0")
    assert _environment_block({"b": "\u00e9", "A": "value"})[:].startswith(
        "A=value\0b=\u00e9\0\0",
    )


@pytest.mark.parametrize("environment", [
    {"": "value"}, {"BAD=KEY": "value"}, {"BAD\0KEY": "value"},
    {"KEY": "bad\0value"}, {"KEY": 3}, {3: "value"},
])
def test_windows_explicit_environment_refuses_invalid_entries(environment):
    from agent_procutil._windows_caller_owned import _environment_block

    with pytest.raises((TypeError, ValueError)):
        _environment_block(environment)


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


@pytest.mark.skipif(os.name != "nt", reason="Windows bounded pipe timeout evidence")
def test_windows_timeout_retains_bounded_raw_partial_streams_after_retirement():
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run(
            "import os,time;os.write(1,b'partial\\xff');"
            "os.write(2,b'phase\\r\\n');time.sleep(60)",
            timeout=5,
        )
    assert caught.value.stdout == b"partial\xff"
    assert caught.value.stderr == b"phase\r\n"
    assert "partial\xff" not in str(caught.value)
    assert "phase\r\n" not in str(caught.value)


def test_windows_timeout_after_stream_eof_retains_raw_bytes():
    def expired():
        raise subprocess.TimeoutExpired(["fixture"], 1)

    process = SimpleNamespace(
        stdin=None, stdout=io.BytesIO(b"partial\xff"), stderr=io.BytesIO(b"phase\r\n"),
        poll=expired, wait=lambda **kwargs: 0, retire_job=lambda job, timeout: None,
    )
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        ownership._communicate_windows_bounded(
            process, object(), input_text=None, deadline=time.monotonic() + 5,
            argv=["fixture"], timeout=1, capture_limits=(32, 32),
        )
    assert caught.value.output == b"partial\xff"
    assert caught.value.stderr == b"phase\r\n"
    assert "partial" not in str(caught.value)
    assert "phase" not in str(caught.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell nested live stdin contract")
def test_nested_powershell_json_input_reaches_eof_while_outer_stdin_stays_open(tmp_path):
    from agent_procutil import spawn_sync_in_kill_on_close_job

    powershell = Path(os.environ["SystemRoot"]) / r"System32\WindowsPowerShell\v1.0\powershell.exe"
    source = str(Path(ownership.__file__).resolve().parents[1])
    request = json.dumps({"config": {"value": "x" * 1000}})
    script = tmp_path / "reader.ps1"
    script.write_text(
        "[Console]::Error.WriteLine('input');"
        "$v=[Console]::In.ReadToEnd()|ConvertFrom-Json;"
        "[Console]::Out.Write($v.config.value.Length)",
        encoding="utf-8",
    )
    code = (
        f"import sys;sys.path.insert(0,{source!r});"
        "from agent_procutil.owned_process import run_caller_owned;"
        "assert sys.stdin.readline()=='launch\\n';"
        f"r=run_caller_owned([{str(powershell)!r},'-NoProfile','-NonInteractive','-File',"
        f"{str(script)!r}],"
        f"input_text={request!r},timeout=5,max_stdout_bytes=4096,max_stderr_bytes=4096);"
        "assert r.returncode==0,r.stderr;print(r.stdout,flush=True)"
    )
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "COMSPEC"}
    }
    process, job = spawn_sync_in_kill_on_close_job(
        [sys.executable, "-I", "-c", code], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        require_job=True, allow_breakaway=False, text=True, encoding="utf-8",
        creationflags=0x08000000,
    )
    try:
        process.stdin.write("launch\n")
        process.stdin.flush()
        process.wait(timeout=10)
        assert process.returncode == 0, process.stderr.read()
        assert process.stdout.read().strip() == "1000"
    finally:
        job.terminate_and_wait(2)
        process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


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
        retire_job=lambda job, timeout: job.terminate_and_wait(timeout),
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
@pytest.mark.parametrize("failure", ["none", "timeout", "decode", "limit"])
def test_native_settlement_notification_precedes_result_or_failure(failure):
    observed = []
    code, timeout, limit = "print('ready')", 5, 4096
    expected = nullcontext()
    if failure == "timeout":
        code, timeout = "import time; time.sleep(60)", .3
        expected = pytest.raises(subprocess.TimeoutExpired)
    elif failure == "decode":
        code = "import sys; sys.stdout.buffer.write(b'\\xff')"
        expected = pytest.raises(UnicodeError)
    elif failure == "limit":
        code, limit = "print('too much')", 1
        expected = pytest.raises(OutputLimitExceeded)
    with expected:
        _run(code, timeout=timeout, stdout=limit, on_settled=lambda: observed.append(True))
    assert observed == [True]


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


@pytest.mark.parametrize("platform", ["freebsd14"])
def test_unsupported_posix_platform_refuses_before_spawn(monkeypatch, platform):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported platform reached native spawn")

    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(protocol, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(protocol, "_run_owned", forbidden)
    with pytest.raises(RuntimeError, match="requires Linux"):
        _run("pass")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux pidfd observation")
def test_missing_python_pidfd_wrapper_retains_native_handle_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil._linux_pidfd import open_pidfd

    monkeypatch.delattr(os, "pidfd_open", raising=False)
    descriptor = open_pidfd(os.getpid())
    try:
        assert protocol._pidfd_pid(descriptor) == os.getpid()
        assert not os.get_inheritable(descriptor)
    finally:
        os.close(descriptor)
    ownership.require_caller_owned_support()
    result = _run("import sys; print('native-handle'); sys.exit(7)")
    assert result.returncode == 7
    assert result.stdout == "native-handle\n"


def test_missing_native_pidfd_api_is_an_explicit_pre_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _linux_pidfd

    monkeypatch.delattr(_linux_pidfd.os, "pidfd_open", raising=False)
    monkeypatch.setattr(_linux_pidfd.ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace())
    with pytest.raises(RuntimeError, match="libc pidfd acquisition is unavailable"):
        _linux_pidfd.open_pidfd(1)


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

        def retire_job(self, job, timeout):
            job.terminate_and_wait(timeout)

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
    monkeypatch.setattr(protocol, "sys", SimpleNamespace(platform="linux"))
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
    monkeypatch.setattr(protocol, "open_pidfd", lambda pid: 7)
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
    monkeypatch.setattr(protocol, "sys", SimpleNamespace(
        platform="linux", executable=sys.executable,
    ))
    monkeypatch.setattr(ownership, "sys", SimpleNamespace(platform="linux"))
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
    observed = []
    with pytest.raises(OwnedCleanupError) as caught:
        _run("pass", on_settled=lambda: observed.append(True))
    assert caught.value.process is process
    assert caught.value.__cause__ is failure
    assert process.returncode is None
    assert observed == []


def test_prelaunch_refusal_notifies_without_attempting_native_spawn(monkeypatch):
    observed = []

    def forbidden(*args, **kwargs):
        pytest.fail("invalid preparation attempted process creation")

    monkeypatch.setattr(protocol, "_run_owned", forbidden)
    with pytest.raises(ValueError):
        _run("pass", timeout=0, on_settled=lambda: observed.append(True))
    assert observed == [True]


@pytest.mark.skipif(os.name == "nt", reason="POSIX lifetime pipe preparation")
@pytest.mark.parametrize("boundary", ["pipe", "registration", "inheritable"])
def test_lifetime_pipe_refusal_notifies_outside_guard_and_closes_descriptors(monkeypatch, boundary):
    observed, descriptors = [], []
    pipe = os.pipe

    def allocate():
        if boundary == "pipe":
            raise OSError(errno.EMFILE, "synthetic descriptor exhaustion")
        pair = pipe()
        descriptors.extend(pair)
        return pair

    def refuse(*args):
        raise OSError(errno.EIO, "synthetic inheritance refusal")

    def notify():
        assert protocol._lifetime_lock.acquire(blocking=False)
        protocol._lifetime_lock.release()
        assert not set(descriptors) & protocol._lifetime_writers
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)
        observed.append(True)

    def forbidden(*args, **kwargs):
        pytest.fail("failed pipe preparation reached native launch")

    monkeypatch.setattr(protocol.os, "pipe", allocate)
    monkeypatch.setattr(protocol, "_require_posix_support", lambda: None)
    if boundary == "registration":
        class RefusingWriters(set):
            def add(self, value):
                raise MemoryError("synthetic registration refusal")

        monkeypatch.setattr(protocol, "_lifetime_writers", RefusingWriters())
    if boundary == "inheritable":
        monkeypatch.setattr(protocol.os, "set_inheritable", refuse)
    monkeypatch.setattr(protocol, "_run_owned", forbidden)
    with pytest.raises((OSError, MemoryError)):
        _run("pass", on_settled=notify)
    assert observed == [True]
    assert bool(descriptors) is (boundary != "pipe")


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


def _eperm(*args):
    raise PermissionError(errno.EPERM, "Operation not permitted")


class _EpermGroupSignals:
    """Real ``os`` except that signalling any process group reports EPERM."""

    killpg = staticmethod(_eperm)

    def __getattr__(self, name):
        return getattr(os, name)


@pytest.mark.parametrize(
    "exited,zombie_only,inherit,settles",
    [
        (True, True, False, True),
        (True, False, False, False),
        (False, True, False, False),
        (True, True, True, False),
    ],
)
def test_group_retirement_accepts_eperm_only_for_a_confirmed_zombie_only_group(
    monkeypatch, exited, zombie_only, inherit, settles,
):
    process = SimpleNamespace(pid=123, returncode=None)
    monkeypatch.setattr(ownership, "os", SimpleNamespace(kill=_eperm, killpg=_eperm))
    monkeypatch.setattr(ownership, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(ownership, "_observe_unreaped", lambda child: object() if exited else None)
    monkeypatch.setattr(ownership, "_zombie_only_group", lambda child: zombie_only)
    if settles:
        ownership._retire_posix_group(process, inherit_process_group=inherit)
    else:
        with pytest.raises(PermissionError):
            ownership._retire_posix_group(process, inherit_process_group=inherit)


@pytest.mark.skipif(os.name == "nt", reason="native POSIX process-group retirement")
@pytest.mark.parametrize("zombie_only", [True, False])
def test_run_owned_reports_success_only_after_eperm_group_settlement(monkeypatch, zombie_only):
    monkeypatch.setattr(ownership, "os", _EpermGroupSignals())
    monkeypatch.setattr(ownership, "_zombie_only_group", lambda child: zombie_only)
    argv = [sys.executable, "-I", "-c", "pass"]
    if zombie_only:
        assert ownership.run_owned(argv, timeout=10).returncode == 0
    else:
        with pytest.raises(PermissionError):
            ownership.run_owned(argv, timeout=10)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin process-group census")
@pytest.mark.parametrize("live_member", [False, True])
def test_darwin_census_refuses_eperm_while_a_group_member_lives(monkeypatch, tmp_path, live_member):
    monkeypatch.setattr(ownership, "os", _EpermGroupSignals())
    marker = tmp_path / "member.pid"
    code = (
        "import pathlib,subprocess,sys;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))"
        if live_member else "pass"
    )
    argv = [sys.executable, "-I", "-c", code]
    if not live_member:
        assert ownership.run_owned(argv, timeout=10).returncode == 0
        return
    try:
        with pytest.raises(PermissionError):
            ownership.run_owned(argv, timeout=10)
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name == "nt", reason="native POSIX process-group retirement")
def test_group_retirement_settles_a_zombie_only_group():
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", "pass"], stdin=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while ownership._observe_unreaped(process) is None:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    ownership._retire_posix_group(process)
    assert process.wait(timeout=2) == 0
