"""Observed retirement and old unowned receipts are not cleanup proofs."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_bridge.native_manager import NativeManager
from agent_bridge.native_resources import (
    DEFINITIONS_SCHEMA, EMPTY_SCHEMA, HostResources, ResourceMailbox,
)
from agent_bridge.native_store import NativeError


def setup_manager(tmp_path):
    manager = NativeManager(tmp_path / "controller", lambda: ["provider"])
    store = manager.store(create=True)
    definition = {"schema": DEFINITIONS_SCHEMA, "version": 1, "resources": {
        "preview": {"argv": [sys.executable, "provider.py"], "config": {}},
    }}
    store.reserve("execution", "generation", "launch", "example-space", str(tmp_path), "hash", {
        "spec": {"hostResources": definition}, "launchRequested": True,
        "sessionId": "session", "resourceDefinitions": {"preview": EMPTY_SCHEMA},
    })
    store.update("execution", "generation", state="ready", represented=True)
    return manager, store


class SettlementTransport:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.claimed = True
        self.forwarding = True

    async def request(self, method, params=None):
        self.calls.append(method)
        if method == "status":
            return {"state": "stopped", "retired": True, "represented": False, "sessionId": "session"}
        assert method == "stop"
        if self.failure == "stop":
            return {"state": "stopping", "retired": False}
        self.claimed = False
        return {"state": "stopped", "retired": True, "recovery": {"ok": False}}

    async def close(self):
        self.calls.append("close")
        if self.failure == "close":
            raise OSError("transport cleanup unconfirmed")
        self.forwarding = False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "stop", "close"])
async def test_observed_retirement_still_settles_provider_and_transport(tmp_path, failure):
    manager, store = setup_manager(tmp_path)
    transport = SettlementTransport(failure)
    manager.transports["execution"] = transport

    async def cleanup(*args):
        assert not transport.claimed and not transport.forwarding

    manager._host_resources = SimpleNamespace(cleanup=AsyncMock(side_effect=cleanup))
    observed = await manager.status("execution", "generation")
    assert observed["state"] == "stopping"
    assert store.get("execution")["data"]["retired"]
    assert transport.claimed and transport.forwarding
    if failure:
        with pytest.raises((NativeError, OSError)):
            await manager.stop("execution", "generation")
        assert store.get("execution")["state"] == "stopping"
        assert not store.get("execution")["data"].get("providerRetirementConfirmed")
        manager._host_resources.cleanup.assert_not_awaited()
        assert "execution" in manager.transports
        transport.failure = None
    stopped = await manager.stop("execution", "generation")
    assert stopped["state"] == "stopped"
    assert "stop" in transport.calls and transport.calls[-1] == "close"
    assert not transport.claimed and not transport.forwarding
    assert "execution" not in manager.transports
    assert store.get("execution")["data"]["providerRetirementConfirmed"]
    manager._host_resources.cleanup.assert_awaited_once()
    previous_calls = list(transport.calls)
    assert await manager.stop("execution", "generation") == stopped
    assert transport.calls == previous_calls


@pytest.mark.asyncio
async def test_observed_retirement_without_transport_reconnects_for_provider_settlement(tmp_path, monkeypatch):
    manager, store = setup_manager(tmp_path)
    manager._save_result(store, "execution", "generation", {
        "state": "stopped", "retired": True, "represented": False, "sessionId": "session",
    })
    transport = SettlementTransport()
    durable = AsyncMock(return_value=None)

    async def reconnect(execution, generation, *, retirement_only):
        assert retirement_only is True
        manager.transports[execution] = transport

    prepare = AsyncMock(side_effect=reconnect)
    monkeypatch.setattr(manager, "_retirement_receipt", durable)
    monkeypatch.setattr(manager, "_prepare", prepare)
    assert (await manager.stop("execution", "generation"))["state"] == "stopped"
    durable.assert_awaited_once()
    prepare.assert_awaited_once_with("execution", "generation", retirement_only=True)
    assert transport.calls == ["stop", "close"]
    assert not transport.claimed and not transport.forwarding


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lost", "invalid"])
async def test_new_ensure_forgets_old_unowned_proof_and_reconciles_journal(tmp_path, failure):
    manager, store = setup_manager(tmp_path)
    owned = tmp_path / "scope-owned"
    external = tmp_path / "unowned"
    external.write_text("preserve")
    ensure_count = 0
    release_count = 0
    confirm_release = False
    old_receipt = {"unownedIdentity": "external-service"}

    async def provider(definition, payload, owner):
        nonlocal ensure_count, release_count
        identity = {key: payload[key] for key in (
            "schema", "version", "executionId", "generation", "resource", "operationId",
        )}
        if payload["operation"] == "ensure":
            ensure_count += 1
            if ensure_count == 1:
                return {**identity, "ok": True, "owned": False, "value": {}, "receipt": old_receipt}
            assert payload["previous"] == old_receipt
            journal = Path(payload["stateDir"]) / "journal.json"
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(json.dumps({"operationId": payload["operationId"], "owned": True}))
            owned.write_text("created after the earlier unowned response")
            if failure == "lost":
                raise NativeError("resource_provider_unavailable", "ensure reply lost")
            return {**identity, "operationId": "wrong", "ok": True, "owned": True, "value": {}, "receipt": {}}
        release_count += 1
        assert payload["previous"] == old_receipt
        journal = json.loads((Path(payload["stateDir"]) / "journal.json").read_text())
        assert journal["operationId"] == payload["operationId"] and journal["owned"]
        if not confirm_release:
            raise NativeError("resource_cleanup_unconfirmed", "release not yet proved")
        owned.unlink(missing_ok=True)
        return {**identity, "ok": True, "released": True}

    resources = HostResources(store, lambda: True, invoke=provider)
    manager._host_resources = resources
    mailbox = ResourceMailbox(store)
    first = {"executionId": "execution", "generation": "generation", "sessionId": "session",
             "requestId": "first", "resource": "preview", "input": {}}
    mailbox.submit(first)
    result = await resources.ensure("execution", "generation", first)
    mailbox.complete("execution", "generation", result)
    known = store.get("execution")["data"]["_host_resource_preview"]
    assert known["owned"] is False
    mailbox.submit(first)
    assert mailbox.result("execution", "generation", "first") == result
    assert mailbox.pending("execution", "generation") == []
    assert store.get("execution")["data"]["_host_resource_preview"] == known
    assert ensure_count == 1
    with pytest.raises(NativeError):
        await resources.ensure("execution", "generation", {**first, "requestId": "second"})
    uncertain = store.get("execution")["data"]["_host_resource_preview"]
    assert uncertain["owned"] is None
    assert uncertain["receipt"] == old_receipt
    assert owned.exists() and external.exists()
    transport = SettlementTransport()
    manager.transports["execution"] = transport
    with pytest.raises(NativeError, match="not yet proved"):
        await manager.stop("execution", "generation")
    assert store.get("execution")["state"] == "stopping"
    assert owned.exists() and release_count == 1
    assert transport.calls == ["stop", "close"]
    confirm_release = True
    assert (await manager.stop("execution", "generation"))["state"] == "stopped"
    assert not owned.exists() and external.read_text() == "preserve"
    assert release_count == 2 and transport.calls == ["stop", "close"]
