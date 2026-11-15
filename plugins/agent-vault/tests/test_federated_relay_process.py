"""Actual admission cancellation settles a synthetic native CLI, never real auth."""

import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_vault.federated_relay import selected_relay
from agent_procutil import JobHandle, spawn_in_kill_on_close_job
from credential_relay.sources import az_login
from test_federated_relay_service import configure, conduit, eventually, isolate_vault, running
from test_service_generation import _capture_process
from zdd.private_peer import PeerProcess
from zdd.private_protocol import Deadline

CHILD = """
import ctypes, json, os, subprocess, sys, time
from pathlib import Path
child = None
if sys.argv[2] == "tree":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
marker = Path(sys.argv[1])
pending = marker.with_suffix(".pending")
pending.write_text(json.dumps({
    "pid": os.getpid(), "child": child.pid if child else None,
    "console": bool(ctypes.windll.kernel32.GetConsoleWindow()) if os.name == "nt" else None}))
pending.replace(marker)
time.sleep(120)
"""


@pytest.mark.parametrize("cause", ["admission-timeout", "withdrawal", "overlapping-withdrawal"])
@pytest.mark.parametrize("tree", [False, pytest.param(
    True, marks=pytest.mark.skipif(os.name != "nt", reason="Native Windows CLI-wrapper Job"),
)])
def test_admission_loss_reaps_native_cli_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str, tree: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "vault"
    isolate_vault(root, monkeypatch)
    marker = tmp_path / "cli-ready.json"
    spawn = spawn_in_kill_on_close_job
    owned: list[tuple[asyncio.subprocess.Process, JobHandle | None]] = []

    async def recorded_spawn(
        *args: str, **kwargs: Any,
    ) -> tuple[asyncio.subprocess.Process, JobHandle | None]:
        pair = await spawn(*args, **kwargs)
        owned.append(pair)
        return pair

    monkeypatch.setattr(az_login, "spawn_in_kill_on_close_job", recorded_spawn)
    monkeypatch.setattr(
        az_login, "_az_argv",
        lambda _: [sys.executable, "-c", CHILD, str(marker), "tree" if tree else "direct"],
    )

    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, _):
                configure(root, rig, route, monkeypatch, native_admission=True)
                runtime = selected_relay()
                assert runtime is not None
                handles: list[int] = []
                peers: list[PeerProcess] = []
                kernel: Any = None
                cleanup_started, cleanup_release, cleanup_settled = (
                    asyncio.Event(), asyncio.Event(), asyncio.Event(),
                )
                withdrawal: asyncio.Task[None] | None = None
                if cause == "overlapping-withdrawal":
                    settle_process = az_login._settle_process

                    async def held_cleanup(
                        proc: asyncio.subprocess.Process, job: JobHandle | None, *, drain: bool,
                    ) -> None:
                        cleanup_started.set()
                        await cleanup_release.wait()
                        await settle_process(proc, job, drain=drain)
                        cleanup_settled.set()

                    monkeypatch.setattr(az_login, "_settle_process", held_cleanup)
                try:
                    await runtime.tick()
                    observed: dict[str, Any] | None = None

                    def readable_marker() -> bool:
                        nonlocal observed
                        try:
                            observed = json.loads(marker.read_text())
                        except (FileNotFoundError, PermissionError):
                            return False
                        return True

                    await eventually(readable_marker)
                    assert observed is not None
                    assert len(owned) == 1
                    process, job = owned[0]
                    assert process.returncode is None
                    if os.name == "nt":
                        assert job is not None and observed["console"] is False
                        from ctypes import wintypes

                        kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
                        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                        kernel.OpenProcess.restype = wintypes.HANDLE
                        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                        kernel.WaitForSingleObject.restype = wintypes.DWORD
                        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                        for pid in (observed["pid"], observed["child"]):
                            if pid is not None:
                                handle = kernel.OpenProcess(0x00100000, False, pid)
                                assert handle
                                handles.append(handle)
                                assert kernel.WaitForSingleObject(handle, 0) == 258
                    else:
                        assert process.pid == observed["pid"]
                    if cause == "overlapping-withdrawal":
                        peers = [
                            _capture_process(pid) for pid in (observed["pid"], observed["child"])
                            if pid is not None
                        ]
                        connector_task = runtime._task
                        assert connector_task is not None
                        withdrawal = asyncio.create_task(runtime.pause())
                        await asyncio.wait_for(cleanup_started.wait(), 3)
                        for _ in range(2):
                            withdrawal.cancel()
                            await asyncio.sleep(0)
                        await asyncio.wait_for(runtime.close(), 1)
                        assert not withdrawal.done() and not connector_task.done()
                        assert not cleanup_settled.is_set()
                        assert process.returncode is None and all(
                            not peer.exited(Deadline(1)) for peer in peers
                        )
                        cleanup_release.set()
                        with pytest.raises(asyncio.CancelledError):
                            await asyncio.wait_for(withdrawal, 8)
                        assert connector_task.done() and cleanup_settled.is_set()
                        for peer in peers:
                            peer.wait_exit(Deadline(3))
                        assert job is None or job.handle is None
                        with capsys.disabled():
                            print("native_cleanup_receipt=" + json.dumps({
                                "settled": True,
                                "processes": [
                                    {"pid": peer.peer.pid, "birth": peer.peer.start_time,
                                     "exit_confirmed": peer.exited(Deadline(1))}
                                    for peer in peers
                                ],
                                "returncode": process.returncode,
                                "job_closed": job is None or job.handle is None,
                            }))
                    elif cause == "withdrawal":
                        await asyncio.wait_for(runtime.pause(), 8)
                    else:
                        with pytest.raises((asyncio.TimeoutError, ValueError)):
                            assert runtime._task is not None
                            await asyncio.wait_for(asyncio.shield(runtime._task), 8)
                    assert process.returncode is not None
                    for handle in handles:
                        assert kernel.WaitForSingleObject(handle, 2000) == 0
                    assert not rig.state.channels and not runtime.status()["channel_attached"]
                finally:
                    cleanup_release.set()
                    await runtime.close()
                    if withdrawal is not None:
                        await asyncio.gather(withdrawal, return_exceptions=True)
                    for process, job in owned:
                        if job is not None:
                            job.close()
                        if process.returncode is None:
                            process.kill()
                        await process.communicate()
                    for handle in handles:
                        kernel.CloseHandle(handle)
                    for peer in peers:
                        peer.close()
    asyncio.run(scenario())
