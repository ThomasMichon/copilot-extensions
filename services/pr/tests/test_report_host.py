"""Optional normal-host reports with isolated owners and no operator state."""

from __future__ import annotations

import asyncio
import builtins
from dataclasses import replace
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable

import pytest

from agent_pull_requests.native_report import NativeGitHubReport
from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.report_read import ReportBinding
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests import watch_service
from agent_pull_requests_service import hosting
from agent_pull_requests_service.config import load_config
from fleet_manager.gateway_service_channel import BODY_BYTES, RESPONSE_BYTES
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
else:
    from test_host import Configuration, configuration as configuration


def select_reports(configuration: Configuration, limit: int = BODY_BYTES) -> Configuration:
    path, value = configuration
    admitted = value["bindings"][0]
    value["reports"] = {
        "response_bytes": limit,
        "bindings": [{
            "principal": "caller-a", "target": "repository-a",
            "source_config": str(path.parent / "native.json"),
            "scope": {
                "provider": "github", "endpoint": "github.com",
                "account": admitted["account"], "repo": admitted["repo"],
            },
            "context_id": "selected-native",
        }],
    }
    path.write_text(json.dumps(value))
    return path, value


@pytest.fixture
def native_support(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-worktrees/src"))
    for source in (root / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(source))


@pytest.mark.parametrize("limit", [BODY_BYTES, BODY_BYTES + 1, RESPONSE_BYTES])
def test_selected_report_provision_restart_and_rollback_are_passive(
    configuration: Configuration, native_support: None, monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("native resolution during load/provision")

    monkeypatch.setattr(NativeGitHubReport, "resolve", forbidden)
    path, value = select_reports(configuration, limit)
    config = load_config(path)
    assert config.reports is not None
    config.provision()
    before = config.store().path.read_bytes()
    files = {p.name for p in config.root.iterdir()}
    assert load_config(path).store().path.read_bytes() == before
    value.pop("reports")
    path.write_text(json.dumps(value))
    assert load_config(path).reports is None
    assert load_config(path).store().path.read_bytes() == before
    assert {p.name for p in config.root.iterdir()} == files
    assert not (config.root / "review-cache").exists()


@pytest.mark.parametrize("change", [
    lambda v: v.update(response_bytes=True),
    lambda v: v.update(response_bytes=BODY_BYTES - 1),
    lambda v: v.update(response_bytes=RESPONSE_BYTES + 1),
    lambda v: v.update(response_bytes="32768"),
    lambda v: v.update(command=["arbitrary"]),
    lambda v: v.update(bindings=[]),
    lambda v: v["bindings"].append(v["bindings"][0]),
    lambda v: v["bindings"][0].update(principal=[]),
    lambda v: v["bindings"][0].update(target="not-admitted"),
    lambda v: v["bindings"][0].update(source_config="relative"),
    lambda v: v["bindings"][0].update(context_id=""),
    lambda v: v["bindings"][0].update(credential_command=["borrowed"]),
    lambda v: v["bindings"][0]["scope"].update(account="other"),
    lambda v: v["bindings"][0]["scope"].update(repo="other/repository"),
    lambda v: v["bindings"][0]["scope"].update(provider="gitea"),
    lambda v: v["bindings"][0]["scope"].update(endpoint="enterprise.example"),
    lambda v: v["bindings"][0]["scope"].update(number=7),
])
def test_selected_report_configuration_is_strict(
    configuration: Configuration, native_support: None,
    change: Callable[[dict[str, Any]], Any],
) -> None:
    path, value = select_reports(configuration)
    change(value["reports"])
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, TypeError)):
        load_config(path)


def test_missing_selected_native_owner_leaves_default_host_independent(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = builtins.__import__

    def importing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("agent_worktrees"):
            raise ImportError("not selected in this process")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    path, _ = configuration
    assert load_config(path).reports is None
    select_reports(configuration)
    with pytest.raises(ValueError, match="hosting APIs unsupported"):
        load_config(path)
    assert not Path(configuration[1]["root"]).exists()


def test_old_executing_service_api_refuses_before_service_credentials(
    configuration: Configuration, native_support: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = select_reports(configuration, RESPONSE_BYTES)
    config = load_config(path)
    config.provision()
    monkeypatch.setattr(watch_service, "WatchService", lambda: None)
    monkeypatch.setattr(
        hosting, "load_service_credentials",
        lambda **_kwargs: pytest.fail("credentials before selected support"),
    )
    with pytest.raises(ValueError, match="hosting APIs unsupported"):
        asyncio.run(hosting.run(config))


@pytest.mark.parametrize("old", ["native", "sdk"])
def test_selected_prior_native_or_sdk_api_refuses_without_acquisition(
    configuration: Configuration, native_support: None, monkeypatch: pytest.MonkeyPatch,
    old: str,
) -> None:
    if old == "native":
        monkeypatch.delattr(NativeGitHubReport, "check_support")
    else:
        from fleet_manager import gateway_service_handler
        monkeypatch.setattr(gateway_service_handler, "ServiceRequest", lambda: None)
    path, _ = select_reports(configuration, RESPONSE_BYTES)
    with pytest.raises(ValueError, match="hosting APIs unsupported"):
        load_config(path)
    assert not Path(configuration[1]["root"]).exists()


@pytest.mark.parametrize("limit", [BODY_BYTES, RESPONSE_BYTES])
def test_foreground_host_supplies_actual_connector_budget_and_request_only_owner(
    configuration: Configuration, native_support: None, monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    path, _ = select_reports(configuration, limit)
    config = load_config(path)
    config.provision()
    before = config.store().path.read_bytes()
    acquired: list[int] = []
    connectors: list[Any] = []

    class Connector:
        def __init__(self, **kwargs: Any) -> None:
            self.handler = kwargs["handler"]
            self.connected = asyncio.Event()
            connectors.append(self)

        async def run(self, stop: asyncio.Event) -> None:
            self.connected.set()
            await stop.wait()

    def resolve(self: Any, remaining: Callable[[], float], *,
                response_limit: int = BODY_BYTES) -> ReportBinding:
        assert remaining() > 0
        acquired.append(response_limit)

        def collect(_operation: str, number: int, check: Callable[[], float]) -> tuple[int, str]:
            check()
            return 0, json.dumps({
                "schema": "agent-worktrees.pr-diff-read", "version": 1,
                "provider": "github", "repo": "example/repository", "pr": number,
                "supported": True, "diff": "x" * (40000 if response_limit > BODY_BYTES else 10),
                "complete": None, "error": "",
            })

        return ReportBinding(
            self.scope, self.context_id, lambda: None, collect, response_limit=response_limit,
        )

    monkeypatch.setattr(NativeGitHubReport, "resolve", resolve)
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    async def access() -> str:
        pytest.fail("deterministic connector does not acquire observation credentials")
    monkeypatch.setattr(hosting, "load_service_credentials", lambda **_kwargs: SimpleNamespace(
        proof_key=object(), access_token=access,
    ))

    async def scenario() -> None:
        stop = asyncio.Event()
        owner = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            for _ in range(1000):
                if connectors and connectors[0].connected.is_set():
                    break
                if owner.done():
                    await owner
                await asyncio.sleep(.001)
            assert connectors and connectors[0].connected.is_set()
            binding = connectors[0].handler
            assert binding.response_limit == limit
            assert not acquired
            request = ServiceRequest(
                CallerContext("fleet-a", "gateway-a", "pr-watch", "caller-a", int(time.time()) + 60),
                "POST", "/", (), b"", response_limit=limit,
            )

            class Caller:
                async def request(
                    self, method: str, target: str, *, request_headers: tuple[tuple[str, str], ...],
                    body: bytes, response_limit: int,
                ) -> Any:
                    return await binding.handle(replace(
                        request, method=method, target=target,
                        headers=tuple((k.lower(), v) for k, v in request_headers), body=body,
                    ))

            client_type: Callable[..., Any] = ReportClient
            client = client_type(
                Caller(), route_prefix="", binding=ReplayBinding(
                    "fleet-a", "gateway-a", "pr-watch", config.namespace,
                    "caller-a", "repository-a", "dispatch-a", "account-a", "example/repository",
                ),
                scope={
                    "provider": "github", "endpoint": "github.com",
                    "account": "account-a", "repo": "example/repository",
                }, response_limit=limit,
            )
            await client.capabilities()
            result = await client.read("diff", {"number": 7, "budget_seconds": 5})
            assert len(result["report"]["diff"]) == (40000 if limit > BODY_BYTES else 10)
            assert acquired == [limit, limit]
            assert config.store().path.read_bytes() == before
        finally:
            stop.set()
            await asyncio.wait_for(owner, 5)

    asyncio.run(scenario())
