"""Private Windows writers exercise the real finite acquisition owner."""

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

import pytest

from agent_worktrees import installer_run

pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(os.name != "nt", reason="held Windows Job write accounting"),
]

_CHILD = """
import os,sys,time
mode,path=sys.argv[1:]
with open(path,'w+b',buffering=0) as stream:
 stream.write(b'x'*4096)
 until=time.monotonic()+(1.6 if mode in ('complete','stall') else 30)
 while time.monotonic()<until:
  if mode=='read':
   stream.seek(0);stream.read(4096)
  else:
   stream.write(b'x'*4096)
  time.sleep(.05)
 if mode=='stall': time.sleep(30)
"""
_ROOT = """
import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-I','-B','-c',sys.argv[1],sys.argv[2],sys.argv[3]])
while not Path(sys.argv[3]).exists() or Path(sys.argv[3]).stat().st_size<4096:
 time.sleep(.01)
ready=Path(sys.argv[4])
staged=ready.with_suffix('.tmp')
staged.write_text(json.dumps({'root':os.getpid(),'child':child.pid}))
staged.replace(ready)
child.wait()
os.write(1,b'unchanged-out\\n')
os.write(2,b'unchanged-err\\n')
"""


@pytest.mark.parametrize(
    "mode,observe",
    [
        ("complete", True),
        ("complete", False),
        ("stall", True),
        ("read", True),
        ("absolute", True),
        ("failure", True),
    ],
)
def test_native_writes_quiet_limits_and_exact_descendant_exit(
    tmp_path,
    monkeypatch,
    mode,
    observe,
):
    from agent_procutil import job_activity

    original_spawn = installer_run._spawn
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles, spawned, lines = [], [], []
    ready, data = tmp_path / "ready.json", tmp_path / "writes"

    def spawn(*args, **kwargs):
        proc, job = original_spawn(*args, **kwargs)
        spawned.append((proc, job))
        until = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < until:
            assert proc.poll() is None
            time.sleep(0.01)
        assert ready.exists()
        identities = json.loads(ready.read_text())
        assert identities["root"] == proc.pid
        for pid in identities.values():
            handle = kernel.OpenProcess(0x00100000, False, pid)
            assert handle, ctypes.get_last_error()
            handles.append(handle)
        return proc, job

    monkeypatch.setattr(installer_run, "_spawn", spawn)
    if mode == "failure":

        def fail(_job):
            raise job_activity.JobActivityError("fixture accounting unavailable")

        monkeypatch.setattr(job_activity, "_write_count", fail)
    started = time.monotonic()
    try:
        call = lambda: installer_run.run_installer(
            [
                sys._base_executable,
                "-I",
                "-B",
                "-c",
                _ROOT,
                _CHILD,
                "absolute" if mode == "failure" else mode,
                str(data),
                str(ready),
            ],
            cwd=tmp_path,
            env=os.environ,
            deadline_s=0.6,
            log=lines.append,
            require_tree_exit=True,
            **(
                {"native_write_deadline": started + (1.2 if mode == "absolute" else 8)}
                if observe
                else {}
            ),
        )
        if mode == "failure":
            with pytest.raises(installer_run.InstallerActivityError, match="verified cleanup"):
                call()
        else:
            result = call()
            assert (result.status == "OK") is (mode == "complete" and observe)
            if result.status == "OK":
                assert set(lines) == {"unchanged-out", "unchanged-err"}
                assert result.duration_s > 0.6
            else:
                assert result.status.startswith("timed out") and result.rc is None
                assert not lines
        if mode != "failure":
            assert data.stat().st_size >= 4096
        if mode == "absolute":
            assert 1.2 <= time.monotonic() - started < 3
        if mode == "stall":
            assert 1.6 < time.monotonic() - started < 4
        assert len(handles) == 2
        assert all(kernel.WaitForSingleObject(handle, 1000) == 0 for handle in handles)
        assert all(proc.returncode is not None and job.handle is None for proc, job in spawned)
    finally:
        for proc, job in spawned:
            if job.handle:
                job.terminate_and_wait(10)
            proc.wait(timeout=10)
        for handle in handles:
            assert kernel.CloseHandle(handle)
