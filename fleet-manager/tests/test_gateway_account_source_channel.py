"""Synthetic credentials through the real TLS/PoP/channel and source custodian."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fleet_manager.gateway_account_source import LEASE_CONTRACT
from gateway_service_transport_fixtures import eventually, running
from test_gateway_account_source import CAPABILITY, FIELDS, PIN, handler
from test_gateway_service_handler import connect_handler


def custodian(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(
        Path(__file__).resolve().parents[2] / "libs" / "credential-relay" / "src",
    ))
    from credential_relay.account_relay import AccountRelay
    from credential_relay.sources.gh_auth import GhAuthSource

    calls = []

    async def resolve(self, action, fields, *, timeout):
        calls.append((self._account, action, fields))
        return (
            "protocol=https\nhost=github.com\nusername=account-a\n"
            f"token=synthetic-source-token-{len(calls)}\n\n"
        )

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    capability = tmp_path / "capability"
    capability.write_text(CAPABILITY, encoding="ascii")
    document = {
        "schema": "credential-relay.account-policy", "version": 1,
        "providers": {"local": {"kind": "gh-auth", "account": "account-a", "host": "github.com"}},
        "grants": [{
            **FIELDS, "provider": "local", "capability_file": str(capability),
            "expires_at": time.time() + 3600, "source_lease_seconds": 60,
        }],
    }
    policy = tmp_path / "source-policy.json"
    policy.write_text(json.dumps(document), encoding="utf-8")
    return AccountRelay(str(policy)), calls, policy, document


def test_real_source_request_renewal_denial_loss_and_reconnect(tmp_path, monkeypatch, caplog):
    source, calls, policy, document = custodian(tmp_path, monkeypatch)

    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, handler(source).handle)
            async with await rig.request("/api/health", headers=PIN) as response:
                support = await response.json()
                assert response.status == 200 and calls == []
                assert support["source_credentials_contract"] == {
                    "lease_contract": LEASE_CONTRACT, "exchange": "http/v1",
                }
            value = {**FIELDS, "capability": CAPABILITY, "lease_contract": LEASE_CONTRACT}

            async def acquire(expected=200, **changes):
                async with await rig.request(
                    "/api/source-credentials", "POST", json={**value, **changes}, headers=PIN,
                ) as response:
                    assert response.status == expected
                    assert response.headers["Cache-Control"] == "no-store"
                    return await response.json()

            first = await acquire()
            second = await acquire()
            assert first["token"] == "synthetic-source-token-1"
            assert second["token"] == "synthetic-source-token-2"
            assert first["lease_id"] != second["lease_id"]
            assert 0 < second["lease_remaining_seconds"] <= 60
            assert second["installationId"] == support["installationId"]
            assert calls == [("account-a", "get-github-token", {"host": "github.com"})] * 2
            await acquire(503, capability="wrong-capability-but-valid-synthetic-size")
            await acquire(403, recipient="foreign")
            assert len(calls) == 2
            approved = document["grants"]
            document["grants"] = []
            policy.write_text(json.dumps(document), encoding="utf-8")
            await acquire(503)
            assert len(calls) == 2
            await rig.state.channels["service-a"].close()
            await eventually(lambda: "service-a" not in rig.state.channels)
            await acquire(503)
            assert len(calls) == 2
            document["grants"] = approved
            policy.write_text(json.dumps(document), encoding="utf-8")
            await connect_handler(rig, handler(source).handle)
            third = await acquire()
            assert third["token"] == "synthetic-source-token-3"
            assert len(calls) == 3
            assert rig.backends[0].requests == []

    asyncio.run(scenario())
    assert CAPABILITY not in caplog.text
    assert "synthetic-source-token-" not in caplog.text


def test_source_request_lost_after_dispatch_is_not_replayed_to_replacement(tmp_path):
    from test_gateway_account_source import Source

    async def scenario():
        async with running(tmp_path) as rig:
            source = Source()
            entered, cancelled = asyncio.Event(), asyncio.Event()

            async def resolve(fields, capability, *, source_lease):
                source.calls.append((fields, capability, source_lease))
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            source.resolve = resolve
            await connect_handler(rig, handler(source).handle)
            pending = asyncio.create_task(rig.request(
                "/api/source-credentials", "POST", json={**FIELDS, "capability": CAPABILITY},
                headers=PIN,
            ))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                replacement = Source()
                await connect_handler(rig, handler(replacement).handle)
                await asyncio.wait_for(cancelled.wait(), 3)
                async with await pending as response:
                    assert response.status == 502
                    assert await response.json() == {"error": "outcome-unknown"}
                assert len(source.calls) == 1 and replacement.calls == []
                async with await rig.request(
                    "/api/source-credentials", "POST", json={**FIELDS, "capability": CAPABILITY},
                    headers=PIN,
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["token"] == "synthetic-token"
                assert len(replacement.calls) == 1
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_installation_replacement_between_health_and_post_never_acquires(tmp_path):
    from test_gateway_account_source import Source

    async def scenario():
        async with running(tmp_path) as rig:
            first, replacement = Source(), Source()
            await connect_handler(rig, handler(first).handle)
            async with await rig.request("/api/health", headers=PIN) as response:
                assert response.status == 200
                assert (await response.json())["installationId"] == PIN["X-Agent-Index-Installation-Id"]
            await connect_handler(rig, handler(replacement, installation="replacement").handle)
            for pin in ({}, PIN):
                async with await rig.request(
                    "/api/source-credentials", "POST",
                    json={**FIELDS, "capability": CAPABILITY}, headers=pin,
                ) as response:
                    assert response.status == 409
                    assert await response.json() == {"error": "source-installation-mismatch"}
            assert first.calls == replacement.calls == []
            async with await rig.request(
                "/api/source-credentials", "POST", json={**FIELDS, "capability": CAPABILITY},
                headers={"X-Agent-Index-Installation-Id": "replacement"},
            ) as response:
                assert response.status == 200
            assert len(replacement.calls) == 1

    asyncio.run(scenario())
