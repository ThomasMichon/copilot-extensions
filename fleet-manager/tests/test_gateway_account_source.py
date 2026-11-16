"""Account-source custody over the existing outbound service channel."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable
from typing import Any, TypeVar

import pytest

from fleet_manager.gateway_account_source import (
    LEASE_CONTRACT, AccountSource, AccountSourceGrant, AccountSourceHandler,
)
from fleet_manager.gateway_vault_available import NORMAL_FETCH_CONTRACT
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest, ServiceResponse


CAPABILITY = "synthetic-source-capability-not-a-real-secret"
PIN = {"X-Agent-Index-Installation-Id": "source-installation"}
FIELDS = {
    "source": "repo-a", "account": "account-a", "recipient": "worker-a",
    "host": "github.com", "action": "get-github-token",
}
T = TypeVar("T")


class Source:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, str], str, bool]] = []
        self.result: str | None = "protocol=https\nhost=github.com\nusername=account-a\ntoken=synthetic-token\n\n"

    async def resolve(
        self, fields: dict[str, str], capability: str, *, source_lease: bool = False,
    ) -> str | None:
        self.calls.append((fields, capability, source_lease))
        return self.result


def handler(source: AccountSource, *, installation: str = "source-installation") -> AccountSourceHandler:
    return AccountSourceHandler(
        source, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        installation=installation,
        grants=(AccountSourceGrant("caller-a", **FIELDS),),
    )


def request(value: object = None, *, principal: str = "caller-a", method: str = "POST",
            target: str = "/source-credentials", raw: bytes | None = None,
            headers: tuple[tuple[str, str], ...] = tuple(PIN.items()),
            **context: Any) -> ServiceRequest:
    return ServiceRequest(
        CallerContext(**{
            "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
            "principal": principal, "expires_at": int(time.time()) + 3600, **context,
        }),
        method, target, headers,
        raw if raw is not None else json.dumps(
            {**FIELDS, "capability": CAPABILITY} if value is None else value,
        ).encode(),
    )


def test_legacy_account_response_preserves_exact_source_capability() -> None:
    source = Source()
    result = asyncio.run(handler(source).handle(request()))
    assert result.status == 200
    assert json.loads(result.body) == {
        "account": "account-a", "host": "github.com", "token": "synthetic-token",
        "installationId": "source-installation",
    }
    assert source.calls == [(FIELDS, CAPABILITY, False)]
    assert CAPABILITY.encode() not in result.body
    assert b"expires_at" not in result.body


def test_source_lease_is_not_token_expiry_and_acquisition_consumes_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall, monotonic = [1000.0], [10.0]
    monkeypatch.setattr("fleet_manager.gateway_account_source.time.time", lambda: wall[0])
    monkeypatch.setattr("fleet_manager.gateway_account_source.time.monotonic", lambda: monotonic[0])
    source = Source()

    async def resolve(
        fields: dict[str, str], capability: str, *, source_lease: bool = False,
    ) -> str:
        assert fields == FIELDS and capability == CAPABILITY and source_lease
        wall[0], monotonic[0] = 1002.0, 12.0
        assert source.result is not None
        return (
            source.result.rstrip() + f"\nlease_contract={LEASE_CONTRACT}\n"
            f"lease_id={'a' * 32}\nlease_issued_at=1000\nlease_expires_at=1060\n\n"
        )

    monkeypatch.setattr(source, "resolve", resolve)
    result = asyncio.run(handler(source).handle(request(
        {**FIELDS, "capability": CAPABILITY, "lease_contract": LEASE_CONTRACT},
        expires_at=1100,
    )))
    assert result.status == 200
    value = json.loads(result.body)
    assert value["lease_remaining_seconds"] == 58
    assert value["lease_expires_at"] == 1060
    assert "token_expires_at" not in value and "scopes" not in value


@pytest.mark.parametrize("changes", [
    {"principal": "foreign"}, {"fleet": "foreign"}, {"gateway": "foreign"},
    {"slot": "foreign"}, {"expires_at": 1},
])
def test_unadmitted_context_never_reaches_source(changes: dict[str, Any]) -> None:
    source = Source()
    result = asyncio.run(handler(source).handle(request(**changes)))
    assert result.status == 403
    assert source.calls == []


@pytest.mark.parametrize("changes", [
    {"source": "foreign"}, {"account": "foreign"}, {"recipient": "foreign"},
    {"host": "foreign"}, {"action": "get-azure-token"},
    {"capability": "short"}, {"lease_contract": "unsupported"}, {"extra": "field"},
])
def test_invalid_or_unselected_request_never_reaches_source(changes: dict[str, str]) -> None:
    source = Source()
    result = asyncio.run(handler(source).handle(request({**FIELDS, "capability": CAPABILITY, **changes})))
    assert result.status in (400, 403, 409)
    assert source.calls == []


@pytest.mark.parametrize("raw", [
    b'{"source":"repo-a","source":"foreign"}', b'{"capability":NaN}',
    b"[]", b"\xff", b" " * 16385,
])
def test_malformed_body_does_not_acquire(raw: bytes) -> None:
    source = Source()
    assert asyncio.run(handler(source).handle(request(raw=raw))).status == 400
    assert source.calls == []


@pytest.mark.parametrize("result", [
    None, "", "token=synthetic-token\n\n",
    "protocol=https\nhost=foreign\nusername=account-a\ntoken=synthetic-token\n\n",
    "protocol=https\nhost=github.com\nusername=foreign\ntoken=synthetic-token\n\n",
    "protocol=https\nhost=github.com\nusername=account-a\ntoken=x\ntoken=y\n\n",
    "protocol=https\nhost=github.com\nusername=account-a\ntoken=x\nextra=private\n\n",
])
def test_unavailable_or_mismatched_provider_result_is_sanitized(result: str | None) -> None:
    source = Source()
    source.result = result
    response = asyncio.run(handler(source).handle(request()))
    assert response.status == 503
    assert json.loads(response.body) == {"error": "source-credential-unavailable"}
    assert len(source.calls) == 1


def test_support_uses_selected_handler_without_fetching_credentials() -> None:
    source = Source()
    response = asyncio.run(handler(source).handle(request(
        method="GET", target="/health", raw=b"",
    )))
    assert response.status == 200
    assert json.loads(response.body) == {
        "installationId": "source-installation",
        "source_credentials_contract": {"lease_contract": LEASE_CONTRACT, "exchange": "http/v1"},
        "normal_fetch_contract": NORMAL_FETCH_CONTRACT,
    }
    assert source.calls == []


@pytest.mark.parametrize("changes", [
    {"lease_contract": "unsupported"}, {"lease_id": "not-an-id"},
    {"lease_issued_at": "nan"}, {"lease_expires_at": "inf"},
    {"lease_expires_at": "1000"}, {"lease_expires_at": "5000"},
])
def test_invalid_source_lease_is_never_delivered(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, str],
) -> None:
    monkeypatch.setattr("fleet_manager.gateway_account_source.time.time", lambda: 1000.0)
    source = Source()
    lease = {
        "lease_contract": LEASE_CONTRACT, "lease_id": "a" * 32,
        "lease_issued_at": "999", "lease_expires_at": "1060", **changes,
    }
    assert source.result is not None
    source.result = source.result.rstrip() + "\n" + "\n".join(
        f"{key}={value}" for key, value in lease.items()
    ) + "\n\n"
    response = asyncio.run(handler(source).handle(request(
        {**FIELDS, "capability": CAPABILITY, "lease_contract": LEASE_CONTRACT},
        expires_at=1100,
    )))
    assert response.status == 503
    assert b"synthetic-token" not in response.body


def test_disconnect_after_acquisition_never_returns_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source()
    value = request()

    async def resolve(
        fields: dict[str, str], capability: str, *, source_lease: bool = False,
    ) -> str | None:
        value.lifetime.close()
        return source.result

    monkeypatch.setattr(source, "resolve", resolve)
    response = asyncio.run(handler(source).handle(value))
    assert response.status == 503
    assert b"synthetic-token" not in response.body


def test_disconnect_before_scheduled_acquisition_never_calls_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source()
    value = request()
    wait_for = asyncio.wait_for

    async def close_before_start(awaitable: Awaitable[T], timeout: float | None) -> T:
        value.lifetime.close()
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr("fleet_manager.gateway_account_source.asyncio.wait_for", close_before_start)
    response = asyncio.run(handler(source).handle(value))
    assert response.status == 503
    assert source.calls == []


@pytest.mark.parametrize("pins", [
    (), (("X-Agent-Index-Installation-Id", "foreign"),),
    (("X-Agent-Index-Installation-Id", "source-installation"),
     ("x-agent-index-installation-id", "source-installation")),
])
def test_missing_mismatched_or_duplicate_installation_pin_never_acquires(
    pins: tuple[tuple[str, str], ...],
) -> None:
    from dataclasses import replace

    source = Source()
    value = replace(request(), headers=pins)
    response = asyncio.run(handler(source).handle(value))
    assert response.status == 409
    assert source.calls == []


def test_channel_cancellation_is_propagated_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        source = Source()
        started = asyncio.Event()

        async def resolve(
            fields: dict[str, str], capability: str, *, source_lease: bool = False,
        ) -> None:
            source.calls.append((fields, capability, source_lease))
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(source, "resolve", resolve)
        pending: asyncio.Task[ServiceResponse] = asyncio.create_task(handler(source).handle(request()))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert len(source.calls) == 1

    asyncio.run(scenario())


def test_normal_fetch_preserves_unknown_expiry_without_requesting_a_source_lease() -> None:
    source = Source()
    value = {
        **FIELDS, "capability": CAPABILITY, "fetch_contract": NORMAL_FETCH_CONTRACT,
        "installationId": "source-installation",
    }
    response = asyncio.run(handler(source).handle(request(
        value, target="/token-fetch", headers=(),
    )))
    assert response.status == 200
    result = json.loads(response.body)
    assert result == {
        **FIELDS, "fetch_contract": NORMAL_FETCH_CONTRACT, "acquisition": "normal-fetch",
        "token_expires_at": None, "installationId": "source-installation",
        "token": "synthetic-token",
    }
    assert source.calls == [(FIELDS, CAPABILITY, False)]
    assert CAPABILITY.encode() not in response.body


@pytest.mark.parametrize("changes,status", [
    ({"installationId": "replacement"}, 409), ({"fetch_contract": "unsupported"}, 409),
    ({"lease_contract": LEASE_CONTRACT}, 400), ({"account": "foreign"}, 403),
    ({"source": "foreign"}, 403), ({"recipient": "foreign"}, 403),
])
def test_normal_fetch_never_acquires_without_exact_contract_pin_and_grant(
    changes: dict[str, str], status: int,
) -> None:
    source = Source()
    response = asyncio.run(handler(source).handle(request({
        **FIELDS, "capability": CAPABILITY, "fetch_contract": NORMAL_FETCH_CONTRACT,
        "installationId": "source-installation", **changes,
    }, target="/token-fetch", headers=())))
    assert response.status == status
    assert not source.calls
