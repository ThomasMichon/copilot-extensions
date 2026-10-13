"""Native finite registry probes retain descendant custody through pipe EOF."""

from __future__ import annotations

import os
import sys
import time
import ctypes

import pytest

from agent_dispatch import repository_resource_capture as capture


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
