"""Native finite registry probes retain descendant custody through pipe EOF."""

from __future__ import annotations

import os
import sys
import time
import ctypes
import subprocess

import pytest

from agent_dispatch import repository_resource_capture as capture
from agent_dispatch.registrar import RegistrarError


@pytest.mark.parametrize("identity", ["reaped", "unknown"])
def test_posix_probe_cleanup_refuses_unowned_group(monkeypatch, identity):
    proc = subprocess.CompletedProcess([], 0 if identity == "reaped" else None)
    proc.pid = 4242
    signals = []
    monkeypatch.setattr(capture.os, "killpg", lambda *a: signals.append(a), raising=False)

    def unknown(*args):
        raise ChildProcessError("not an unreaped child")

    monkeypatch.setattr(capture.os, "waitid", unknown, raising=False)
    for key in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT"):
        if not hasattr(capture.os, key):
            monkeypatch.setattr(capture.os, key, 0, raising=False)
    with pytest.raises(RegistrarError, match="custody"):
        capture._retire_posix_group(proc)
    assert signals == []


@pytest.mark.skipif(os.name == "nt", reason="native POSIX unreaped-leader custody")
def test_registry_probe_posix_timeout_reaps_descendant_after_leader_exit(tmp_path, monkeypatch):
    pidfile = tmp_path / "descendant.pid"
    marker = tmp_path / "survived"
    child = (
        "import os,time;from pathlib import Path;"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()));"
        f"time.sleep(4);Path({str(marker)!r}).touch()"
    )
    leader = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(.2)"
    )
    monkeypatch.setattr(capture, "agent_worktrees_launch_prefix", lambda: [sys.executable])
    started = time.monotonic()
    assert capture.run_agent_worktrees_capture("-c", leader, timeout=1) is None
    assert time.monotonic() - started < 3
    assert pidfile.exists(), "descendant must actually start before the timeout"
    time.sleep(4)
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX unreaped-leader custody")
def test_registry_probe_posix_retirement_precedes_reap(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "agent_worktrees_launch_prefix", lambda: [sys.executable])
    retire = capture._retire_posix_group
    identities = []

    def checked(proc):
        identities.append((proc.pid, proc.returncode))
        retire(proc)

    monkeypatch.setattr(capture, "_retire_posix_group", checked)
    result = capture.run_agent_worktrees_capture(
        "-c", "import sys;print('unicode: \u03b1');sys.stderr.write('error')", timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == "unicode: \u03b1\n"
    assert result.stderr == "error"
    assert len(identities) == 1 and identities[0][1] is None


@pytest.mark.skipif(os.name == "nt", reason="native POSIX unreaped-leader custody")
def test_registry_probe_posix_waits_for_leader_after_pipe_eof(tmp_path, monkeypatch):
    marker = tmp_path / "finished"
    monkeypatch.setattr(capture, "agent_worktrees_launch_prefix", lambda: [sys.executable])
    result = capture.run_agent_worktrees_capture(
        "-c", "import os,time;from pathlib import Path;os.close(1);os.close(2);"
        f"time.sleep(.2);Path({str(marker)!r}).touch()", timeout=5,
    )
    assert result.returncode == 0
    assert marker.exists()


def test_registry_probe_native_success_preserves_scrubbed_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(capture, "agent_worktrees_launch_prefix", lambda: [sys.executable])
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "unrelated-payload")
    monkeypatch.setenv("RESOURCE_CAPTURE_FIXTURE", "kept")
    result = capture.run_agent_worktrees_capture(
        "-c",
        "import os;assert 'COPILOT_PLUGIN_ROOT' not in os.environ;"
        "print(os.environ['RESOURCE_CAPTURE_FIXTURE'])",
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "kept"


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job containment contract")
def test_registry_probe_native_timeout_reaps_descendant_after_leader_exit(tmp_path, monkeypatch):
    from ctypes import WinDLL
    from ctypes import wintypes

    pidfile = tmp_path / "descendant.pid"
    child = (
        "import os,time;from pathlib import Path;"
        f"Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(30)"
    )
    leader = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(.3)"
    )
    monkeypatch.setattr(capture, "agent_worktrees_launch_prefix", lambda: [sys.executable])
    started = time.monotonic()
    assert capture.run_agent_worktrees_capture("-c", leader, timeout=2) is None
    assert time.monotonic() - started < 5
    assert pidfile.exists(), "descendant must have actually started"
    pid = int(pidfile.read_text())
    kernel = WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if handle:
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            assert code.value != 259, "finite-probe descendant survived Job closure"
        finally:
            kernel.CloseHandle(handle)
