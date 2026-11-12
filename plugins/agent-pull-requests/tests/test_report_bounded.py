"""Opt-in report profile budgets; isolated owners, no installed enrollment."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import json
import sys
import time
from types import SimpleNamespace

import pytest
from fleet_manager.gateway_service_channel import BODY_BYTES, RESPONSE_BYTES
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest, ServiceResponse

from agent_pull_requests import __main__ as cli
from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.report_read import ReportReader
from agent_pull_requests.watch_replay_client import ReplayClientError

from test_report_read import read, reports as reports

LIMIT = 128 * 1024


@pytest.fixture
def bounded(reports):
    store, gateway, reader, old_client, caller, state, report = reports

    def resolve(remaining, *, response_limit=BODY_BYTES):
        state.setdefault("budgets", []).append(response_limit)
        return replace(reader.resolve_binding(remaining), response_limit=response_limit)

    owner = ReportReader(
        reader.consumer_scope, reader.scope, resolve, max_work_seconds=3,
        clock=reader.clock, response_limit=LIMIT,
    )
    gateway.reports = {(old_client.binding.principal, old_client.binding.target): owner}
    caller.handler_response_limit = LIMIT
    client = ReportClient(caller, route_prefix="", binding=old_client.binding,
                          scope=reader.scope, response_limit=LIMIT)
    return store, gateway, owner, client, caller, state, report


@pytest.mark.parametrize("operation", ["threads", "diff"])
@pytest.mark.parametrize("outcome", ["success", "native-error", "owner-disagrees"])
def test_source_cli_keeps_large_whole_reports_without_replay_writes(
    bounded, monkeypatch, capsys, operation, outcome,
):
    store, gateway, _, client, caller, state, report = bounded
    original = store.path.read_bytes()

    def large(value):
        value["future"] = {"nested": ["λ\r\n\ttail" * 5500, None, False]}
        if outcome == "native-error":
            value.update(error="provider refusal", complete=False)
    state["change"] = large
    state["exit"] = 0 if outcome == "success" else 17
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"number":7,"budget_seconds":2}')))
    assert cli.main(["report", operation, "--request", "-"], report_host=client) == int(outcome != "success")
    value = json.loads(capsys.readouterr().out)
    expected = report(operation, 7)
    large(expected)
    assert value["report"] == expected and value["owner_exit"] == state["exit"]
    assert value["profile"] == "owner_executed_report/v2" and value["max_response_bytes"] == LIMIT
    assert BODY_BYTES < len(json.dumps(value).encode()) <= LIMIT
    assert caller.calls == [f"/v2/read/project/{verb}" for verb in ("capabilities", operation)]
    assert state["calls"] == [(operation, 7)] and state["budgets"] == [LIMIT, LIMIT]
    assert store.path.read_bytes() == original
    asyncio.run(gateway.drain(timeout=1))


@pytest.mark.parametrize("transport", [BODY_BYTES, LIMIT // 2, None, True])
def test_unqualified_transport_refuses_before_resolution_or_capabilities(bounded, transport):
    _, _, _, client, caller, state, _ = bounded
    caller.handler_response_limit = transport
    with pytest.raises(ReplayClientError, match="report_unsupported"):
        read(client)
    assert state.get("resolved", 0) == 0 and not state["calls"]
    assert len(caller.calls) == 1


@pytest.mark.parametrize("change", ["old-owner", "old-sdk", "wrong-scope"])
def test_missing_support_cannot_advertise_or_acquire(bounded, monkeypatch, change):
    _, gateway, reader, client, _, state, _ = bounded
    if change == "old-owner":
        reader.resolve_binding = lambda remaining: state["binding"]
    elif change == "old-sdk":
        import fleet_manager.gateway_service_handler as sdk
        monkeypatch.delattr(sdk, "BoundedServiceResponse")
    else:
        client.scope["endpoint"] = "elsewhere"
    with pytest.raises(ReplayClientError):
        read(client)
    assert not state["calls"]
    if change != "old-owner":
        assert state.get("resolved", 0) == 0


@pytest.mark.parametrize("expected", [None, True, 0, BODY_BYTES, LIMIT + 1, float("inf")])
def test_expectation_is_strict_and_precedes_owner_resolution(bounded, expected):
    _, gateway, _, client, _, state, _ = bounded
    payload = {"number": 7, "budget_seconds": 2}
    if expected is not None:
        payload["expected_response_bytes"] = expected
    request = ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", int(time.time()) + 60),
        "POST", "/v2/read/project/diff", (("content-type", "application/json"),),
        json.dumps({"binding": {**client.binding.document(), "report_scope": client.scope},
                    "request": payload}).encode(),
        response_limit=LIMIT,
    )
    assert asyncio.run(gateway.handle(request)).status == 400
    assert state.get("resolved", 0) == 0 and not state["calls"]


@pytest.mark.parametrize("change", ["budget", "both-budgets", "reader", "rollback"])
def test_negotiation_changes_refuse_without_a_second_acquisition(bounded, change):
    _, gateway, reader, client, caller, state, _ = bounded

    async def scenario():
        await client.capabilities()
        if change in ("budget", "both-budgets"):
            reader.response_limit = LIMIT // 2
            if change == "both-budgets":
                caller.handler_response_limit = LIMIT // 2
        elif change == "reader":
            gateway.reports = {}
        else:
            caller.handler_response_limit = BODY_BYTES
        with pytest.raises(ReplayClientError):
            await client.read("diff", {"number": 7, "budget_seconds": 2})
    asyncio.run(scenario())
    assert state["resolved"] == 1 and not state["calls"] and len(caller.calls) == 2


def test_v1_coexists_but_never_inherits_v2_capture_or_response_allocation(bounded):
    _, _, reader, selected, caller, state, _ = bounded
    old = ReportClient(caller, route_prefix="", binding=selected.binding, scope=reader.scope)
    state["change"] = lambda value: value.update(future="x" * BODY_BYTES)
    with pytest.raises(ReplayClientError) as caught:
        read(old)
    assert caught.value.http_status == 503 and state["budgets"] == [BODY_BYTES, BODY_BYTES]
    assert read(selected)["max_response_bytes"] == LIMIT
    assert state["budgets"][-2:] == [LIMIT, LIMIT]


def test_encoded_envelope_overhead_and_escaping_count_toward_the_limit(bounded):
    state = bounded[5]
    # Native JSON fits; escaping the exchange plus binding/metadata does not.
    state["change"] = lambda value: value.update(future="λ" * (LIMIT // 3))
    with pytest.raises(ReplayClientError) as caught:
        read(bounded[3])
    assert caught.value.http_status == 503 and state["calls"] == [("threads", 7)]


@pytest.mark.parametrize("malformed", ["over-limit", "declaration", "read-budget"])
def test_client_enforces_its_own_collection_and_negotiated_exchange(bounded, malformed):
    caller, client, state = bounded[4], bounded[3], bounded[5]

    def change(response):
        value = json.loads(response.body)
        if malformed == "declaration" or "report" in value:
            if malformed == "over-limit":
                return SimpleNamespace(status=200, headers=response.headers, body=b" " * (LIMIT + 1))
            value["max_response_bytes"] = RESPONSE_BYTES if malformed == "declaration" else LIMIT // 2
        return replace(response, body=json.dumps(value).encode())
    caller.change = change
    with pytest.raises(ReplayClientError, match="invalid_report_response"):
        read(client)
    assert len(state["calls"]) == (0 if malformed == "declaration" else 1)


def test_v2_client_old_host_never_selects_v1_or_native_fallback(bounded):
    caller, client, state = bounded[4], bounded[3], bounded[5]
    caller.change = lambda response: ServiceResponse(404)
    with pytest.raises(ReplayClientError, match="report_unsupported"):
        read(client)
    assert len(caller.calls) == 1 and not state["calls"]


@pytest.mark.parametrize("gap,status", [("deadline", 503), ("withdrawal", 403), ("budget", 503)])
def test_v2_selection_and_lifetime_are_rechecked_at_final_publication(bounded, gap, status):
    gateway, reader, client, state = bounded[1], bounded[2], bounded[3], bounded[5]
    original = gateway._completed

    def completed(task):
        original(task)
        if json.loads(task.result().body).get("operation") == "threads":
            if gap == "deadline":
                state["clock"] = 10
            elif gap == "withdrawal":
                state["active"] = False
            else:
                reader.response_limit //= 2
    gateway._completed = completed
    with pytest.raises(ReplayClientError) as caught:
        read(client)
    assert caught.value.http_status == status and state["calls"] == [("threads", 7)]


def test_v2_client_has_one_budget_including_auth_and_response_validation(bounded, monkeypatch):
    _, _, _, client, caller, state, _ = bounded
    original = caller.request

    async def slow(*args, **kwargs):
        if args[1].endswith("/diff"):
            await asyncio.sleep(.03)
        return await original(*args, **kwargs)
    caller.request = slow

    async def scenario():
        await client.capabilities()
        with pytest.raises(ReplayClientError, match="report_deadline_exhausted"):
            await client.read("diff", {"number": 7, "budget_seconds": .01})
    asyncio.run(scenario())
    assert not state["calls"]


def test_v2_client_validation_cannot_publish_after_original_budget(bounded, monkeypatch):
    from agent_pull_requests import report_client

    client = bounded[3]
    original = report_client.validate_exchange

    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs["operation"] == "diff":
            time.sleep(.03)
        return result
    monkeypatch.setattr(report_client, "validate_exchange", slow)

    async def scenario():
        await client.capabilities()
        with pytest.raises(ReplayClientError, match="report_deadline_exhausted"):
            await client.read("diff", {"number": 7, "budget_seconds": .01})
    asyncio.run(scenario())
    assert bounded[5]["calls"] == [("diff", 7)]
