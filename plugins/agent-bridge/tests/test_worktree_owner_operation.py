"""An uncertain owner launch must not be killed or replaced."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge import worktree_owner_operation as operation
from agent_bridge.transport import SpawnTarget


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_timeout", [False, True])
async def test_owner_launch_timeout_preserves_capture_and_process(monkeypatch, legacy_timeout):
    finished = asyncio.Event()
    if legacy_timeout:
        class LegacyAsyncioTimeoutError(Exception):
            pass

        wait_for = asyncio.wait_for

        async def legacy_wait_for(awaitable, timeout):
            try:
                return await wait_for(awaitable, timeout)
            except TimeoutError as exc:
                raise LegacyAsyncioTimeoutError() from exc

        monkeypatch.setattr(operation.asyncio, "TimeoutError", LegacyAsyncioTimeoutError)
        monkeypatch.setattr(operation.asyncio, "wait_for", legacy_wait_for)

    async def communicate():
        await finished.wait()
        return b'{"ok": true}', b""

    proc = SimpleNamespace(communicate=communicate, returncode=None, kill=Mock())
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(operation.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(operation, "_agent_worktrees_launch_prefix", lambda: ["owner"])
    resolver = SimpleNamespace(resolve=lambda _: SpawnTarget(type="local"))
    with pytest.raises(operation.OwnerOperationUnconfirmed):
        await operation.run_owner_operation(
            "example", SimpleNamespace(project="example-project"), resolver,
            ["embody", "--worktree-id", "wt-example", "--json"], timeout=0.001,
        )
    assert operation._pending
    proc.kill.assert_not_called()
    finished.set()
    await asyncio.gather(*operation._pending)
    await asyncio.sleep(0)
    assert not operation._pending
    assert spawn.call_args.args[:3] == ("owner", "--project", "example-project")


@pytest.mark.asyncio
@pytest.mark.parametrize("shell", ["pwsh", "powershell", "cmd"])
async def test_windows_ssh_owner_arguments_preserve_literal_identity(monkeypatch, shell):
    proc = SimpleNamespace(
        communicate=AsyncMock(return_value=(b'{"ok": true}', b"")), returncode=0,
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(operation.asyncio, "create_subprocess_exec", spawn)
    resolver = SimpleNamespace(resolve=lambda _: SpawnTarget(
        type="ssh", host="example-host", user="legacy-user", ssh_shell=shell,
    ))
    result = await operation.run_owner_operation(
        "example", SimpleNamespace(project="example-project"), resolver,
        ["embody", "--worktree-id", "wt-'example", "--json"],
    )
    assert result == '{"ok": true}'
    remote = spawn.call_args.args[-1]
    assert remote.startswith(("pwsh" if shell == "pwsh" else "powershell") + " -NoProfile")
    script = base64.b64decode(remote.split("-EncodedCommand ", 1)[1]).decode("utf-16-le")
    assert "'wt-''example'" in script
    assert spawn.call_args.args[-2] == "example-host"
