import asyncio
from types import SimpleNamespace

import pytest

from ssh_manager import manager as module


class Process:
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdin = None
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0


def manager(monkeypatch, process):
    item = module.ConnectionManager.__new__(module.ConnectionManager)
    info = SimpleNamespace(
        config=SimpleNamespace(ssh_target="fixture-alias"),
        env={}, multiplexed=True, child_processes=[],
    )
    item._connections = {"fixture-alias": info}
    monkeypatch.setattr(item, "_mux_ssh_args", lambda info: [])

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(module, "create_ssh_subprocess", spawn)
    return item, info


@pytest.mark.asyncio
async def test_existing_exec_boundary_forwards_output_before_command_completion(monkeypatch):
    process = Process()
    item, info = manager(monkeypatch, process)
    received = asyncio.Event()
    updates = []

    def output(name, chunk):
        updates.append((name, chunk))
        received.set()

    operation = asyncio.create_task(item.exec_command(
        "fixture-alias", "fixture resolve", on_output=output,
    ))
    process.stderr.feed_data(b"[launch] instruction synchronization: starting\n")
    await asyncio.wait_for(received.wait(), timeout=2)
    assert not operation.done()
    assert process in info.child_processes
    assert updates == [("stderr", b"[launch] instruction synchronization: starting\n")]
    process.stdout.feed_data(b'{"worktree":"same"}\n')
    process.stdout.feed_eof()
    process.stderr.feed_eof()
    result = await operation
    assert result.stdout == '{"worktree":"same"}'
    assert result.stderr == "[launch] instruction synchronization: starting"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert info.child_processes == []


@pytest.mark.asyncio
async def test_callback_failure_preserves_error_and_owned_cleanup(monkeypatch):
    process = Process()
    item, info = manager(monkeypatch, process)
    terminated = []

    async def terminate(child):
        terminated.append(child)
        child.returncode = -1
        child.stdout.feed_eof()
        child.stderr.feed_eof()

    monkeypatch.setattr(module, "_terminate_process_tree", terminate)
    error = ValueError("progress observer failed")

    def output(*args):
        raise error

    process.stderr.feed_data(b"target update")
    with pytest.raises(ValueError) as raised:
        await item.exec_command("fixture-alias", "fixture resolve", on_output=output)
    assert raised.value is error
    assert terminated == [process]
    assert info.child_processes == []
