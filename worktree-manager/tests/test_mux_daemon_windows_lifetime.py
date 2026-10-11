"""Production mux starts survive caller Job cleanup without losing PID ownership."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import agent_procutil as pu
import pytest

from worktree_manager import mux_daemon, mux_daemon_cutover

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="native Windows Job lifetime"),
]

_CALLER = """
import json, os, pathlib, subprocess, sys, time
from agent_procutil import no_window_kwargs
from worktree_manager import mux_daemon, mux_daemon_cutover, mux_daemon_process
root = pathlib.Path(sys.argv[1])
if sys.argv[2] == 'ordinary':
    assert mux_daemon_process.spawn_detached([
        sys.executable, '-m', 'worktree_manager', 'mux-daemon', 'run', '--root=' + str(root),
    ])
else:
    port = mux_daemon_cutover.pick_free_port()
    proc = mux_daemon_cutover.spawn_passive(pathlib.Path(sys.argv[3]), root=root, port=port)
    data = {'pid': proc.pid, 'manager_mux_endpoint': '127.0.0.1:' + str(port)}
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    if sys.argv[2] == 'ordinary':
        data = mux_daemon.read_lock_data(mux_daemon.lock_path(root))
        if mux_daemon._daemon_is_live(data):
            break
    else:
        from work_coalescing_singleton.client import DaemonUnavailable
        try:
            client = mux_daemon_cutover.ControlClient('http://' + data['manager_mux_endpoint'], root=root)
            if client.health()['status'] == 'ready':
                break
        except DaemonUnavailable:
            if proc.poll() is not None:
                raise RuntimeError('fixture passive daemon exited')
    time.sleep(.05)
else:
    raise RuntimeError('fixture daemon did not become healthy')
control = subprocess.Popen(
    [sys.executable, '-c', 'import time; time.sleep(60)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    **no_window_kwargs(),
)
(root / 'ready.json').write_text(json.dumps({
    'daemon': data['pid'], 'control': control.pid, 'endpoint': data['manager_mux_endpoint'],
}), encoding='utf-8')
"""


@pytest.mark.parametrize("mode", ["ordinary", "passive"])
def test_mux_daemon_survives_caller_job(
    tmp_path: Path, mode: str,
) -> None:
    root = tmp_path / "isolated runtime"
    root.mkdir()
    env = dict(os.environ)
    # Only finite, isolated fixture daemons escape this fixture's inner Job.
    # The outer test retains containment and never changes a deployed service.
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["WORKTREE_MANAGER_ROOT"] = str(root)
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handles: dict[str, object] = {}
    caller = None
    job = None
    client = None
    try:
        caller, job = pu.spawn_sync_in_kill_on_close_job(
            [sys.executable, "-c", _CALLER, str(root), mode,
             str(Path(mux_daemon.__file__).resolve().parents[2])],
            env=env, cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **pu.no_window_kwargs(),
        )
        assert job is not None, "native Job assignment is required for this test"
        stdout, stderr = caller.communicate(timeout=30)
        assert caller.returncode == 0, (stdout, stderr)
        ready = json.loads((root / "ready.json").read_text(encoding="utf-8"))
        for name in ("daemon", "control"):
            handle = api.OpenProcess(0x00100000 | 0x0001, False, ready[name])
            assert handle, f"cannot pin fixture {name}"
            handles[name] = handle
        client = mux_daemon_cutover.ControlClient(
            f"http://{ready['endpoint']}", root=root,
        )
        assert client.health()["status"] == "ready"
        job.close()
        assert api.WaitForSingleObject(handles["control"], 5000) == 0
        assert api.WaitForSingleObject(handles["daemon"], 0) == 258
        assert client.health()["status"] == "ready"
    finally:
        if job is not None:
            job.close()
        if caller is not None and caller.poll() is None:
            caller.wait(timeout=5)
        if client is not None:
            client.shutdown()
        for name, handle in handles.items():
            if api.WaitForSingleObject(handle, 15000) != 0:
                assert api.TerminateProcess(handle, 1), f"fixture {name} cleanup failed"
                assert api.WaitForSingleObject(handle, 5000) == 0
            api.CloseHandle(handle)
