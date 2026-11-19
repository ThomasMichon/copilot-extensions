"""Provider-side optional assertions retain OS-admitted consumer ownership."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from fleet_manager.gateway_service_handler import BoundedServiceResponse
from fleet_manager.local_service_handler import LocalCallerContext, LocalServiceRequest
from agent_pull_requests.repository_contract import CONSUMER_BINDING_PROTOCOL, PROTOCOL
from agent_pull_requests.repository_gateway import RepositoryGatewayOwner, handle
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from test_repository_reviewer import Selection, observe, setup


def gateway_for(selected: Selection) -> WatchGateway:
    return WatchGateway(
        selected[1], fleet=None, gateway=None, slot=None,
        bindings=(WatchScopeBinding(
            "reader", "repository", ConsumerScope("consumer", selected[2].key, selected[2].repo),
        ),),
        local=True, repositories=selected[3], checkpoint=lambda: None,
    )


async def call(
    gateway: WatchGateway, selected: Selection, operation: str,
    extra: dict[str, Any] | None = None,
) -> BoundedServiceResponse:
    value = {
        "protocol": PROTOCOL, "namespace": "test", "repository_key": selected[2].key,
        "request": {"request_id": "original", "ttl": 60}, **(extra or {}),
    }
    request = LocalServiceRequest(
        LocalCallerContext("test", "reader", 10000),
        "GET" if operation == "capabilities" else "POST",
        f"/v1/repositories/repository/{operation}",
        (("content-type", "application/json"),),
        b"" if operation == "capabilities" else json.dumps(value).encode(),
    )
    result = await handle(cast(RepositoryGatewayOwner, gateway), request)
    assert isinstance(result, BoundedServiceResponse)
    return result


def test_running_capability_and_old_new_envelopes_keep_one_original_registration(tmp_path: Path) -> None:
    async def scenario() -> None:
        selected = setup(tmp_path)
        observe(selected)
        gateway = gateway_for(selected)
        try:
            capability = json.loads((await call(gateway, selected, "capabilities")).body)
            assert capability["consumer_binding"] == {
                "protocol": CONSUMER_BINDING_PROTOCOL, "consumer": "consumer",
            }
            legacy = await call(gateway, selected, "subscribe")
            original = json.loads(legacy.body)["result"]["subscription"]
            before = selected[1].path.read_bytes()
            current = await call(gateway, selected, "subscribe", {"expected_consumer": "consumer"})
            assert json.loads(current.body)["result"]["subscription"] == original
            assert selected[1].path.read_bytes() == before
        finally:
            gateway.stop_admission()
            await gateway.drain(timeout=2)
    asyncio.run(scenario())


def test_request_recovery_negotiates_and_reports_scoped_absence_or_cutoff_without_resubscription(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        selected = setup(tmp_path)
        gateway = gateway_for(selected)
        now = int(selected[0][0])
        cutoff = now + 10
        try:
            capability = json.loads((await call(gateway, selected, "capabilities")).body)
            assert capability["request_recovery"]["protocol"] == (
                "agent-pull-requests.repository-request-recovery/v1"
            )
            before = selected[1].path.read_bytes()
            absent = await call(gateway, selected, "inspect-request", {
                "request": {"request_id": "original"}, "expected_consumer": "consumer",
            })
            assert absent.status == 200 and json.loads(absent.body)["result"] == {
                "status": "not_found", "request_id": "original", "subscription": None, "now": now,
            }
            assert selected[1].path.read_bytes() == before
            bounded = {"request": {"request_id": "original", "ttl": 60, "not_after": cutoff}}
            admitted = json.loads((await call(gateway, selected, "subscribe", bounded)).body)["result"]
            selected[0][0] = cutoff
            before = selected[1].path.read_bytes()
            refused = await call(gateway, selected, "subscribe", {
                "request": {"request_id": "late", "ttl": 60, "not_after": cutoff},
            })
            assert refused.status == 410 and json.loads(refused.body) == {
                "error": "repository_request_expired", "reconcile": "inspect-request",
            }
            original = json.loads((await call(gateway, selected, "subscribe", bounded)).body)["result"]
            assert original["status"] == "expired"
            assert original["subscription"] == admitted["subscription"]
            assert selected[1].path.read_bytes() == before
        finally:
            gateway.stop_admission()
            await gateway.drain(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("extra,status,error", [
    ({"expected_consumer": "another"}, 409, "repository_consumer_mismatch"),
    ({"expected_consumer": None}, 409, "repository_consumer_mismatch"),
    ({"consumer": "consumer"}, 400, "invalid_repository_request"),
    ({"principal": "reader"}, 400, "invalid_repository_request"),
])
def test_optional_expectation_never_supplies_identity_or_bypasses_strict_fields(
    tmp_path: Path, extra: dict[str, Any], status: int, error: str,
) -> None:
    async def scenario() -> None:
        selected = setup(tmp_path)
        observe(selected)
        gateway = gateway_for(selected)
        before = selected[1].path.read_bytes()
        try:
            result = await call(gateway, selected, "subscribe", extra)
            assert result.status == status and json.loads(result.body)["error"] == error
            assert selected[1].path.read_bytes() == before
        finally:
            gateway.stop_admission()
            await gateway.drain(timeout=2)
    asyncio.run(scenario())
