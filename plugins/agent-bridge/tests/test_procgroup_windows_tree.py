"""Regression tests for the shared Windows graceful-then-forceful tree-kill
(#4031): a forceful `taskkill /T /F` alone vanishes the local process
instantly but gives no chance for a well-behaved child (e.g. ssh.exe) to tear
its own remote session down, so the remote end notices only via its own
keepalive timeout. These tests use mocks throughout and do not depend on the
host's actual platform.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.procgroup import terminate_windows_tree


def _fake_proc(pid: int = 4242) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.stdin = MagicMock()
    proc.stdin.is_closing.return_value = False
    return proc


@pytest.mark.asyncio
async def test_graceful_exit_skips_taskkill():
    proc = _fake_proc()
    proc.wait = AsyncMock(return_value=0)

    with patch("agent_bridge.procgroup.asyncio.create_subprocess_exec") as mock_exec:
        await terminate_windows_tree(proc)

    proc.stdin.close.assert_called_once()
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_forceful_taskkill_when_graceful_exit_times_out():
    proc = _fake_proc()
    proc.wait = AsyncMock(side_effect=asyncio.TimeoutError)

    killer = MagicMock()
    killer.wait = AsyncMock(return_value=0)

    with patch(
        "agent_bridge.procgroup.asyncio.create_subprocess_exec",
        AsyncMock(return_value=killer),
    ) as mock_exec:
        await terminate_windows_tree(proc)

    proc.stdin.close.assert_called_once()
    mock_exec.assert_called_once()
    args = mock_exec.call_args[0]
    assert args[0] == "taskkill"
    assert "4242" in args


@pytest.mark.asyncio
async def test_no_stdin_skips_close_but_still_waits():
    proc = _fake_proc()
    proc.stdin = None
    proc.wait = AsyncMock(return_value=0)

    with patch("agent_bridge.procgroup.asyncio.create_subprocess_exec") as mock_exec:
        await terminate_windows_tree(proc)

    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_stdin_close_exception_does_not_block_kill():
    proc = _fake_proc()
    proc.stdin.close.side_effect = RuntimeError("already closed")
    proc.wait = AsyncMock(return_value=0)

    with patch("agent_bridge.procgroup.asyncio.create_subprocess_exec") as mock_exec:
        await terminate_windows_tree(proc)

    mock_exec.assert_not_called()
