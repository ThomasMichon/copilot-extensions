"""Regression test for ssh-manager stdio-channel process-group isolation (#1001).

``open_stdio_channel`` spawns the ``ssh`` client that carries a remote ACP
session. It must start the child in its own session/process group
(``start_new_session=True`` on POSIX) so the bridge's teardown
(``os.killpg(os.getpgid(pid), ...)``) signals only the ssh tree -- never the
bridge's own process group. Without it, stopping a remote session SIGTERMs the
whole daemon.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ssh_manager.manager import ConnectionManager


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group behavior")
async def test_open_stdio_channel_starts_new_session():
    """The ssh stdio child must lead its own process group on POSIX."""
    mgr = ConnectionManager.__new__(ConnectionManager)
    # Minimal state open_stdio_channel touches: a registered connection whose
    # config exposes an ssh_target. Bypass real SSH arg construction.
    mgr._connections = {  # type: ignore[attr-defined]
        "host": SimpleNamespace(config=SimpleNamespace(ssh_target="user@host")),
    }
    object.__setattr__(mgr, "_mux_ssh_args", lambda info: ["ssh"])

    fake_proc = SimpleNamespace(pid=4321)
    with patch(
        "ssh_manager.manager.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as spawn:
        proc = await mgr.open_stdio_channel("host", "echo hi")

    assert proc is fake_proc
    spawn.assert_awaited_once()
    assert spawn.await_args.kwargs.get("start_new_session") is True
