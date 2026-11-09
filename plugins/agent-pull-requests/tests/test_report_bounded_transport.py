"""New/new TLS delivery and actual fixture-native capture budget composition."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import threading
import time

import pytest
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import BODY_BYTES, CHUNK_BYTES
from fleet_manager.gateway_service_connector import Exchange, ServiceConnector
from fleet_manager.gateway_service_handler import RequestDisconnected

from agent_pull_requests import __main__ as cli
from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.report_read import ReportUnavailable
from agent_pull_requests.watch_replay_client import ReplayClientError

from test_native_report import native as native, native_gateway as native_gateway
from test_report_bounded import LIMIT, bounded as bounded
from test_report_read import reports as reports


@pytest.mark.parametrize("operation", ["threads", "diff"])
@pytest.mark.parametrize("limit,thread_repeats,diff_bytes", [
    (LIMIT, 11000, 40000), (8 * 1024 * 1024, 160000, 2 * 1024 * 1024),
])
def test_actual_native_sdk_gateway_tls_and_source_cli_deliver_large_report(
    native_gateway, tmp_path, monkeypatch, capsys, operation, limit, thread_repeats, diff_bytes,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler

    store, gateway, selected, scope = native_gateway
    reader = next(iter(gateway.reports.values()))
    reader.response_limit = limit
    monkeypatch.setenv("FIXTURE_LARGE", "1")
    monkeypatch.setenv("FIXTURE_THREAD_REPEATS", str(thread_repeats))
    monkeypatch.setenv("FIXTURE_REPORT_BYTES", str(diff_bytes))
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"number":7,"budget_seconds":15}')))
    before = store.path.read_bytes()

    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle, response_limit=limit)

            async def access():
                return rig.access("caller-a", 3)

            client = ReportClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
                proof_key=rig.h.keys[3].get_op_key("sign"),
            ), route_prefix="/api", binding=selected.binding, scope=scope, response_limit=limit)
            assert await asyncio.to_thread(
                cli.main, ["report", operation, "--request", "-"], report_host=client,
            ) == 0
            value = json.loads(capsys.readouterr().out)
            assert value["max_response_bytes"] == limit and value["owner_exit"] == 0
            assert value["scope"] == scope and value["report"]["complete"] is None
            if operation == "threads":
                assert value["report"]["threads"][0]["comments"][0]["text"] == ("λ\r\n" * thread_repeats).rstrip()
            else:
                assert value["report"]["diff"] == "x" * diff_bytes + "λ\r\ntail"
            assert BODY_BYTES < len(json.dumps(value).encode()) < limit
            if limit > LIMIT:
                from agent_pull_requests.native_diff import _report as diff_report
                from agent_pull_requests.native_threads import _report as thread_report

                text = json.dumps(value["report"])
                assert len(text.encode()) > 1_048_576
                with pytest.raises(ValueError, match="size budget"):
                    (thread_report if operation == "threads" else diff_report)(text, scope["repo"], 7)
            assert rig.backends[0].requests == []
            await gateway.drain(timeout=2)
    asyncio.run(scenario())
    assert store.path.read_bytes() == before


def test_actual_native_capture_budget_refuses_without_a_second_body_read(native, monkeypatch):
    monkeypatch.setenv("FIXTURE_LARGE", "1")
    monkeypatch.setenv("FIXTURE_REPORT_BYTES", str(LIMIT + 1))
    owner, remaining, calls, _, _ = native
    binding = owner.resolve(remaining, response_limit=LIMIT)
    with pytest.raises(ReportUnavailable, match="transport unavailable"):
        binding.collect("diff", 7, remaining)
    assert len([call for call in calls() if any("/pulls/" in arg for arg in call)]) == 1


def test_actual_legacy_handler_refuses_v2_before_owner_resolution(bounded, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler

    _, gateway, reader, selected, _, state, _ = bounded

    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle)

            async def access():
                return rig.access("caller-a", 3)

            client = ReportClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
                proof_key=rig.h.keys[3].get_op_key("sign"),
            ), route_prefix="/api", binding=selected.binding, scope=reader.scope, response_limit=LIMIT)
            with pytest.raises(ReplayClientError, match="report_unsupported"):
                await client.capabilities()
            assert state.get("resolved", 0) == 0 and not state["calls"]
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


def test_actual_local_handler_refuses_v2_without_advertisement_or_body_read(bounded, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import Rig
    from test_gateway_local_handler import local_binding

    _, gateway, reader, selected, _, state, _ = bounded

    async def scenario():
        rig = Rig(tmp_path)

        async def handle(local):
            return await gateway.handle(local.request)

        try:
            await rig.start(local_handlers=(local_binding(rig, handle),))

            async def access():
                return rig.access("caller-a", 3)

            client = ReportClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
                proof_key=rig.h.keys[3].get_op_key("sign"),
            ), route_prefix="/api", binding=selected.binding, scope=reader.scope, response_limit=LIMIT)
            with pytest.raises(ReplayClientError, match="report_unsupported"):
                await client.capabilities()
            assert state.get("resolved", 0) == 0 and not state["calls"] and not rig.state.channels
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["expiry", "disconnect", "deadline"])
def test_report_chunks_keep_original_sdk_lifetime_during_ack_backpressure(bounded, loss):
    _, gateway, _, client, _, state, _ = bounded
    state["change"] = lambda value: value.update(future="x" * 40000)

    async def scenario():
        frames, requests = [], []
        started = asyncio.Event()

        async def handle(request):
            requests.append(request)
            return await gateway.handle(request)

        selected = gateway.handler_binding(response_limit=LIMIT)
        connector = ServiceConnector(
            slot="service-a", gateway_origin="https://gateway.example",
            handler=replace(selected, handle=handle),
            access_token_callback=lambda: None, proof_key=None,
        )
        spec = connector._request({
            "type": "request", "id": "1" * 48, "method": "POST", "target": "/v2/read/project/diff",
            "headers": {"content-type": "application/json"},
            "body": base64.b64encode(json.dumps({
                "binding": {**client.binding.document(), "report_scope": client.scope},
                "request": {"number": 7, "budget_seconds": 2, "expected_response_bytes": LIMIT},
            }).encode()).decode(),
            "timeout": .1 if loss == "deadline" else 60,
            "context": {"fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
                        "principal": "caller-a", "expires_at": int(time.time()) + 600},
        })
        request = spec[-1]
        exchange = Exchange("1" * 48, request=request)

        class Socket:
            async def send_str(self, raw):
                frame = json.loads(raw)
                frames.append(frame)
                if frame["type"] == "chunk":
                    started.set()

        pending = asyncio.create_task(connector._exchange(Socket(), None, exchange, spec))
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert [frame["type"] for frame in frames] == ["headers", "chunk"]
            assert len(base64.b64decode(frames[-1]["data"])) == CHUNK_BYTES
            assert requests == [request] and state["calls"] == [("diff", 7)]
            if loss == "expiry":
                from unittest.mock import patch
                with patch("fleet_manager.gateway_service_handler.time.time", return_value=2**52):
                    exchange.ack.set_result(None)
                    await asyncio.wait_for(pending, 2)
            elif loss == "disconnect":
                request.lifetime.close()
                exchange.ack.set_result(None)
                await asyncio.wait_for(pending, 2)
            else:
                await asyncio.wait_for(pending, 2)
            assert frames[-1]["type"] == "error" and not any(frame["type"] == "end" for frame in frames)
            with pytest.raises(RequestDisconnected):
                request.lifetime.check()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await gateway.drain(timeout=2)
    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["channel", "cancel", "policy"])
def test_v2_disconnect_retains_original_gateway_owner_until_native_cleanup(
    bounded, tmp_path, monkeypatch, loss,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler

    store, gateway, reader, selected, _, state, report = bounded
    started, release, settled = threading.Event(), threading.Event(), threading.Event()

    def collect(operation, number, remaining):
        remaining()
        started.set()
        try:
            assert release.wait(10)
            return 0, json.dumps({**report(operation, number), "future": "x" * 40000})
        finally:
            settled.set()
    state["binding"] = replace(state["binding"], collect=collect)
    before = store.path.read_bytes()

    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle, response_limit=LIMIT)

            async def access():
                return rig.access("caller-a", 3)

            client = ReportClient(ServiceCaller(
                gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
                proof_key=rig.h.keys[3].get_op_key("sign"),
            ), route_prefix="/api", binding=selected.binding, scope=reader.scope, response_limit=LIMIT)
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
