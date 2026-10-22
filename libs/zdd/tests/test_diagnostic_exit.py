"""Signal acceptance is not native process-exit confirmation."""

import subprocess
import sys
import os
import time
from types import SimpleNamespace

import pytest

from zdd import diagnostic_exit, diagnostics


@pytest.mark.parametrize("events,confirmed", [([], False), ([(4096, 1)], True)])
def test_posix_wait_supports_native_descriptors_above_fd_setsize(monkeypatch, events, confirmed):
    calls = []

    class Poll:
        def register(self, fd, flags):
            calls.append(("register", fd, flags))

        def poll(self, milliseconds):
            calls.append(("poll", milliseconds))
            return events

    monkeypatch.setattr(diagnostic_exit.select, "poll", Poll, raising=False)
    for name, value in [("POLLIN", 1), ("POLLERR", 8), ("POLLHUP", 16), ("POLLNVAL", 32)]:
        monkeypatch.setattr(diagnostic_exit.select, name, value, raising=False)
    assert diagnostic_exit.wait_posix(4096, 0.05) is confirmed
    assert calls == [("register", 4096, 1), ("poll", 50)]


@pytest.mark.parametrize("flags", [8, 32])
def test_posix_wait_error_is_not_exit_evidence(monkeypatch, flags):
    class Poll:
        def register(self, *_):
            pass

        def poll(self, _):
            return [(4096, flags)]

    monkeypatch.setattr(diagnostic_exit.select, "poll", Poll, raising=False)
    for name, value in [("POLLIN", 1), ("POLLERR", 8), ("POLLHUP", 16), ("POLLNVAL", 32)]:
        monkeypatch.setattr(diagnostic_exit.select, name, value, raising=False)
    with pytest.raises(OSError, match="native"):
        diagnostic_exit.wait_posix(4096, 0)


@pytest.mark.parametrize("exited", [False, True])
def test_posix_liveness_uses_exit_evidence_before_pid_existence(monkeypatch, exited):
    calls = []
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(os, "pidfd_open", lambda pid: 123, raising=False)
    monkeypatch.setattr(os, "close", calls.append)
    monkeypatch.setattr(diagnostic_exit, "wait_posix", lambda fd, timeout: exited)
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("PID existence is not exit evidence"))
    assert diagnostics._pid_alive(456) is (not exited)
    assert calls == [123]


def test_failed_posix_exit_probe_preserves_custody_and_closes_descriptor(monkeypatch):
    closed = []
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(os, "pidfd_open", lambda pid: 123, raising=False)
    monkeypatch.setattr(os, "close", closed.append)

    def unavailable(*_):
        raise OSError("exit probe unavailable")

    monkeypatch.setattr(diagnostic_exit, "wait_posix", unavailable)
    assert diagnostics._pid_alive(456) is True
    assert closed == [123]


@pytest.mark.parametrize("error,alive", [
    (ProcessLookupError(), False), (PermissionError(), True), (OSError(), True),
])
def test_posix_exit_handle_failure_retains_conservative_pid_fallback(monkeypatch, error, alive):
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")

    def unavailable(_):
        raise error

    monkeypatch.setattr(os, "pidfd_open", unavailable, raising=False)
    monkeypatch.setattr(os, "kill", lambda *_: None)
    assert diagnostics._pid_alive(456) is alive


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux unreaped-process exit proof")
@pytest.mark.parametrize("without_python_pidfd", [False, True])
def test_exited_unreaped_child_is_not_a_live_retention_owner(monkeypatch, without_python_pidfd):
    from zdd.singleton_linux import _open_pidfd

    if without_python_pidfd:
        monkeypatch.delattr(os, "pidfd_open", raising=False)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready',flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE, text=True,
    )
    descriptor = None
    try:
        assert child.stdout.readline().strip() == "ready"
        assert diagnostics._pid_alive(child.pid)
        descriptor = _open_pidfd(child.pid)
        child.terminate()
        assert diagnostic_exit.wait_posix(descriptor, 5)
        # Do not poll/wait: its parent still owns the unreaped process record.
        os.kill(child.pid, 0)
        assert not diagnostics._pid_alive(child.pid)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


@pytest.mark.parametrize("timeout", [-1, 31, float("inf"), float("nan"), True, "1"])
def test_invalid_exit_timeout_refuses_before_signaling(monkeypatch, timeout):
    monkeypatch.setattr(
        diagnostics, "_terminate_posix_if_identity",
        lambda *_: pytest.fail("invalid deadline must not signal"),
    )
    with pytest.raises(ValueError, match="exit_timeout"):
        diagnostics.terminate_pid_if_identity(123, "fixture", exit_timeout=timeout)


@pytest.mark.parametrize("wait_result,confirmed", [(0, True), (0x102, False)])
def test_windows_wait_uses_the_same_native_handle(wait_result, confirmed):
    calls = []

    class Wait:
        def __call__(self, handle, milliseconds):
            calls.append((handle, milliseconds))
            return wait_result

    kernel32 = SimpleNamespace(WaitForSingleObject=Wait())
    assert diagnostic_exit.wait_windows(kernel32, 12345, 0.05) is confirmed
    assert calls == [(12345, 50)]


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux pidfd exit proof")
@pytest.mark.parametrize("ignore_term", [False, True])
def test_exit_is_confirmed_through_the_signaled_pidfd(ignore_term):
    script = (
        "import signal,time; "
        + ("signal.signal(signal.SIGTERM,signal.SIG_IGN); " if ignore_term else "")
        + "print('ready',flush=True); time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        start = diagnostics.process_start_time(child.pid)
        result = diagnostics.terminate_pid_if_identity(child.pid, start, exit_timeout=0.2)
        assert result["killed"] and result["identity_verified"]
        assert result["exit_confirmed"] is (not ignore_term)
        if not ignore_term:
            child.wait(timeout=1)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=1)
        child.stdout.close()


@pytest.mark.skipif(sys.platform != "linux", reason="native Linux graceful cleanup")
@pytest.mark.parametrize("active", [False, True])
def test_passive_cleanup_does_not_wait_on_termination_routing_fence(tmp_path, active):
    from zdd import routing

    if active:
        routing.publish_active(tmp_path, bind="127.0.0.1", port=61001, pid=os.getpid())
    else:
        routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    script = (
        "import os,signal,sys,time\n"
        "from zdd import routing\n"
        "def stop(*_):\n"
        "    routing.clear_if_owner(sys.argv[1],os.getpid())\n"
        "    raise SystemExit\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        "print('ready',flush=True)\n"
        "time.sleep(30)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        start = diagnostics.process_start_time(child.pid)
        with routing.routing_lock(tmp_path, deadline=time.monotonic() + 1):
            result = diagnostics.terminate_pid_if_identity(child.pid, start, exit_timeout=1)
            assert result["exit_confirmed"] is True
        child.wait(timeout=1)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=1)
        child.stdout.close()
