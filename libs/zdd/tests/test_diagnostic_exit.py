"""Signal acceptance is not native process-exit confirmation."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from zdd import diagnostic_exit, diagnostics


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
