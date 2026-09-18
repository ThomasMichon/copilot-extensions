"""Portable provider selection, pinned ownership, and wait-budget contracts."""

from unittest.mock import patch
import json
from pathlib import Path

import pytest

from agent_bridge.native_manager import NativeManager, ProviderTransport
from agent_bridge.native_store import NativeError


@pytest.mark.guard
@pytest.mark.asyncio
@pytest.mark.parametrize("provider_available", [False, True])
async def test_remote_command_capability_is_explicit_before_allocation(tmp_path, capsys, provider_available):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from agent_bridge.native_runtime import command
    from agent_bridge.routes import native

    app = FastAPI()
    root = tmp_path / "native-state"
    app.state.native_manager = NativeManager(
        root, lambda namespace="codespace": ["/provider", namespace] if provider_available else None,
    )
    app.include_router(native.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.get("/api/v1/native-executions/capabilities")
    assert response.status_code == 200
    value = response.json()
    expected = {"schema": "copilot-extensions.remote-command", "version": 1, "receiptHash": "sha256"}
    assert value["capabilities"].get("remoteCommand") == expected
    assert value["providerAvailable"] is provider_available
    assert not root.exists(), "capability preflight must not allocate native ownership"
    assert command(SimpleNamespace(native_host_action="capabilities")) == 0
    assert json.loads(capsys.readouterr().out)["capabilities"]["remoteCommand"] == expected


@pytest.mark.guard
def test_native_public_capabilities_and_venue_receipt_match_captured_contract():
    from agent_bridge.native_capabilities import capabilities
    from agent_bridge.native_store import receipt

    corpus = Path(__file__).parents[1] / "contract" / "fixtures" / "http" / "current"
    assert capabilities() == json.loads((corpus / "native-capabilities.json").read_text())["capabilities"]
    request = json.loads((corpus / "native-launch.json").read_text())["request"]
    expected = json.loads((corpus / "native-receipt.json").read_text())["response"]
    assert receipt({
        "id": expected["executionId"], "generation": expected["generation"],
        "codespace": expected["target"], "owner": expected["owner"], "state": "ready",
        "data": {"spec": request, "sessionId": "session-example", "represented": True},
    }) == expected


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


@pytest.mark.asyncio
async def test_native_provider_reply_timeout_retains_delivery_uncertainty():
    import asyncio
    from types import SimpleNamespace

    frames = []

    class Pipe:
        def write(self, raw):
            frames.append(json.loads(raw))

        async def drain(self):
            pass

    transport = ProviderTransport([], {"id": "execution", "generation": "generation"}, lambda: None)
    transport.process = SimpleNamespace(stdin=Pipe())
    real_wait_for = asyncio.wait_for

    async def expire(future, timeout):
        return await real_wait_for(future, timeout=0)

    with patch("agent_bridge.native_manager.asyncio.wait_for", expire):
        with pytest.raises(NativeError) as failure:
            await transport.request("message", {"messageId": "same-message", "wait": True, "waitTimeout": 120})
    assert failure.value.code == "reply_transport_timeout" and failure.value.status == 504
    assert isinstance(failure.value.__cause__, asyncio.TimeoutError)
    assert "delivery remains uncertain" in failure.value.detail
    assert frames[0]["params"]["messageId"] == "same-message"
    assert not transport.pending
