"""Actual optional report dispatch without a provider, listener or SDK extension."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fleet_manager import gateway_service_handler as sdk
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest, ServiceResponse

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_unconfigured_reports_keep_structured_refusal_without_optional_sdk_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    monkeypatch.delattr(sdk, "BoundedServiceResponse")
    store = ReplayStore(
        tmp_path, "service", clock=lambda: 100,
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"),
    )
    store.provision()
    gateway = WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=(WatchScopeBinding(
            "caller-a", "project", ConsumerScope("consumer-a", "account-a", "example/project"),
        ),),
    )
    before = store.path.read_bytes()
    response = asyncio.run(gateway.handle(ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", 160),
        "POST", f"/{version}/read/project/capabilities",
        (("content-type", "application/json"),), b"{}",
    )))
    assert isinstance(response, ServiceResponse)
    assert response.status == 404
    assert json.loads(response.body) == {"error": "report_unsupported"}
    assert store.path.read_bytes() == before
