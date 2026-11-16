"""Synthetic credentials through the real TLS/PoP/channel and source custodian."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

import pytest
from aiohttp import ClientResponse

from fleet_manager.gateway_account_source import (
    LEASE_CONTRACT, AccountSource, AccountSourceGrant, AccountSourceHandler,
)
from fleet_manager.gateway_controller_services import ControllerService
from fleet_manager.gateway_local_handler import LocalHandlerBinding
from fleet_manager.gateway_manifest import ServiceSlot
from fleet_manager.gateway_service_handler import ServiceRequest, ServiceResponse
from fleet_manager.gateway_vault_available import NORMAL_FETCH_CONTRACT
from fleet_manager.gateway_vault_fetch import AccountFetchGrant, AccountSourcePin, GatewayAccountFetch
from gateway_service_transport_fixtures import eventually as _eventually, running as _running
from test_gateway_account_source import CAPABILITY, FIELDS, PIN, handler
from test_gateway_service_handler import connect_handler as _connect_handler
from test_gateway_controller_services import connect, gateway_owner

if TYPE_CHECKING:
    from credential_relay.sources.gh_auth import GhAuthSource

NORMAL_SERVICE = "vault-normal-fetch-v1"

# These shared transport helpers expose dynamically configured test rigs.
eventually: Callable[[Callable[[], bool]], Awaitable[None]] = _eventually
running: Callable[[Path], AbstractAsyncContextManager[Any]] = _running
connect_handler: Callable[
    [Any, Callable[[ServiceRequest], Awaitable[ServiceResponse]]], Awaitable[Any],
] = _connect_handler


def fetch_owner(slot: ServiceSlot, *, budget: float = 30) -> GatewayAccountFetch:
    return GatewayAccountFetch(slot=slot, service=NORMAL_SERVICE, budget=budget, grants=(
        AccountFetchGrant(
            "caller-a", "binding-a", "account-a", "worker-a", "github.com",
            (AccountSourcePin("service-a", "repo-a", "source-installation"),
             AccountSourcePin("service-b", "repo-a", "second-installation")),
        ),
    ))


@asynccontextmanager
async def source_running(root: Path, profile: str) -> AsyncIterator[Any]:
    if profile == "jwt-pop-v1":
        async with running(root) as rig:
            yield rig
        return
    from test_gateway_enrolled_identity import EnrolledRig, RS256Harness

    enrolled_rig: Any = EnrolledRig
    rig = enrolled_rig(root, RS256Harness)
    try:
        await rig.start(identity_provider=rig.h.provider)
        yield rig
    finally:
        await rig.close()


def custodian(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[AccountSource, list[tuple[str | None, str, dict[str, str]]], Path, dict[str, Any]]:
    monkeypatch.syspath_prepend(str(
        Path(__file__).resolve().parents[2] / "libs" / "credential-relay" / "src",
    ))
    from credential_relay.account_relay import AccountRelay
    from credential_relay.sources.gh_auth import GhAuthSource

    calls: list[tuple[str | None, str, dict[str, str]]] = []

    async def resolve(
        self: GhAuthSource, action: str, fields: dict[str, str], *, timeout: float,
    ) -> str:
        calls.append((self._account, action, fields))
        return (
            "protocol=https\nhost=github.com\nusername=account-a\n"
            f"token=synthetic-source-token-{len(calls)}\n\n"
        )

    monkeypatch.setattr(GhAuthSource, "resolve", resolve)
    capability = tmp_path / "capability"
    capability.write_text(CAPABILITY, encoding="ascii")
    document: dict[str, Any] = {
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


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-rs256-pop-v1"])
def test_real_source_request_renewal_denial_loss_and_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, profile: str,
) -> None:
    source, calls, policy, document = custodian(tmp_path, monkeypatch)

    async def scenario() -> None:
        async with source_running(tmp_path, profile) as rig:
            await connect_handler(rig, handler(source).handle)
            async with await rig.request("/api/health", headers=PIN) as response:
                support = await response.json()
                assert response.status == 200 and calls == []
                assert support["source_credentials_contract"] == {
                    "lease_contract": LEASE_CONTRACT, "exchange": "http/v1",
                }
            value = {**FIELDS, "capability": CAPABILITY, "lease_contract": LEASE_CONTRACT}

            async def acquire(expected: int = 200, **changes: Any) -> dict[str, Any]:
                async with await rig.request(
                    "/api/source-credentials", "POST", json={**value, **changes}, headers=PIN,
                ) as response:
                    assert response.status == expected
                    assert response.headers["Cache-Control"] == "no-store"
                    result: dict[str, Any] = await response.json()
                    return result

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


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-rs256-pop-v1"])
def test_source_request_lost_after_dispatch_is_not_replayed_to_replacement(
    tmp_path: Path, profile: str,
) -> None:
    from test_gateway_account_source import Source

    async def scenario() -> None:
        async with source_running(tmp_path, profile) as rig:
            source = Source()
            entered, cancelled = asyncio.Event(), asyncio.Event()

            async def resolve(
                fields: dict[str, str], capability: str, *, source_lease: bool,
            ) -> None:
                source.calls.append((fields, capability, source_lease))
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            setattr(source, "resolve", resolve)
            await connect_handler(rig, handler(source).handle)
            pending: asyncio.Task[ClientResponse] = asyncio.create_task(rig.request(
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


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-rs256-pop-v1"])
def test_installation_replacement_between_health_and_post_never_acquires(
    tmp_path: Path, profile: str,
) -> None:
    from test_gateway_account_source import Source

    async def scenario() -> None:
        async with source_running(tmp_path, profile) as rig:
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


def test_gateway_normal_fetch_uses_current_channels_custodian_and_success_affinity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from test_gateway_account_source import Source

    service = NORMAL_SERVICE
    actual, calls, policy, document = custodian(tmp_path, monkeypatch)
    first = Source()
    first.result = None
    owner: GatewayAccountFetch | None = None
    order: list[str] = []
    received: list[ServiceRequest] = []

    def binding_factory(slot: ServiceSlot) -> LocalHandlerBinding:
        nonlocal owner
        owner = fetch_owner(slot)
        return owner.binding()

    def remote(
        source: AccountSource, slot: str, installation: str,
    ) -> Callable[[ServiceRequest], Awaitable[ServiceResponse]]:
        selected = AccountSourceHandler(
            source, fleet="fleet-a", gateway="gateway-a", slot=slot,
            installation=installation, grants=(AccountSourceGrant("caller-a", **FIELDS),))

        async def handle(request: ServiceRequest) -> ServiceResponse:
            order.append(slot)
            received.append(request)
            return await selected.handle(request)

        return handle

    async def scenario() -> None:
        async with gateway_owner(
            tmp_path, lambda request: None, binding_factory=binding_factory,
        ) as rig:
            try:
                one = await connect(rig, offers=(ControllerService(service, "/api"),),
                                    handle=remote(first, "service-a", "source-installation"))
                two = await connect(rig, 1, offers=(ControllerService(service, "/assets"),),
                                    handle=remote(actual, "service-b", "second-installation"))
                value = {
                    "fetch_contract": NORMAL_FETCH_CONTRACT, "binding": "binding-a",
                    "sources": {"service-a": CAPABILITY, "service-b": CAPABILITY},
                }

                async def fetch(status: int = 200, **changes: Any) -> dict[str, Any]:
                    async with await rig.request(
                        "/vault/token-fetch", "POST", json={**value, **changes},
                    ) as response:
                        assert response.status == status
                        assert response.headers["Cache-Control"] == "no-store"
                        result: dict[str, Any] = await response.json()
                        return result

                result = await fetch()
                assert result["token"] == "synthetic-source-token-1"
                assert result["state"] == "fetched" and result["acquisition"] == "normal-fetch"
                assert result["token_expires_at"] is None
                assert order == ["service-a", "service-b"]
                order.clear()
                await fetch()
                assert order[0] == "service-b"
                assert len(calls) == 2
                assert all(request.context.principal == "caller-a" for request in received)
                assert all(request.origin is not None and request.origin.slot == "vault"
                           for request in received)
                assert all(request.origin is not None
                           and request.origin.target_uri == rig.origin + "/vault/token-fetch"
                           for request in received)
                assert all(not ({"authorization", "dpop"} & dict(request.headers).keys())
                           for request in received)
                await fetch(403, binding="foreign")
                await fetch(403, sources={"foreign": CAPABILITY})
                assert len(calls) == 2
                document["grants"] = []
                policy.write_text(json.dumps(document), encoding="utf-8")
                result = await fetch(503)
                assert result["state"] == "unavailable" and "token" not in result
                assert len(calls) == 2
                await one.advertise(())
                await two.advertise(())
                assert (await fetch(503))["state"] == "no-source"
                assert rig.state.pending == 0
                assert rig.backends[0].requests == rig.backends[1].requests == []
            finally:
                if owner is not None:
                    owner.stop()
                    assert await owner.drain(timeout=3)
                    assert owner.pending_count == 0

    asyncio.run(scenario())
    assert CAPABILITY not in caplog.text and "synthetic-source-token-" not in caplog.text


@pytest.mark.parametrize("stop", ["replacement", "withdrawal", "timeout", "caller"])
def test_gateway_normal_fetch_unknown_work_is_not_replayed_to_a_new_source(
    tmp_path: Path, stop: str,
) -> None:
    from test_gateway_account_source import Source

    owner: GatewayAccountFetch | None = None

    def binding_factory(slot: ServiceSlot) -> LocalHandlerBinding:
        nonlocal owner
        owner = fetch_owner(slot, budget=0.1 if stop == "timeout" else 3)
        return owner.binding()

    async def scenario() -> None:
        async with gateway_owner(
            tmp_path, lambda request: None, binding_factory=binding_factory,
        ) as rig:
            entered, cancelled = asyncio.Event(), asyncio.Event()
            source, replacement = Source(), Source()

            async def resolve(
                fields: dict[str, str], capability: str, *, source_lease: bool,
            ) -> None:
                source.calls.append((fields, capability, source_lease))
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            setattr(source, "resolve", resolve)
            offered = (ControllerService(NORMAL_SERVICE, "/api"),)
            connector = await connect(rig, offers=offered, handle=handler(source).handle)
            pending: asyncio.Task[ClientResponse] = asyncio.create_task(rig.request(
                "/vault/token-fetch", "POST", json={
                "fetch_contract": NORMAL_FETCH_CONTRACT, "binding": "binding-a",
                "sources": {"service-a": CAPABILITY},
            }))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if stop == "replacement":
                    await connect(rig, offers=offered, handle=handler(replacement).handle)
                elif stop == "withdrawal":
                    await connector.advertise(())
                elif stop == "caller":
                    rig.h.document["callers"] = []
                    await rig.update()
                async with await pending as response:
                    assert response.status != 200
                    assert b"synthetic-token" not in await response.read()
                await asyncio.wait_for(cancelled.wait(), 3)
                assert source.calls == [(FIELDS, CAPABILITY, False)]
                assert not replacement.calls
                assert owner is not None
                assert await owner.drain(timeout=3)
                assert owner.pending_count == rig.state.pending == 0
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                assert owner is not None
                owner.stop()
                assert await owner.drain(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["legacy", "installationId", "account", "token_expires_at"])
def test_gateway_normal_fetch_refuses_old_or_misbound_reply_without_fallback(
    tmp_path: Path, change: str,
) -> None:
    owner: GatewayAccountFetch | None = None

    def binding_factory(slot: ServiceSlot) -> LocalHandlerBinding:
        nonlocal owner
        owner = fetch_owner(slot)
        return owner.binding()

    async def scenario() -> None:
        received: list[ServiceRequest] = []

        async def remote(request: ServiceRequest) -> ServiceResponse:
            received.append(request)
            if change == "legacy":
                return ServiceResponse(404)
            value = {
                **FIELDS, "installationId": "source-installation",
                "fetch_contract": NORMAL_FETCH_CONTRACT, "acquisition": "normal-fetch",
                "token": "synthetic-token", "token_expires_at": None,
                change: 123 if change == "token_expires_at" else "foreign",
            }
            return ServiceResponse(200, body=json.dumps(value).encode())

        async with gateway_owner(
            tmp_path, lambda request: None, binding_factory=binding_factory,
        ) as rig:
            try:
                await connect(rig, offers=(ControllerService(NORMAL_SERVICE, "/api"),), handle=remote)
                async with await rig.request("/vault/token-fetch", "POST", json={
                    "fetch_contract": NORMAL_FETCH_CONTRACT, "binding": "binding-a",
                    "sources": {"service-a": CAPABILITY},
                }) as response:
                    assert response.status == 503
                    assert b"synthetic-token" not in await response.read()
                assert [request.target for request in received] == ["/token-fetch"]
                assert owner is not None
                assert owner.pending_count == rig.state.pending == 0
            finally:
                assert owner is not None
                owner.stop()
                assert await owner.drain(timeout=3)

    asyncio.run(scenario())
