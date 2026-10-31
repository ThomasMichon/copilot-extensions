"""Subprocess ownership contracts for reconstructed relay-serving probes."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge.session_host import endpoints


pytestmark = pytest.mark.asyncio


def _make_probe(*, fail_open: bool = True):
    return endpoints.endpoint_serving_probe_factory(
        {"ssh": {"host_alias": "example.test"}},
        fail_open=fail_open,
    )(51000)


@pytest.fixture
def probe_runtime(monkeypatch):
    process = SimpleNamespace(
        returncode=None,
        communicate=AsyncMock(return_value=(b"OK\n", b"")),
    )
    spawn = AsyncMock(return_value=process)
    cleanup = AsyncMock()
    build_args = Mock(wraps=endpoints.build_remote_exec_args)
    monkeypatch.setattr(endpoints, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(endpoints, "terminate_ssh_process_tree", cleanup)
    monkeypatch.setattr(endpoints, "build_remote_exec_args", build_args)
    return SimpleNamespace(
        process=process, spawn=spawn, cleanup=cleanup, build_args=build_args,
    )


@pytest.mark.parametrize(
    ("returncode", "output", "expected"),
    [(0, b"OK\n", True), (0, b"", False), (124, b"", False),
     (0, b"NOT OK\n", False)],
)
async def test_endpoint_probe_uses_owned_ssh_launcher(
    probe_runtime, returncode, output, expected,
):
    probe_runtime.process.returncode = returncode
    probe_runtime.process.communicate.return_value = (output, b"")

    assert await _make_probe()() is expected

    probe_runtime.build_args.assert_called_once()
    config = probe_runtime.build_args.call_args.args[0]
    assert config.host_alias == "example.test"
    assert probe_runtime.spawn.await_args.kwargs["config"] is config
    assert probe_runtime.spawn.await_args.kwargs == {
        "config": config,
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.DEVNULL,
    }
    probe_runtime.cleanup.assert_not_awaited()


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
@pytest.mark.parametrize("fail_open", [True, False])
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_endpoint_probe_failure_after_spawn_awaits_tree_cleanup(
    probe_runtime, monkeypatch, caplog, failure, fail_open, cleanup_fails,
):
    real_wait_for = asyncio.wait_for
    communicating = asyncio.Event()
    cleanup_started = asyncio.Event()
    finish_cleanup = asyncio.Event()

    async def communicate():
        communicating.set()
        await asyncio.Future()

    async def cleanup(process):
        assert process is probe_runtime.process
        cleanup_started.set()
        await finish_cleanup.wait()
        process.returncode = -15
        if cleanup_fails:
            raise OSError("cleanup reporting failed")

    probe_runtime.process.communicate.side_effect = communicate
    probe_runtime.cleanup.side_effect = cleanup
    if failure == "timeout":
        async def short_timeout(awaitable, *, timeout):
            assert timeout == 60.0
            return await real_wait_for(awaitable, timeout=0.01)

        monkeypatch.setattr(endpoints.asyncio, "wait_for", short_timeout)

    task = asyncio.create_task(_make_probe(fail_open=fail_open)())
    try:
        await real_wait_for(communicating.wait(), timeout=1.0)
        probe_runtime.spawn.assert_awaited_once()
        assert probe_runtime.process.returncode is None
        if failure == "cancel":
            task.cancel()
        await real_wait_for(cleanup_started.wait(), timeout=1.0)
        assert not task.done()
        finish_cleanup.set()
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await real_wait_for(task, timeout=1.0)
        elif fail_open:
            assert await real_wait_for(task, timeout=1.0) is True
        else:
            with pytest.raises(TimeoutError):
                await real_wait_for(task, timeout=1.0)
        assert probe_runtime.process.returncode == -15
        probe_runtime.cleanup.assert_awaited_once_with(probe_runtime.process)
        if cleanup_fails:
            assert "SSH probe cleanup failed" in caplog.text
    finally:
        finish_cleanup.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@pytest.mark.parametrize("fail_open", [True, False])
async def test_endpoint_probe_spawn_failure_preserves_health_hint(
    probe_runtime, fail_open,
):
    probe_runtime.spawn.side_effect = OSError("SSH spawn failed")

    if fail_open:
        assert await _make_probe(fail_open=fail_open)() is True
    else:
        with pytest.raises(OSError, match="SSH spawn failed"):
            await _make_probe(fail_open=fail_open)()

    probe_runtime.cleanup.assert_not_awaited()


@pytest.mark.parametrize("reconstructed", [False, True])
async def test_slow_valid_probe_uses_selected_transport_budget(
    probe_runtime, monkeypatch, reconstructed,
):
    elapsed = 17.0
    budget = 90.0
    real_wait_for = asyncio.wait_for
    now = [0.0]
    monkeypatch.setattr(
        endpoints.asyncio, "get_running_loop",
        lambda: SimpleNamespace(time=lambda: now[0]),
    )

    async def selected_wait_for(awaitable, *, timeout):
        assert timeout == budget
        assert timeout > elapsed
        if reconstructed and now[0] == 0.0:
            now[0] += elapsed
        return await real_wait_for(awaitable, timeout=timeout)

    async def run(command, *, timeout):
        assert timeout == budget
        assert timeout > elapsed
        assert 'read -r -t 3' in command
        now[0] += elapsed
        return 0, "OK\n", ""

    monkeypatch.setattr(endpoints.asyncio, "wait_for", selected_wait_for)
    probe_runtime.process.returncode = 0
    if reconstructed:
        probe = endpoints.endpoint_serving_probe_factory(
            {"ssh": {"host_alias": "example.test"}},
            fail_open=False, timeout=budget,
        )(51000)
    else:
        probe = endpoints.transport_serving_probe(
            SimpleNamespace(run=run), 51000, fail_open=False, timeout=budget,
        )
    await endpoints.wait_for_relay_serving(probe, timeout=budget)
    assert now[0] == elapsed
    probe_runtime.cleanup.assert_not_awaited()
    if reconstructed:
        assert probe_runtime.build_args.call_args.kwargs["connect_timeout"] == budget
        argv = probe_runtime.spawn.await_args.args
        assert "ConnectTimeout=90" in argv
        assert "ConnectTimeout=15" not in argv


async def test_spawner_propagates_configured_readiness_budget():
    from agent_bridge.session_host.spawner import CodeSpaceSpawner

    transport = SimpleNamespace(run=AsyncMock(return_value=(0, "OK\n", "")))
    spawner = CodeSpaceSpawner(transport, ready_timeout=150.0)
    assert spawner._relay_ready_timeout == 150.0
    assert await spawner._serving_probe_for_port(51000, fail_open=False)()
    assert transport.run.await_args.kwargs["timeout"] == 150.0


@pytest.mark.parametrize("output", ["NOT OK\n", "", "OK extra\n"])
async def test_transport_probe_rejects_invalid_protocol_result(output):
    transport = SimpleNamespace(run=AsyncMock(return_value=(0, output, "")))
    assert not await endpoints.transport_serving_probe(
        transport, 51000, fail_open=False,
    )()


@pytest.mark.parametrize("cancel", [False, True])
async def test_readiness_deadline_cancels_and_cleans_owned_probe(
    probe_runtime, cancel,
):
    communicating = asyncio.Event()

    async def communicate():
        communicating.set()
        await asyncio.Future()

    probe_runtime.process.communicate.side_effect = communicate
    task = asyncio.create_task(endpoints.wait_for_relay_serving(
        _make_probe(fail_open=False), timeout=0.05 if not cancel else 90.0,
    ))
    await communicating.wait()
    if cancel:
        task.cancel()
    with pytest.raises(
        asyncio.CancelledError if cancel else endpoints.CredentialRelayReadinessError,
    ):
        await task
    probe_runtime.cleanup.assert_awaited_once_with(probe_runtime.process)


async def test_readiness_failure_exhausts_fake_monotonic_budget(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(
        endpoints.asyncio, "get_running_loop",
        lambda: SimpleNamespace(time=lambda: now[0]),
    )

    async def failed_probe():
        now[0] += 91.0
        return False

    with pytest.raises(endpoints.CredentialRelayReadinessError):
        await endpoints.wait_for_relay_serving(failed_probe, timeout=90.0)


@pytest.mark.parametrize("budget", [17.25, 90.0, 150.0])
async def test_transport_command_uses_selected_ssh_connection_budget(monkeypatch, budget):
    import math
    from ssh_manager import ConnectionManager, SSHConfig
    from ssh_manager import manager as ssh_manager

    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"OK\n", b"")),
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(ssh_manager, "create_ssh_subprocess", spawn)
    manager = ConnectionManager.__new__(ConnectionManager)
    manager._connections = {
        "example": SimpleNamespace(
            config=SSHConfig(host_alias="example.test"), multiplexed=True,
            socket_path="owned-socket", env={}, child_processes=[],
        ),
    }
    result = await manager.exec_command("example", "relay-ping", timeout=budget)
    assert result.ok
    options = [arg for arg in spawn.await_args.args if arg.startswith("ConnectTimeout=")]
    assert options == [f"ConnectTimeout={math.ceil(budget)}"]
