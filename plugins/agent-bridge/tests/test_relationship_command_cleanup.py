"""Authority deadlines include bounded, owned child cleanup."""

from __future__ import annotations

import asyncio
import ctypes
import json
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge import relationship_authority as authority
from test_relationship_authority import process as process


@pytest.mark.asyncio
async def test_cleanup_pipe_collection_has_its_own_deadline(process, monkeypatch, caplog):
    proc, _ = process
    monkeypatch.setattr(authority, "_CLEANUP_TIMEOUT", 0.02)

    async def communicate(data=None):
        if data is not None:
            raise TimeoutError("transaction deadline")
        await asyncio.Event().wait()

    proc.returncode = None
    proc.communicate = AsyncMock(side_effect=communicate)
    started = time.monotonic()
    with pytest.raises(authority.RelationshipUnknown, match="cleanup is unconfirmed"):
        await authority.command("reserve", "local/project/worktree", {})
    assert time.monotonic() - started < 1
    assert "cleanup is unconfirmed" in caplog.text
    proc._job.close.assert_called_once()


@pytest.mark.asyncio
async def test_native_retirement_failure_is_unknown_and_bounded(process, monkeypatch):
    proc, _ = process
    monkeypatch.setattr(authority, "_CLEANUP_TIMEOUT", 0.02)
    proc.communicate.side_effect = TimeoutError("transaction deadline")
    proc._job.terminate_and_wait.side_effect = OSError("termination not confirmed")
    started = time.monotonic()
    with pytest.raises(authority.RelationshipUnknown, match="cleanup is unconfirmed"):
        await authority.command("reserve", "local/project/worktree", {})
    assert time.monotonic() - started < 1
    proc._job.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancellation_with_unconfirmed_pipe_cleanup_stays_cancellation(process, monkeypatch):
    proc, _ = process
    monkeypatch.setattr(authority, "_CLEANUP_TIMEOUT", 0.02)
    entered = asyncio.Event()

    async def communicate(data=None):
        entered.set()
        await asyncio.Event().wait()

    proc.returncode = None
    proc.communicate = AsyncMock(side_effect=communicate)
    pending = asyncio.create_task(authority.command("reserve", "local/project/worktree", {}))
    await asyncio.wait_for(entered.wait(), 1)
    started = time.monotonic()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError, match="cleanup is unconfirmed"):
        await pending
    assert pending.cancelled() and time.monotonic() - started < 1
    proc._job.close.assert_called_once()


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_close_job_during_native_retirement(process):
    proc, _ = process
    proc.communicate.side_effect = TimeoutError("transaction deadline")
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def retire(timeout):
        loop.call_soon_threadsafe(entered.set)
        if not release.wait(1):
            raise RuntimeError("test retirement was not released")
        proc.returncode = 1

    proc._job.terminate_and_wait.side_effect = retire
    pending = asyncio.create_task(authority.command("reserve", "local/project/worktree", {}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()
        proc._job.close.assert_not_called()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    proc._job.close.assert_called_once()


@pytest.mark.asyncio
async def test_missing_native_custody_never_supplies_transaction_stdin(process, monkeypatch):
    from zdd import diagnostics

    proc, start = process
    proc.pid = 12345
    start.return_value = (proc, None)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: None)
    with pytest.raises(authority.RelationshipRefused, match="custody is unavailable"):
        await authority.command("reserve", "local/project/worktree", {})
    proc.stdin.close.assert_called_once()
    proc.communicate.assert_not_called()


@pytest.mark.asyncio
async def test_incompatible_job_api_refuses_before_transaction_stdin(process):
    proc, start = process
    job = SimpleNamespace(close=Mock())
    start.return_value = proc, job
    with pytest.raises(authority.RelationshipRefused, match="retirement is unavailable"):
        await authority.command("reserve", "local/project/worktree", {})
    proc.stdin.close.assert_called_once()
    proc.communicate.assert_not_called()


def _job_contains(job, pid):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        contained = wintypes.BOOL()
        if not kernel.IsProcessInJob(process, job.handle, ctypes.byref(contained)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(contained.value)
    finally:
        kernel.CloseHandle(process)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows Job ownership; POSIX uses the existing group primitive")
@pytest.mark.parametrize("parent_exits", [False, True])
async def test_actual_timeout_retires_descendant_and_inherited_pipes(monkeypatch, tmp_path, parent_exits):
    marker = tmp_path / "command-descendant.json"
    code = """
import json,os,subprocess,sys,time
from pathlib import Path
from agent_procutil import no_window_kwargs
sys.stdin.buffer.read()
capture = '''
import ctypes,os
from ctypes import wintypes
kernel,user=ctypes.windll.kernel32,ctypes.windll.user32
kernel.GetConsoleWindow.restype=wintypes.HWND
user.GetForegroundWindow.restype=wintypes.HWND
user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
def snapshot():
    foreground=wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(),ctypes.byref(foreground))
    return {'console':int(kernel.GetConsoleWindow() or 0),'foreground':foreground.value==os.getpid()}
'''
exec(capture)
child_code = capture + '''
import json,sys,time
from pathlib import Path
samples=[]
for _ in range(2):
    samples.append(snapshot())
    time.sleep(.05)
Path(sys.argv[1]).write_text(json.dumps(samples))
time.sleep(60)
'''
child_marker=Path(sys.argv[1]).with_suffix('.child.json')
child = subprocess.Popen([sys.executable, '-c', child_code, str(child_marker)],
                         stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                         **no_window_kwargs())
samples=[]
for _ in range(2):
    samples.append(snapshot())
    time.sleep(.05)
deadline=time.monotonic()+2
while not child_marker.exists() and time.monotonic()<deadline:
    time.sleep(.01)
Path(sys.argv[1]).write_text(json.dumps({
    'root':os.getpid(),'child':child.pid,'samples':samples,
    'child_samples':json.loads(child_marker.read_text())}))
if sys.argv[2]=='exit':
    raise SystemExit(0)
time.sleep(60)
"""
    spawn = authority.spawn_in_kill_on_close_job
    evidence = SimpleNamespace(retired=False, contained=False, process=None)

    async def owned(*args, **kwargs):
        proc, job = await spawn(*args, **kwargs)
        assert job is not None
        evidence.process = proc

        def retire(timeout):
            ids = json.loads(marker.read_text())
            assert ids["samples"] == [{"console": 0, "foreground": False}] * 2
            assert ids["child_samples"] == [{"console": 0, "foreground": False}] * 2
            evidence.contained = _job_contains(job, ids["child"])
            job.terminate_and_wait(timeout)
            evidence.retired = True

        return proc, SimpleNamespace(terminate_and_wait=retire, close=job.close)

    monkeypatch.setattr(authority, "spawn_in_kill_on_close_job", owned)
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda **kwargs: [
        sys.executable, "-c", code, str(marker), "exit" if parent_exits else "wait",
    ])
    monkeypatch.setattr(authority, "_TIMEOUT", 3)
    monkeypatch.setattr(authority, "_CLEANUP_TIMEOUT", 2)
    started = time.monotonic()
    with pytest.raises(authority.RelationshipUnknown, match="outcome is unknown"):
        await authority.command("reserve", "local/project/worktree", {})
    assert 2.5 <= time.monotonic() - started < 10
    assert evidence.contained and evidence.retired
    assert evidence.process.returncode is not None


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "linux", reason="Native Linux process-group authority; not Windows or macOS")
async def test_actual_linux_command_uses_identity_checked_process_group(monkeypatch):
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda **kwargs: [
        sys.executable, "-c",
        "import json,sys;sys.stdin.buffer.read();"
        "print(json.dumps({'schema':'copilot-extensions.resource-relationship','version':1,'ok':True}))",
    ])
    result = await authority.command("capabilities")
    assert result["ok"] is True
