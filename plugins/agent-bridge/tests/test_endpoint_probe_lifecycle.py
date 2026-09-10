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
    [(0, b"OK\n", True), (0, b"", False), (124, b"", False)],
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
            assert timeout == 10.0
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
