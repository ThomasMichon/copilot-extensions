"""Actual authenticated SDK/connector and delayed read-owner settlement."""

import asyncio
import json
from pathlib import Path
import threading

import pytest
from fleet_manager.gateway_service_caller import ServiceCaller

from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.watch_replay_client import ReplayClientError

from test_report_read import reports as reports


def test_actual_sdk_gateway_scopes_full_bodies_and_policy_withdrawal(reports, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler

    store, gateway, reader, selected, _, state, report = reports
    before = store.path.read_bytes()
    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle)
            async def access():
                return rig.access("caller-a", 3)
            client = ReportClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
                proof_key=rig.h.keys[3].get_op_key("sign"),
            ), route_prefix="/api", binding=selected.binding, scope=reader.scope)
            await client.capabilities()
            for operation in ("threads", "diff"):
                value = await client.read(operation, {"number": 7, "budget_seconds": 2})
                assert value["report"] == report(operation, 7)
                assert value["owner_exit"] == 0
            rig.h.document["callers"] = []
            await rig.update()
            with pytest.raises(ReplayClientError) as caught:
                await client.read("diff", {"number": 7, "budget_seconds": 2})
            assert caught.value.http_status == 403
            assert state["calls"] == [("threads", 7), ("diff", 7)]
            assert rig.backends[0].requests == []
            await gateway.drain(timeout=1)
    asyncio.run(scenario())
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("loss", ["channel", "cancel", "policy"])
def test_disconnect_keeps_original_gateway_task_until_collector_cleanup_settles(
    reports, tmp_path, monkeypatch, loss,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler
    from dataclasses import replace

    store, gateway, reader, selected, _, state, report = reports
    started, release, settled = threading.Event(), threading.Event(), threading.Event()
    def collect(operation, number, remaining):
        remaining()
        started.set()
        try:
            assert release.wait(10)
            return 0, json.dumps(report(operation, number))
        finally:
            settled.set()
    state["binding"] = replace(state["binding"], collect=collect)
    before = store.path.read_bytes()

    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle)
            async def access():
                return rig.access("caller-a", 3)
            client = ReportClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
                proof_key=rig.h.keys[3].get_op_key("sign"),
            ), route_prefix="/api", binding=selected.binding, scope=reader.scope)
            await client.capabilities()
            pending = asyncio.create_task(client.read("diff", {"number": 7, "budget_seconds": 2}))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                if loss == "channel":
                    await rig.state.channels["service-a"].close()
                elif loss == "policy":
                    rig.h.document["callers"] = []
                    await rig.update()
                else:
                    pending.cancel()
                with pytest.raises((ReplayClientError, asyncio.CancelledError)):
                    await pending
                await eventually(lambda: not rig.connectors[-1].active)
                assert not settled.is_set() and len(gateway._tasks) == 1
                with pytest.raises(TimeoutError):
                    await gateway.drain(timeout=.01)
                assert len(gateway._tasks) == 1
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                await gateway.drain(timeout=3)
            assert settled.is_set() and not gateway._tasks
            assert rig.backends[0].requests == []
    asyncio.run(scenario())
    assert store.path.read_bytes() == before
