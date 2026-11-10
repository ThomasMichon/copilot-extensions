"""Foreground host and real TLS/SDK transport with synthetic native children."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from importlib import import_module
import json
import os
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

import pytest
from cryptography.hazmat.primitives import serialization

from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests.watch_replay_client import ReplayBatch, ReplayClient, ReplayClientError
from agent_pull_requests_service import hosting
from agent_pull_requests_service.config import HostConfig, load_config
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import BODY_BYTES, RESPONSE_BYTES

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
    from services.pr.tests.test_report_host import native_support as native_support, select_reports
else:
    from test_host import Configuration, configuration as configuration
    from test_report_host import native_support as native_support, select_reports


@pytest.fixture
def selected_native(
    configuration: Configuration, native_support: None,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Configuration, Any, Callable[[], list[Any]]]:
    if os.name == "nt":
        pytest.skip("fixture gh executable is POSIX; native Windows qualification is separate")
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-pull-requests/tests"))
    fixture = import_module("test_native_report")
    native, _, calls, _, source = fixture.native_fixture(tmp_path, monkeypatch)
    _, value = configuration
    for row in (value["bindings"][0], value["observations"][0]):
        row.update(account=native.scope["account"], repo=native.scope["repo"])
    path, value = select_reports(configuration)
    value["reports"]["bindings"][0]["source_config"] = str(source)
    path.write_text(json.dumps(value))
    monkeypatch.syspath_prepend(str(root / "fleet-manager/tests"))
    return configuration, native, calls


@asynccontextmanager
async def foreground(
    configuration: Configuration, rig: Any,
) -> AsyncIterator[tuple[HostConfig, asyncio.Event, asyncio.Task[None], Any]]:
    path, value = configuration
    proof, access = path.parent / "service.pem", path.parent / "service-access"
    proof.write_bytes(rig.h.keys[1].get_op_key("sign").private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    access.write_text(rig.access("service-a", 1))
    value.update(
        slot="service-a", gateway_origin=rig.origin, gateway_ca=rig.ca,
        proof_key_file=str(proof), access_token_file=str(access),
        interval=1, drain_timeout=1,
    )
    path.write_text(json.dumps(value))
    config = load_config(path)
    if not config.root.exists():
        config.provision()
    stop = asyncio.Event()
    previous_channel = rig.state.channels.get("service-a")
    owner = asyncio.create_task(hosting.run(config, stop=stop))
    transport = import_module("gateway_service_transport_fixtures")
    try:
        await transport.eventually(lambda: (
            hosting.ready(config.status_path)
            and rig.state.channels.get("service-a") is not None
            and rig.state.channels.get("service-a") is not previous_channel
        ), timeout=5)

        async def caller_access() -> str:
            rig.h.clock.value = int(time.time())
            return str(rig.access("caller-a", 3))

        caller = ServiceCaller(
            gateway_origin=rig.origin, gateway_ca=rig.ca,
            access_token_callback=caller_access, proof_key=rig.h.keys[3].get_op_key("sign"),
        )
        yield config, stop, owner, caller
    finally:
        stop.set()
        await asyncio.wait_for(asyncio.shield(owner), 10)


def report_client(config: HostConfig, caller: Any, limit: int) -> Any:
    admitted = config.bindings[0]
    assert config.reports is not None
    client_type: Callable[..., Any] = ReportClient
    return client_type(
        caller, route_prefix="/api", binding=ReplayBinding(
            config.fleet, config.gateway, config.slot, config.namespace,
            admitted.principal, admitted.target, admitted.scope.consumer,
            admitted.scope.account, admitted.scope.repo,
        ), scope=config.reports.bindings[0][4], response_limit=limit,
    )


@pytest.mark.parametrize("limit", [BODY_BYTES, RESPONSE_BYTES])
def test_actual_foreground_native_tls_reports_and_fresh_restart(
    selected_native: tuple[Configuration, Any, Callable[[], list[Any]]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: int,
) -> None:
    configuration, _, calls = selected_native
    configuration[1]["reports"]["response_bytes"] = limit
    transport = import_module("gateway_service_transport_fixtures")

    async def scenario() -> None:
        async with transport.running(tmp_path) as rig:
            async with foreground(configuration, rig) as (config, _, _, caller):
                assert not calls()
                before = config.store().path.read_bytes()
                client = report_client(config, caller, limit)
                await client.capabilities()
                threads = await client.read("threads", {"number": 7, "budget_seconds": 15})
                assert threads["owner_exit"] == 0
                assert threads["report"]["threads"][0]["comments"][0]["text"] == "body\r\ntext"
                if limit > BODY_BYTES:
                    monkeypatch.setenv("FIXTURE_LARGE", "1")
                    monkeypatch.setenv("FIXTURE_REPORT_BYTES", str(1100000))
                result = await client.read("diff", {"number": 7, "budget_seconds": 15})
                expected = (
                    "x" * 1100000 + "\u03bb\r\ntail" if limit > BODY_BYTES
                    else "diff --git a/x b/x\r\n+body\rtail"
                )
                assert result["report"]["diff"] == expected
                assert result["owner_exit"] == 0
                if limit > BODY_BYTES:
                    assert len(json.dumps(result).encode()) > 1048576
                    # The prior v1 peer keeps its original bound on the same host.
                    old = report_client(config, caller, BODY_BYTES)
                    await old.capabilities()
                    with pytest.raises(ReplayClientError):
                        await old.read("diff", {"number": 7, "budget_seconds": 15})
                monkeypatch.delenv("FIXTURE_LARGE", raising=False)
                monkeypatch.setenv("FIXTURE_FAIL", "1")
                failed = await client.read("diff", {"number": 7, "budget_seconds": 15})
                assert failed["owner_exit"] == 1
                assert failed["report"]["supported"] and failed["report"]["complete"] is False
                assert "fixture-secret" not in json.dumps(failed)
                replay = ReplayClient(
                    caller, route_prefix="/api", target=client.binding.target,
                    repo=client.binding.repo, binding=client.binding,
                )
                pending = await replay.pending()
                assert isinstance(pending, ReplayBatch) and pending.events == ()
                assert config.store().path.read_bytes() == before
                assert rig.backends[0].requests == []
            previous = calls()
            async with foreground(configuration, rig) as (config, _, _, caller):
                assert calls() == previous
                assert config.store().path.read_bytes() == before
                await report_client(config, caller, limit).capabilities()
            assert calls() != previous
            for path in config.root.iterdir():
                if path.is_file():
                    assert b"fixture-secret" not in path.read_bytes()
                    assert b"body\\r\\ntext" not in path.read_bytes()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["account", "policy"])
def test_actual_foreground_changed_native_selection_refuses_before_body(
    selected_native: tuple[Configuration, Any, Callable[[], list[Any]]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    configuration, native, calls = selected_native
    transport = import_module("gateway_service_transport_fixtures")

    async def scenario() -> None:
        async with transport.running(tmp_path) as rig:
            async with foreground(configuration, rig) as (config, _, _, caller):
                client = report_client(config, caller, BODY_BYTES)
                await client.capabilities()
                before = calls()
                if change == "account":
                    monkeypatch.setenv("FIXTURE_LOGIN", "wrong")
                else:
                    policy = json.loads(native.config_path.read_text())
                    policy["repos"]["target"]["pr"]["provider"] = "gitea"
                    native.config_path.write_text(json.dumps(policy))
                with pytest.raises(ReplayClientError):
                    await client.read("diff", {"number": 7, "budget_seconds": 15})
                assert all(
                    not any("/pulls/" in arg or "graphql" in arg for arg in call)
                    for call in calls()[len(before):]
                )

    asyncio.run(scenario())
