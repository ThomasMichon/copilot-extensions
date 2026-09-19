"""Remote retirement proof survives uncertain local transport settlement."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.native_manager import NativeManager
from agent_bridge.native_store import NativeError
from agent_bridge.routes.native import router


def setup(tmp_path, monkeypatch):
    manager = NativeManager(tmp_path / "controller", lambda: pytest.fail("unattributed provider lookup"))
    store = manager.store(create=True)
    store.reserve("execution", "generation", "request", "example-space", str(tmp_path), "hash", {
        "spec": {"codespace": "example-space", "localForward": [], "reverseForward": []},
        "launchRequested": True,
    })
    store.update("execution", "generation", state="ready")
    monkeypatch.setattr(manager, "_prepare", AsyncMock(side_effect=AssertionError("unexpected transport creation")))
    proof = {
        "executionId": "execution", "generation": "generation", "owner": str(tmp_path),
        "codespace": "example-space", "retired": True, "exitCode": -9,
        "sessionId": None, "recovery": {"ok": False, "detail": "optional export not configured"},
    }
    return manager, store, proof


@pytest.mark.asyncio
async def test_successful_remote_stop_persists_proof_before_close_timeout_and_retry(tmp_path, monkeypatch):
    manager, store, proof = setup(tmp_path, monkeypatch)
    fail_close = True
    calls = []
    observed_proofs = []

    class Transport:
        async def request(self, method, params=None):
            calls.append(method)
            assert calls.count("stop") == 1, "confirmed retirement must not be sent again"
            return proof

        async def close(self):
            calls.append("close")
            observed_proofs.append(store.get("execution")["data"].get("providerRetirementProof"))
            manager._save_result(store, "execution", "generation", {
                "state": "ready", "represented": True, "retired": False, "exitCode": None,
            })
            if fail_close:
                raise asyncio.TimeoutError("process wait timed out after successful remote stop")

    manager.transports["execution"] = Transport()
    monkeypatch.setattr(manager, "_retirement_receipt", AsyncMock(side_effect=AssertionError("proof was persisted")))
    app = FastAPI()
    app.state.native_manager = manager
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://fixture") as client:
        first = await client.post("/api/v1/native-executions/execution/stop", json={"generation": "generation"})
        assert first.status_code == 503
        assert first.json()["detail"]["code"] == "retirement_unconfirmed"
        row = store.get("execution")
        assert row["state"] == "stopping" and row["data"]["transportCleanupPending"]
        assert not row["data"].get("providerRetirementConfirmed")
        with pytest.raises(NativeError, match="blocks ACP"):
            manager.assert_acp_allowed("example-space")
        fail_close = False
        second = await client.post("/api/v1/native-executions/execution/stop", json={"generation": "generation"})
    assert second.status_code == 200 and second.json()["state"] == "stopped"
    assert second.json()["exitCode"] == -9 and second.json()["recovery"] == proof["recovery"]
    assert calls == ["stop", "close", "close"]
    assert observed_proofs == [proof, proof]
    assert "execution" not in manager.transports
    assert not store.get("execution")["data"]["transportCleanupPending"]
    assert await manager.stop("execution", "generation") == second.json()
    assert calls == ["stop", "close", "close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["matching", "missing", "generation", "owner", "venue", "identity-missing", "malformed"])
async def test_dead_pipe_reads_real_local_identity_bound_receipt(tmp_path, monkeypatch, kind):
    manager, store, proof = setup(tmp_path, monkeypatch)
    if kind in {"generation", "owner"}:
        proof[kind] = "wrong"
    if kind == "venue":
        proof["codespace"] = "another-space"
    if kind == "identity-missing":
        proof.pop("generation")
    if kind == "malformed":
        proof = []
    provider = tmp_path / "receipt.py"
    provider.write_text(
        "import json,sys\n"
        "assert sys.argv[1:3]==['native-retirement','example-space']\n"
        "assert sys.argv[-4:]==['--execution-id','execution','--generation','generation']\n"
        f"print({json.dumps({'receipt': None if kind == 'missing' else proof})!r})\n",
        encoding="utf-8",
    )
    store.update("execution", "generation", providerCommand=[sys.executable, str(provider)])
    calls = []

    class Transport:
        async def request(self, method, params=None):
            calls.append(method)
            raise ConnectionResetError("Connection lost")

        async def close(self):
            calls.append("close")

    manager.transports["execution"] = Transport()
    if kind == "matching":
        assert (await manager.stop("execution", "generation"))["state"] == "stopped"
        assert store.get("execution")["data"]["providerRetirementProof"] == proof
    else:
        with pytest.raises(NativeError) as failure:
            await manager.stop("execution", "generation")
        assert failure.value.code in {"retirement_unconfirmed", "identity_mismatch"}
        assert store.get("execution")["state"] == "stopping"
        assert not store.get("execution")["data"].get("providerRetirementConfirmed")
        with pytest.raises(NativeError, match="blocks ACP"):
            manager.assert_acp_allowed("example-space")
    assert calls == ["stop", "close"]
    assert "execution" not in manager.transports


@pytest.mark.asyncio
async def test_uncertain_cleanup_keeps_ownership_and_does_not_retry_dead_pipe(tmp_path, monkeypatch):
    manager, store, proof = setup(tmp_path, monkeypatch)
    cleanup_possible = False
    calls = []

    class Transport:
        async def request(self, method, params=None):
            calls.append(method)
            if calls.count("stop") > 1:
                pytest.fail("retirement retry wrote to the dead pipe")
            raise ConnectionResetError("Connection lost")

        async def close(self):
            calls.append("close")
            if not cleanup_possible:
                raise OSError("owned process exit cannot be confirmed")

    manager.transports["execution"] = Transport()
    monkeypatch.setattr(manager, "_retirement_receipt", AsyncMock(return_value=proof))
    for _ in range(2):
        with pytest.raises(NativeError, match="transport"):
            await manager.stop("execution", "generation")
        assert store.get("execution")["state"] == "stopping"
        assert store.get("execution")["data"]["providerRetirementProof"] == proof
        assert not store.get("execution")["data"].get("providerRetirementConfirmed")
        assert "execution" in manager.transports
    cleanup_possible = True
    assert (await manager.stop("execution", "generation"))["state"] == "stopped"
    assert calls.count("stop") == 1


@pytest.mark.asyncio
async def test_proof_cannot_hide_unconfirmed_local_cleanup_after_owner_loss(tmp_path, monkeypatch):
    manager, store, proof = setup(tmp_path, monkeypatch)
    store.update("execution", "generation", state="stopping", providerRetirementProof=proof, transportCleanupPending=True)
    monkeypatch.setattr(manager, "_retirement_receipt", AsyncMock(return_value=proof))
    with pytest.raises(NativeError, match="transport"):
        await manager.stop("execution", "generation")
    assert store.get("execution")["state"] == "stopping"
    assert not store.get("execution")["data"].get("providerRetirementConfirmed")


@pytest.mark.asyncio
async def test_resource_cleanup_follows_transport_settlement_and_retains_retry(tmp_path, monkeypatch):
    manager, store, proof = setup(tmp_path, monkeypatch)
    row = store.get("execution")
    store.update("execution", "generation", spec={**row["data"]["spec"], "hostResources": {
        "schema": "copilot-extensions.native-host-resources", "version": 1,
        "resources": {"preview": {"argv": [sys.executable], "config": {}}},
    }})
    transport = SimpleNamespace(request=AsyncMock(return_value=proof), close=AsyncMock())
    manager.transports["execution"] = transport

    async def cleanup(*args):
        assert "execution" not in manager.transports
        assert not store.get("execution")["data"]["transportCleanupPending"]
        assert store.get("execution")["data"]["providerRetirementConfirmed"]
        raise NativeError("resource_cleanup_unconfirmed", "resource release not confirmed", 503)

    manager._host_resources = SimpleNamespace(cleanup=AsyncMock(side_effect=cleanup))
    with pytest.raises(NativeError, match="resource release"):
        await manager.stop("execution", "generation")
    assert store.get("execution")["state"] == "stopping"
    manager._host_resources.cleanup.side_effect = None
    assert (await manager.stop("execution", "generation"))["state"] == "stopped"
    transport.request.assert_awaited_once_with("stop")


@pytest.mark.asyncio
async def test_concurrent_stops_share_one_settlement(tmp_path, monkeypatch):
    manager, _, proof = setup(tmp_path, monkeypatch)

    async def remote_stop(*args):
        await asyncio.sleep(.01)
        return proof

    transport = SimpleNamespace(request=AsyncMock(side_effect=remote_stop), close=AsyncMock())
    manager.transports["execution"] = transport
    first, second = await asyncio.gather(
        manager.stop("execution", "generation"), manager.stop("execution", "generation"),
    )
    assert first == second and first["state"] == "stopped"
    transport.request.assert_awaited_once_with("stop")
    transport.close.assert_awaited_once()
