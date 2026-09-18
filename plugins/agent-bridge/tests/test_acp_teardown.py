"""Strict ACP cleanup preserves failed ownership without changing default detach."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.acp_client import AcpClient

pytestmark = [pytest.mark.asyncio, pytest.mark.contract("agent_bridge.acp_teardown")]


@pytest.mark.parametrize("failure", ["connection", "host", "both"])
@pytest.mark.parametrize("strict", [False, True])
async def test_acp_shutdown_attempts_both_closers_and_retains_strict_failures(failure, strict):
    client = AcpClient()
    client._host_mode = True
    connection = SimpleNamespace(close=AsyncMock(
        side_effect=OSError("connection close failed") if failure in {"connection", "both"} else None,
    ))
    closer = AsyncMock(side_effect=OSError("host close failed") if failure in {"host", "both"} else None)
    client._connection = connection
    client._host_closer = closer
    if strict:
        with pytest.raises(RuntimeError, match="ACP shutdown incomplete"):
            await client.shutdown(strict=True)
        assert (client._connection is connection) is (failure in {"connection", "both"})
        assert (client._host_closer is closer) is (failure in {"host", "both"})
    else:
        await client.shutdown()
        assert client._connection is None
        assert client._host_closer is None
    connection.close.assert_awaited_once()
    closer.assert_awaited_once()
    connection.close.side_effect = None
    closer.side_effect = None
    await client.shutdown(strict=True)
    assert client._connection is None
    assert client._host_closer is None


async def test_strict_process_shutdown_requires_confirmed_exit(monkeypatch):
    client = AcpClient()
    process = SimpleNamespace(returncode=None)
    client._process = process
    terminate = AsyncMock()
    monkeypatch.setattr("agent_bridge.acp_client._terminate_process_tree", terminate)
    with pytest.raises(RuntimeError, match="ACP shutdown incomplete"):
        await client.shutdown(strict=True)
    assert client._process is process
    process.returncode = -15
    await client.shutdown(strict=True)
    assert client._process is None
