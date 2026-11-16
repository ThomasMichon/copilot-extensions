"""Gateway lease negotiation and native-consumer binding, without live auth."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

import httpx
import pytest
from credential_relay.http_auth import HttpAuthContext

from agent_bridge.source_lease_http import (
    GatewaySourceLeaseClient, LEASE_CONTRACT, NORMAL_FETCH_CONTRACT,
    NORMAL_LEASE_SUPPORT, SourceLeaseError,
)


class Auth:
    async def headers(self, context: HttpAuthContext) -> dict[str, str]:
        return {"X-Synthetic-Auth": context.identity}

    async def invalidate(self, context: HttpAuthContext) -> None:
        raise AssertionError("Unexpected authentication retry")


def client(
    root: Path, handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[GatewaySourceLeaseClient, Path]:
    capability = root / "capability"
    capability.write_text("a" * 64, encoding="ascii")
    context = HttpAuthContext("https://example.com", "fixture", "generation")
    return GatewaySourceLeaseClient(
        "https://example.com/vault/token-fetch", auth=Auth(), context=context,
        discovery_endpoint="https://example.com/vault/health",
        discovery_auth=Auth(), discovery_context=context, installation_id="gateway-a",
        binding="binding-a", account="account-a", host="github.com",
        capability_files={"controller-a": capability},
        transport=httpx.MockTransport(handler),
    ), capability


def result(**changes: Any) -> dict[str, Any]:
    now = time.time()
    return {
        "fetch_contract": NORMAL_FETCH_CONTRACT, "state": "fetched",
        "acquisition": "normal-fetch", "binding": "binding-a",
        "account": "account-a", "host": "github.com", "installationId": "gateway-a",
        "token": "synthetic-token", "token_expires_at": None,
        "lease_contract": LEASE_CONTRACT, "lease_id": "a" * 32,
        "lease_issued_at": now - 1, "lease_expires_at": now + 59,
        "lease_remaining_seconds": 55, **changes,
    }


def health(**changes: Any) -> dict[str, Any]:
    return {
        "normal_source_lease_contract": NORMAL_LEASE_SUPPORT,
        "installationId": "gateway-a", **changes,
    }


def test_gateway_capabilities_rotate_and_expiry_is_not_fabricated(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=health() if request.method == "GET" else result())

    selected, capability = client(tmp_path, handle)

    async def scenario() -> None:
        first = await selected.acquire()
        capability.write_text("b" * 64, encoding="ascii")
        second = await selected.acquire()
        assert first.token_expires_at is second.token_expires_at is None
        assert 0 < second.remaining_seconds() <= 55
        assert first.token not in repr(first)
        assert [request.url.path for request in requests] == [
            "/vault/health", "/vault/token-fetch", "/vault/health", "/vault/token-fetch",
        ]
        assert [json.loads(request.content)["sources"] for request in requests[1::2]] == [
            {"controller-a": "a" * 64}, {"controller-a": "b" * 64},
        ]
        assert all(json.loads(request.content)["lease_contract"] == LEASE_CONTRACT
                   for request in requests[1::2])
        assert all(json.loads(request.content)["installationId"] == "gateway-a"
                   for request in requests[1::2])

    asyncio.run(scenario())


@pytest.mark.parametrize("support", [
    {}, health(normal_source_lease_contract=None), health(installationId="other"),
    health(normal_source_lease_contract={"fetch_contract": NORMAL_FETCH_CONTRACT}),
])
def test_old_or_replaced_gateway_never_reads_capabilities_or_posts(
    tmp_path: Path, support: dict[str, Any],
) -> None:
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, json=support)

    selected, capability = client(tmp_path, handle)
    capability.unlink()
    with pytest.raises(SourceLeaseError, match="unsupported"):
        asyncio.run(selected.acquire())
    assert methods == ["GET"]


@pytest.mark.parametrize("changes", [
    {"binding": "other"}, {"account": "other"}, {"host": "other"},
    {"installationId": "other"}, {"fetch_contract": "other"},
    {"lease_contract": "other"}, {"lease_id": "bad"},
    {"lease_remaining_seconds": True}, {"token_expires_at": True},
    {"token_expires_at": float("inf")}, {"token_expires_at": 1},
    {"state": "available"}, {"acquisition": "cache"},
])
def test_invalid_or_expired_gateway_result_is_never_retried(
    tmp_path: Path, changes: dict[str, Any],
) -> None:
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        value = health() if request.method == "GET" else result(**changes)
        return httpx.Response(200, content=json.dumps(value), headers={
            "Content-Type": "application/json",
        })

    selected, _ = client(tmp_path, handle)
    with pytest.raises(SourceLeaseError):
        asyncio.run(selected.acquire())
    assert methods == ["GET", "POST"]


def test_unknown_gateway_post_has_no_source_or_legacy_fallback(tmp_path: Path) -> None:
    methods: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=health())
        raise httpx.ReadError("synthetic-private-provider-detail")

    selected, _ = client(tmp_path, handle)
    with pytest.raises(SourceLeaseError, match="^source-lease-unavailable$"):
        asyncio.run(selected.acquire())
    assert methods == ["GET", "POST"]
