"""Bounded owned-process cleanup after native provider retirement."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import native_process
from agent_bridge.native_manager import NativeManager, ProviderTransport
from agent_bridge.native_store import NativeError


@pytest.mark.asyncio
async def test_real_provider_ignoring_eof_is_reaped_after_remote_retirement(tmp_path, monkeypatch):
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import json,sys,time\n"
        "print(json.dumps({'event':'ready','capability':'codespace-native-transport-v1'}),flush=True)\n"
        "request=json.loads(sys.stdin.readline())\n"
        "assert request['method']=='stop'\n"
        "result={'executionId':request['executionId'],'generation':request['generation'],"
        "'retired':True,'state':'stopped','exitCode':-9}\n"
        "print(json.dumps({'id':request['id'],'ok':True,'result':result}),flush=True)\n"
        "sys.stdin.read()\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    manager = NativeManager(tmp_path / "controller", lambda: pytest.fail("unexpected provider lookup"))
    store = manager.store(create=True)
    row, _ = store.reserve("execution", "generation", "request", "example-space", str(tmp_path), "hash", {
        "spec": {"codespace": "example-space", "localForward": [], "reverseForward": []},
        "launchRequested": True,
    })
    store.update("execution", "generation", state="ready")
    transport = ProviderTransport([sys.executable, "-u", str(provider)], row, lambda: None)
    unrelated = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time;time.sleep(60)", **no_window_kwargs(),
    )
    monkeypatch.setattr(native_process, "GRACE_SECONDS", .05)
    try:
        await transport.start(resume=True)
        manager.transports["execution"] = transport
        stopped = await asyncio.wait_for(manager.stop("execution", "generation"), 10)
        assert stopped["state"] == "stopped" and stopped["exitCode"] == -9
        assert transport.process.returncode is not None
        assert unrelated.returncode is None
        assert transport.reader_task.done() and transport.stderr_task.done()
        assert "execution" not in manager.transports
        assert store.get("execution")["data"]["providerRetirementProof"]["retired"] is True
        assert not store.get("execution")["data"]["transportCleanupPending"]
        with pytest.raises(NativeError, match="closing or disconnected"):
            await transport.request("stop")
    finally:
        for process in (transport.process, unrelated):
            if process is not None:
                if process.returncode is None:
                    process.kill()
                await process.wait()
        await transport.close()


@pytest.mark.asyncio
async def test_cleanup_cannot_succeed_when_owned_exit_is_unconfirmed(monkeypatch):
    class Process:
        returncode = None
        stdin = SimpleNamespace(close=lambda: None)

        async def wait(self):
            await asyncio.Future()

    process = Process()
    terminate = AsyncMock()
    monkeypatch.setattr(native_process, "GRACE_SECONDS", .01)
    monkeypatch.setattr(native_process, "EXIT_SECONDS", .01)
    monkeypatch.setattr(native_process, "_terminate_process_tree", terminate)
    transport = ProviderTransport([], {}, lambda: None)
    transport.process = process
    with pytest.raises(NativeError) as failure:
        await transport.close()
    assert failure.value.code == "retirement_unconfirmed"
    assert transport.closing and process.returncode is None
    terminate.assert_awaited_once_with(process)
    with pytest.raises(NativeError, match="closing or disconnected"):
        await transport.request("stop")


@pytest.mark.asyncio
async def test_exited_process_is_never_signalled_and_open_streams_remain_unconfirmed(monkeypatch):
    event = asyncio.Event()
    transport = ProviderTransport([], {}, lambda: None)
    transport.process = SimpleNamespace(returncode=0)
    transport.reader_task = asyncio.create_task(event.wait())
    monkeypatch.setattr(native_process, "DRAIN_SECONDS", .01)
    monkeypatch.setattr(
        native_process, "_terminate_process_tree",
        AsyncMock(side_effect=AssertionError("exited process must not authorize a PID kill")),
    )
    try:
        with pytest.raises(NativeError, match="streams remain open"):
            await transport.close()
        assert not transport.reader_task.done()
        event.set()
        await transport.drain_task
        await transport.close()
        assert transport.reader_task.done()
    finally:
        event.set()
        await transport.reader_task


@pytest.mark.asyncio
async def test_broken_input_pipe_is_classified_and_not_written_again():
    writes = []

    class Pipe:
        def write(self, value):
            writes.append(value)

        async def drain(self):
            raise ConnectionResetError("Connection lost")

    transport = ProviderTransport([], {"id": "execution", "generation": "generation"}, lambda: None)
    transport.process = SimpleNamespace(stdin=Pipe(), returncode=None)
    for _ in range(2):
        with pytest.raises(NativeError) as failure:
            await transport.request("stop")
        assert failure.value.code == "provider_unavailable"
        assert not transport.pending
    assert len(writes) == 1
