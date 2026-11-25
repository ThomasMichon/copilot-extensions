"""Bounded process transport and cancellation cleanup, without child actors."""
import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from ssh_manager.config_sources import SSHConfig

from agent_ssh_service.native_receiver import PublicObserver


@pytest.mark.parametrize("mode", ["success", "failed", "oversized", "cancelled"])
def test_public_command_bounds_and_cleanup_are_kept_on_every_result(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    from agent_ssh import gateway_native
    from ssh_manager import process, proxy

    events: list[tuple[Any, ...]] = []
    stderr_waiting, release = asyncio.Event(), asyncio.Event()
    if mode == "oversized":
        monkeypatch.setattr(gateway_native, "MAX_BYTES", 32)

    async def wait() -> int:
        if mode == "cancelled":
            stderr_waiting.set()
            await release.wait()
        return 2 if mode == "failed" else 0

    child: SimpleNamespace | None = None

    async def spawn(*args: Any, **kwargs: Any) -> SimpleNamespace:
        nonlocal child
        events.append(("spawn", args))
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
        stdout.feed_data(b"x" * 33 if mode == "oversized" else b'{"public":true}')
        stdout.feed_eof()
        stderr.feed_eof()
        child = SimpleNamespace(stdout=stdout, stderr=stderr, wait=wait)
        return child

    async def terminate(actual: SimpleNamespace) -> None:
        assert actual is child
        events.append(("cleanup",))
        release.set()

    monkeypatch.setattr(proxy, "create_ssh_subprocess", spawn)
    monkeypatch.setattr(process, "terminate_ssh_process_tree", terminate)
    observer = PublicObserver(SSHConfig(host_alias="selected"), ["/fixed gateway/owner"], {})

    async def exercise() -> None:
        operation = asyncio.create_task(observer._call(("export-directory",)))
        if mode == "cancelled":
            await asyncio.wait_for(stderr_waiting.wait(), 2)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        elif mode in ("failed", "oversized"):
            with pytest.raises(ValueError, match="command-unavailable|output-limit"):
                await operation
        else:
            assert await operation == b'{"public":true}'

    asyncio.run(exercise())
    assert events[0][0] == "spawn" and events[-1] == ("cleanup",)
    assert events[0][1][-1] == "'/fixed gateway/owner' export-directory"
