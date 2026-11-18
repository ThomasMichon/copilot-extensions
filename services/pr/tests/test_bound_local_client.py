"""Actual Unix/cache scope fencing, without a provider, task or service launch."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
import json
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

import pytest

from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests import repository_contract
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_local import LocalIngress
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests_service import bound_local_client
from agent_pull_requests_service.bound_local_client import BoundRepositoryClient
from agent_pull_requests_service.local_client import LocalClient, LocalClientError
from fleet_manager.gateway_service_handler import BoundedServiceResponse

if TYPE_CHECKING:
    from services.pr.tests.test_reviewer_feed_example import fixtures
else:
    from test_reviewer_feed_example import fixtures

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix client")


@dataclass
class Host:
    store: ReplayStore
    identity: RepositoryIdentity
    projection: Any
    path: Path
    gateways: list[WatchGateway] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    feature: str = "current"

    def select(self, consumer: str) -> None:
        self.gateways.append(WatchGateway(
            self.store, fleet=None, gateway=None, slot=None,
            bindings=(WatchScopeBinding(
                "reader", "repository", ConsumerScope(consumer, self.identity.key, self.identity.repo),
            ),),
            local=True, repositories=self.projection, checkpoint=lambda: None,
        ))

    def client(self, *, consumer: str = "consumer") -> BoundRepositoryClient:
        return BoundRepositoryClient(
            self.path, "repository", identity=self.identity, consumer=consumer,
        )


@asynccontextmanager
async def running(tmp_path: Path, *, feature: str = "current") -> AsyncIterator[Host]:
    selected = fixtures.setup(tmp_path)
    fixtures.observe(selected)
    host = Host(selected[1], selected[2], selected[3], tmp_path / "client.sock", feature=feature)
    host.select("consumer")

    async def handle(request: Any) -> Any:
        operation = request.target.rsplit("/", 1)[-1]
        host.calls.append({
            "method": request.method, "operation": operation,
            "body": json.loads(request.body) if request.body else None,
        })
        native: Callable[..., Any] = host.gateways[-1].handle_local
        response = await native(request)
        if operation == "capabilities" and host.feature != "current":
            assert isinstance(response, BoundedServiceResponse)
            value = json.loads(response.body)
            if host.feature == "absent":
                value.pop("consumer_binding")
            else:
                value["consumer_binding"]["protocol"] = "unsupported"
            return BoundedServiceResponse(200, response.headers, json.dumps(value).encode())
        return response

    ingress_type: Callable[..., Any] = LocalIngress
    ingress = ingress_type(
        path=host.path, namespace="test",
        peers={int(getattr(os, "getuid", lambda: -1)()): "reader"},
        handler=handle, operation_seconds=3,
    )
    stop = asyncio.Event()
    owner = asyncio.create_task(ingress.run(stop))
    try:
        await asyncio.wait_for(ingress.connected.wait(), 2)
        yield host
    finally:
        for gateway in host.gateways:
            gateway.stop_admission()
        stop.set()
        await asyncio.wait_for(owner, 4)
        for gateway in host.gateways:
            await gateway.drain(timeout=3)
        assert not ingress._tasks and not host.path.exists()


def test_actual_bound_selection_and_legacy_requests_share_the_existing_owner(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            client = host.client()
            before = host.store.path.read_bytes()
            selected = await asyncio.to_thread(client.negotiate)
            selected["identity"]["repo"] = "changed/output"
            selected["consumer_binding"]["consumer"] = "changed-output"
            page = await asyncio.to_thread(client.request, "next-actions", {"reviewer": "reviewer"})
            assert page["identity"] == host.identity.document() and page["consumer"] == "consumer"
            assert host.calls[-1]["body"]["expected_consumer"] == "consumer"
            assert host.store.path.read_bytes() == before
            legacy = LocalClient(host.path, "test", "repository")
            registration = await asyncio.to_thread(
                legacy.request, "subscribe", {"request_id": "legacy", "ttl": 60},
            )
            assert registration["subscription"]["consumer"] == "consumer"
            assert "expected_consumer" not in host.calls[-1]["body"]
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"namespace": "other"}, {"provider": "github"}, {"account": "other"},
    {"endpoint": "https://another.example"}, {"repo": "other/repository"},
])
def test_configured_full_identity_is_not_adopted_from_first_response(
    tmp_path: Path, change: dict[str, str],
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            client = BoundRepositoryClient(
                host.path, "repository", identity=replace(host.identity, **change), consumer="consumer",
            )
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError):
                await asyncio.to_thread(
                    client.request, "subscribe", {"request_id": "original", "ttl": 60},
                )
            assert [call["method"] for call in host.calls] == ["GET"]
            assert client.binding is None and host.store.path.read_bytes() == before
    asyncio.run(scenario())


@pytest.mark.parametrize("feature", ["absent", "unsupported"])
def test_missing_or_incompatible_live_support_refuses_before_mutation(
    tmp_path: Path, feature: str,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path, feature=feature) as host:
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="unsupported"):
                await asyncio.to_thread(
                    host.client().request, "subscribe", {"request_id": "original", "ttl": 60},
                )
            assert [call["method"] for call in host.calls] == ["GET"]
            assert host.store.path.read_bytes() == before
    asyncio.run(scenario())


def test_wrong_configured_consumer_never_reaches_a_registration(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="binding"):
                await asyncio.to_thread(
                    host.client(consumer="another").request,
                    "subscribe", {"request_id": "original", "ttl": 60},
                )
            assert [call["method"] for call in host.calls] == ["GET"]
            assert host.store.path.read_bytes() == before
    asyncio.run(scenario())


@pytest.mark.parametrize("streaming", [False, True])
def test_remapped_os_consumer_is_refused_before_existing_operation_effects(
    tmp_path: Path, streaming: bool,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            client = host.client()
            registered = await asyncio.to_thread(
                client.request, "subscribe", {"request_id": "original", "ttl": 60},
            )
            original = registered["subscription"]
            host.select("remapped")
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="repository_consumer_mismatch"):
                if streaming:
                    await asyncio.to_thread(lambda: list(client.stream({
                        "registration_id": original["registration_id"],
                        "reviewer": "reviewer", "seconds": 1,
                    })))
                else:
                    await asyncio.to_thread(
                        client.request, "subscribe", {"request_id": "another", "ttl": 60},
                    )
            assert host.calls[-1]["body"]["expected_consumer"] == "consumer"
            assert host.store.path.read_bytes() == before
            host.select("consumer")
            inspected = await asyncio.to_thread(
                client.request, "inspect", {"registration_id": original["registration_id"]},
            )
            assert inspected["subscription"] == original
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["path", "namespace", "target", "binding"])
def test_mutated_client_configuration_cannot_retarget_original_scope(
    tmp_path: Path, change: str,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            client = host.client()
            await asyncio.to_thread(client.negotiate)
            if change == "binding":
                assert client.binding is not None
                client.binding["repository_key"] = "another"
            else:
                setattr(client, change, tmp_path / "another.sock" if change == "path" else "another")
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="changed"):
                await asyncio.to_thread(
                    client.request, "subscribe", {"request_id": "original", "ttl": 60},
                )
            assert len(host.calls) == 1 and host.store.path.read_bytes() == before
    asyncio.run(scenario())


def test_unsupported_platform_refuses_without_attempting_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bound_local_client, "sys", SimpleNamespace(platform="win32"))
    identity = RepositoryIdentity("test", "gitea", "https://forge.example", "reader", "owner/repo")
    with pytest.raises(LocalClientError, match="Linux"):
        BoundRepositoryClient(tmp_path / "client.sock", "repository",
                              identity=identity, consumer="consumer")


def test_prior_library_shape_imports_then_refuses_missing_live_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path, feature="absent") as host:
            monkeypatch.delattr(repository_contract, "CONSUMER_BINDING_PROTOCOL")
            spec = importlib.util.spec_from_file_location(
                "agent_pull_requests_service._bound_prior_contract", bound_local_client.__file__,
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            client = module.BoundRepositoryClient(
                host.path, "repository", identity=host.identity, consumer="consumer",
            )
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="unsupported"):
                await asyncio.to_thread(
                    client.request, "subscribe", {"request_id": "original", "ttl": 60},
                )
            assert [call["method"] for call in host.calls] == ["GET"]
            assert host.store.path.read_bytes() == before
    asyncio.run(scenario())
