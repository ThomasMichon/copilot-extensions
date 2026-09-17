"""Portable provider selection, pinned ownership, and wait-budget contracts."""

from unittest.mock import patch

import pytest

from agent_bridge.native_manager import NativeManager, ProviderTransport
from agent_bridge.native_store import NativeError


def descriptor():
    return {
        "schema": "copilot-extensions.remote-command", "version": 1,
        "argv": ["/installed payload/bin/agent-bridge"],
        "receipt": {"path": "/installed payload/current.json", "sha256": "a" * 64},
    }


@pytest.mark.asyncio
async def test_native_provider_identity_and_record_compatibility(tmp_path):
    manager = NativeManager(tmp_path / "state", lambda namespace="codespace": [f"/provider/{namespace}"])
    manager._schedule = lambda *a, **k: None
    request = {
        "requestId": "one", "target": "container:same-name", "owner": str(tmp_path),
        "cwd": "/workspace", "command": "copilot", "remoteCommand": descriptor(),
    }
    result = await manager.start(request)
    assert result["provider"] == "container" and result["target"] == "container:same-name"
    assert "codespace" not in result and result["remoteCommand"] == descriptor()
    assert (await manager.start(request))["executionId"] == result["executionId"]
    with pytest.raises(NativeError, match="blocks ACP"):
        manager.assert_acp_allowed("container:same-name")
    manager.assert_acp_allowed("codespace:same-name")
    legacy = {**request, "requestId": "legacy", "codespace": "same-name"}
    del legacy["target"]
    del legacy["remoteCommand"]
    old = await manager.start(legacy)
    assert old["codespace"] == "same-name"
    with pytest.raises(NativeError):
        manager.assert_acp_allowed("same-name")
    row = manager.store().get(result["executionId"])
    with pytest.raises(NativeError, match="different launch"):
        await manager.start({**request, "remoteCommand": {**descriptor(), "argv": ["/other/bin/agent-bridge"]}})
    manager.provider_command = lambda *a: pytest.fail("saved provider identity was discarded")
    assert manager._row_provider(row) == ["/provider/container"]


@pytest.mark.parametrize("wait", [150, 170, 240, 300])
@pytest.mark.asyncio
async def test_requested_wait_budget_survives_the_provider_boundary(wait):
    class Pipe:
        def write(self, raw):
            pass

        async def drain(self):
            pass

    from types import SimpleNamespace
    transport = ProviderTransport([], {"id": "id", "generation": "gen"}, lambda: None)
    transport.process = SimpleNamespace(stdin=Pipe())
    observed = []

    async def boundary(future, timeout):
        observed.append(timeout)
        future.set_result({"admitted": True, "replied": False})
        return await future

    with patch("agent_bridge.native_manager.asyncio.wait_for", boundary):
        result = await transport.request("message", {"wait": True, "waitTimeout": wait})
    assert observed == [wait + 60]
    assert result == {"admitted": True, "replied": False}


@pytest.mark.parametrize("wait", [0, -1, 301, float("nan"), float("inf")])
@pytest.mark.asyncio
async def test_invalid_wait_is_rejected_before_delivery(wait):
    transport = ProviderTransport([], {"id": "id", "generation": "gen"}, lambda: None)
    with pytest.raises(NativeError):
        await transport.request("message", {"wait": True, "waitTimeout": wait})
    assert not transport.pending
