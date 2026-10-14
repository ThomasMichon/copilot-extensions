"""Retain exact native exit evidence before graceful lifecycle control."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from zdd import diagnostics, process_exit


class _NativeCall:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


def _windows_kernel(monkeypatch, *, open_ok=True, query_ok=True, token=456, exited=False):
    import ctypes
    from types import SimpleNamespace

    calls = []

    def query(handle, creation, *_):
        calls.append(("query", handle))
        creation._obj.dwLowDateTime = token
        return query_ok

    def wait(handle, milliseconds):
        calls.append(("wait", handle, milliseconds))
        return 0 if exited else 0x102

    kernel = SimpleNamespace(
        OpenProcess=_NativeCall(
            lambda rights, inherit, pid: calls.append(("open", rights, inherit, pid))
            or (99333 if open_ok else None),
        ),
        GetProcessTimes=_NativeCall(query),
        WaitForSingleObject=_NativeCall(wait),
        CloseHandle=_NativeCall(lambda handle: calls.append(("close", handle)) or True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(f"native error {code}"), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    return calls


def test_windows_capture_uses_same_query_and_wait_handle_without_signal_rights(monkeypatch):
    calls = _windows_kernel(monkeypatch)
    with process_exit._open_windows(123, "456") as reference:
        assert reference.wait(0) is False
    assert calls == [
        ("open", 0x1000 | 0x00100000, False, 123),
        ("query", 99333), ("wait", 99333, 0), ("wait", 99333, 0), ("close", 99333),
    ]


@pytest.mark.parametrize(
    "options,exception",
    [({"open_ok": False}, OSError), ({"query_ok": False}, OSError),
     ({"token": 123}, ProcessLookupError), ({"exited": True}, ProcessLookupError)],
)
def test_windows_capture_failure_closes_only_opened_reference(monkeypatch, options, exception):
    calls = _windows_kernel(monkeypatch, **options)
    with pytest.raises(exception):
        process_exit._open_windows(123, "456")
    assert [call for call in calls if call[0] == "close"] == (
        [] if options.get("open_ok") is False else [("close", 99333)]
    )


@pytest.mark.parametrize(
    "pid,token",
    [(True, "1"), (0, "1"), (-1, "1"), (2**32, "1"), ("1", "1"), (1, None), (1, ""), (1, "x"),
     (1, "\u0661"), (1, "1" * 65)],
)
def test_invalid_identity_refuses_before_native_open(monkeypatch, pid, token):
    monkeypatch.setattr(
        process_exit, "_open_windows",
        lambda *_: pytest.fail("invalid identity must not open a process"),
    )
    monkeypatch.setattr(
        process_exit, "_open_linux",
        lambda *_: pytest.fail("invalid identity must not open a process"),
    )
    with pytest.raises(ValueError, match="identity"):
        process_exit.open_process(pid, token)


def test_unsupported_platform_has_no_pid_fallback(monkeypatch):
    monkeypatch.setattr(process_exit.sys, "platform", "darwin")
    monkeypatch.setattr(
        diagnostics, "process_start_time",
        lambda *_: pytest.fail("unsupported platform must not guess process identity"),
    )
    with pytest.raises(NotImplementedError, match="native"):
        process_exit.open_process(123, "456")


def test_wait_and_close_remain_bound_to_captured_reference():
    calls = []
    reference = process_exit.ProcessExitReference(
        123, "456", lambda timeout: calls.append(("wait", timeout)) or True,
        lambda: calls.append(("close",)),
    )
    with reference:
        assert reference.wait(0.1) is True
    reference.close()
    assert calls[0][0] == "wait" and 0 <= calls[0][1] <= 0.1
    assert calls[1:] == [("close",)]
    with pytest.raises(RuntimeError, match="closed"):
        reference.wait(0)


@pytest.mark.parametrize("timeout", [-1, 31, True, None, "1", float("inf"), float("nan")])
def test_invalid_wait_budget_never_touches_reference(timeout):
    with process_exit.ProcessExitReference(
        123, "456", lambda *_: pytest.fail("invalid budget must not wait"), lambda: None,
    ) as reference:
        with pytest.raises(ValueError, match="timeout"):
            reference.wait(timeout)


def test_contended_wait_cannot_extend_callers_budget():
    entered, release = threading.Event(), threading.Event()

    def wait(_timeout):
        entered.set()
        assert release.wait(2)
        return True

    reference = process_exit.ProcessExitReference(123, "456", wait, lambda: None)
    worker = threading.Thread(target=lambda: reference.wait(2))
    worker.start()
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError, match="busy"):
            reference.wait(0)
    finally:
        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
        reference.close()


@pytest.mark.parametrize("operation", ["wait", "close", "__enter__"])
def test_inherited_reference_refuses_before_touching_parent_lock(monkeypatch, operation):
    reference = process_exit.ProcessExitReference(
        123, "456", lambda *_: pytest.fail("inherited reference must not wait"),
        lambda: pytest.fail("inherited reference must not close"),
    )

    class ParentLock:
        def acquire(self, **_):
            pytest.fail("must not acquire a possibly orphaned inherited lock")

        def __enter__(self):
            pytest.fail("must not enter a possibly orphaned inherited lock")

        def __exit__(self, *_):
            pass

    reference._lock = ParentLock()
    parent_pid = os.getpid()
    monkeypatch.setattr(process_exit.os, "getpid", lambda: parent_pid + 1)
    with pytest.raises(RuntimeError, match="another process"):
        getattr(reference, operation)(*([0] if operation == "wait" else []))


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux inherited-reference refusal")
def test_forked_child_refuses_orphaned_parent_lock_and_leaves_parent_reference_live():
    import select
    import signal

    token = diagnostics.process_start_time(os.getpid())
    assert token is not None
    with process_exit.open_process(os.getpid(), token) as reference:
        reader, writer = os.pipe()
        reference._lock.acquire()
        try:
            child = os.fork()
        except BaseException:
            reference._lock.release()
            os.close(reader)
            os.close(writer)
            raise
        if child == 0:
            os.close(reader)
            try:
                for operation in ("wait", "close", "__enter__"):
                    try:
                        getattr(reference, operation)(*([0] if operation == "wait" else []))
                    except RuntimeError as exc:
                        if "another process" not in str(exc):
                            os._exit(2)
                    else:
                        os._exit(3)
                os.write(writer, b"refused")
            finally:
                os._exit(0)
        os.close(writer)
        finished = False
        try:
            ready, _, _ = select.select([reader], [], [], 3)
            assert ready and os.read(reader, 64) == b"refused"
            _, status = os.waitpid(child, 0)
            finished = True
            assert os.waitstatus_to_exitcode(status) == 0
        finally:
            reference._lock.release()
            os.close(reader)
            if not finished:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
        assert reference.wait(0) is False


def test_linux_identity_change_closes_reference(monkeypatch):
    from zdd import singleton_linux

    closed = []
    monkeypatch.setattr(singleton_linux, "_open_pidfd", lambda _: 12345)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "different")
    monkeypatch.setattr(process_exit.os, "close", closed.append)
    with pytest.raises(ProcessLookupError, match="identity"):
        process_exit._open_linux(123, "456")
    assert closed == [12345]


def test_linux_exit_between_open_and_identity_validation_refuses(monkeypatch):
    from zdd import singleton_linux

    closed = []
    monkeypatch.setattr(singleton_linux, "_open_pidfd", lambda _: 12345)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "456")
    monkeypatch.setattr(process_exit.diagnostic_exit, "wait_posix", lambda *_: True)
    monkeypatch.setattr(process_exit.os, "close", closed.append)
    with pytest.raises(ProcessLookupError, match="exited"):
        process_exit._open_linux(123, "456")
    assert closed == [12345]


@pytest.mark.skipif(
    sys.platform not in {"linux", "win32"}, reason="native Linux/Windows process exit proof",
)
@pytest.mark.parametrize("wrong_token", [False, True])
@pytest.mark.parametrize(
    "high_fd",
    [False, pytest.param(True, marks=pytest.mark.skipif(
        sys.platform != "linux", reason="Linux high pidfd identity/exit proof",
    ))],
)
def test_real_graceful_exit_uses_reference_opened_before_control(monkeypatch, wrong_token, high_fd):
    if high_fd:
        import fcntl
        import resource
        from zdd import singleton_linux

        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
        if 4096 >= soft_limit:
            pytest.skip("native descriptor limit does not admit an fd above 4096")
        original_open = singleton_linux._open_pidfd

        def open_high(pid):
            fd = original_open(pid)
            try:
                return fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 4096)
            finally:
                os.close(fd)

        monkeypatch.setattr(singleton_linux, "_open_pidfd", open_high)
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; print('ready', flush=True); sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        token = diagnostics.process_start_time(child.pid)
        assert token is not None
        if wrong_token:
            with pytest.raises(ProcessLookupError, match="identity"):
                process_exit.open_process(child.pid, token + "1")
            assert child.poll() is None
        else:
            with process_exit.open_process(child.pid, token) as reference:
                assert reference.wait(0) is False
                monkeypatch.setattr(
                    diagnostics, "process_start_time",
                    lambda *_: pytest.fail("no late PID lookup after graceful control"),
                )
                child.stdin.close()
                assert reference.wait(5) is True
                child.wait(timeout=5)
    finally:
        if not child.stdin.closed:
            child.stdin.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
