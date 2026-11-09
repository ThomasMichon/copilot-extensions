"""Isolated source report exchange; no real accounts, providers or retained hosts."""

import asyncio
from dataclasses import replace
import importlib.abc
import io
import json
import sys
import time
from types import MappingProxyType
from types import SimpleNamespace

import pytest
from fleet_manager.gateway_service_channel import BODY_BYTES, TransportError
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest, ServiceResponse

from agent_pull_requests import __main__ as cli
from agent_pull_requests.report_client import ReportClient
from agent_pull_requests.report_read import PROFILE, ReportBinding, ReportReader
from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests.watch_replay_client import ReplayClient, ReplayClientError
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore


@pytest.fixture
def reports(tmp_path):
    scope = {"provider": "github", "endpoint": "https://forge.example",
             "account": "account-a", "repo": "example/project"}
    consumer = ConsumerScope("consumer-a", scope["account"], scope["repo"])
    store = ReplayStore(
        (tmp_path / "replay").resolve(), "fleet/service/installation",
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"),
    )
    store.provision()
    binding = ReplayBinding("fleet-a", "gateway-a", "service-a", store.namespace,
                            "caller-a", "project", consumer.consumer, consumer.account, consumer.repo)
    state = {"calls": [], "active": True, "exit": 0, "change": None, "clock": 0}

    def report(operation, number):
        return {
            "schema": "agent-worktrees.pr-threads" if operation == "threads" else "agent-worktrees.pr-diff-read",
            "version": 1, "repo": scope["repo"], "pr": number, "provider": scope["provider"],
            "supported": True, "complete": None, "error": "",
            **({"threads": [{"comments": [{"text": "body\r\nλ\rtail", "future": {"x": [1]}}]}]}
               if operation == "threads" else {"diff": "diff --git a/x b/x\r\n+λ\rtail"}),
            "candidate_changed": True, "future": {"opaque": [None, False]},
        }

    def current():
        if not state["active"]:
            raise PermissionError("synthetic-secret-owner-withdrawn")

    def collect(operation, number, remaining):
        assert remaining() > 0
        state["calls"].append((operation, number))
        value = report(operation, number)
        if state["change"]:
            state["change"](value)
        return state["exit"], json.dumps(value, ensure_ascii=False)

    owner_binding = ReportBinding(scope, "owner-context", current, collect)
    state["binding"] = owner_binding

    def resolve(remaining):
        assert remaining() > 0
        state["resolved"] = state.get("resolved", 0) + 1
        return state["binding"]

    reader = ReportReader(consumer, scope, resolve, max_work_seconds=3, clock=lambda: state["clock"])
    gateway = WatchGateway(
        store, fleet=binding.fleet, gateway=binding.gateway, slot=binding.slot,
        bindings=(WatchScopeBinding(binding.principal, binding.target, consumer),
                  WatchScopeBinding("caller-b", binding.target,
                                    ConsumerScope("consumer-b", "account-b", scope["repo"]))),
        reports={(binding.principal, binding.target): reader},
    )

    class Caller:
        def __init__(self):
            self.calls, self.change = [], None
            self.handler_response_limit = BODY_BYTES
            self.principal, self.expires = binding.principal, int(time.time()) + 600

        async def request(self, method, target, *, request_headers, body, response_limit):
            self.calls.append(target)
            response = await gateway.handle(ServiceRequest(
                CallerContext(binding.fleet, binding.gateway, binding.slot, self.principal, self.expires),
                method, target, tuple((name.lower(), value) for name, value in request_headers), body,
                response_limit=self.handler_response_limit,
            ))
            if self.change is not None:
                return self.change(response)
            return response

    caller = Caller()
    client = ReportClient(caller, route_prefix="", binding=binding, scope=scope)
    return store, gateway, reader, client, caller, state, report


def read(client, operation="threads"):
    async def invoke():
        await client.capabilities()
        return await client.read(operation, {"number": 7, "budget_seconds": 2})
    return asyncio.run(invoke())


@pytest.mark.parametrize("operation", ["threads", "diff"])
def test_source_cli_preserves_full_report_and_native_exit(reports, monkeypatch, capsys, operation):
    store, gateway, _, client, caller, state, report = reports
    before = store.path.read_bytes()
    state["exit"] = 17
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b'{"number":7,"budget_seconds":2}')))
    assert cli.main(["report", operation, "--request", "-"], report_host=client) == 1
    value = json.loads(capsys.readouterr().out)
    assert value["report"] == report(operation, 7)
    assert value["owner_exit"] == 17 and value["profile"] == PROFILE
    assert value["binding"] == client.binding.document()
    assert caller.calls == [f"/v1/read/project/{verb}" for verb in ("capabilities", operation)]
    assert state["calls"] == [(operation, 7)]
    assert store.path.read_bytes() == before
    assert not any(name in value for name in ("generation", "principal_id", "source_attachment"))
    asyncio.run(gateway.drain(timeout=1))


@pytest.mark.parametrize("operation", ["threads", "diff"])
def test_v1_keeps_native_raw_json_allowance_when_encoded_exchange_fits(reports, operation):
    store, gateway, _, client, _, state, report = reports
    before = store.path.read_bytes()
    state["change"] = lambda value: value.update(future={"formatting": [{"x": 0}] * 1000})
    original = state["binding"].collect

    def pretty(*args):
        code, text = original(*args)
        text = json.dumps(json.loads(text), indent=4)
        assert BODY_BYTES < len(text.encode()) < 1_048_576
        return code, text

    state["binding"] = replace(state["binding"], collect=pretty)
    value = read(client, operation)
    expected = report(operation, 7)
    state["change"](expected)
    assert value["report"] == expected and value["profile"] == PROFILE
    assert len(json.dumps(value).encode()) < BODY_BYTES
    assert state["calls"] == [(operation, 7)] and store.path.read_bytes() == before
    asyncio.run(gateway.drain(timeout=1))


@pytest.mark.parametrize("change", [
    lambda value: value.update(supported=False, complete=False),
    lambda value: value.update(error="provider refusal", complete=False),
    lambda value: value.update(complete=False),
])
def test_valid_partial_and_refused_reports_remain_whole(reports, change):
    *_, state, report = reports
    state["change"] = change
    value = read(reports[3])
    expected = report("threads", 7)
    change(expected)
    assert value["report"] == expected


@pytest.mark.parametrize("change", [
    lambda value: value.update(version=True),
    lambda value: value.update(pr=True),
    lambda value: value.update(repo="other/project"),
    lambda value: value.update(provider="other"),
    lambda value: value.update(supported=1),
    lambda value: value.update(complete="unknown"),
    lambda value: value.update(threads=[None]),
    lambda value: value.update(error=None),
    lambda value: value.update(future=float("nan")),
])
def test_malformed_native_report_is_unavailable_not_an_empty_success(reports, change):
    reports[5]["change"] = change
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == 503
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("payload", [
    {"number": True, "budget_seconds": 2}, {"number": 0, "budget_seconds": 2},
    {"number": 7, "budget_seconds": True}, {"number": 7, "budget_seconds": float("inf")},
    {"number": 7, "budget_seconds": 0}, {"number": 7, "budget_seconds": 2, "account": "other"},
    {"number": 7, "budget_seconds": 2, "command": "/other"},
])
def test_wire_overrides_refuse_before_owner_resolution(reports, payload):
    store, gateway, _, client, _, state, _ = reports
    response = asyncio.run(gateway.handle(ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", int(time.time()) + 60),
        "POST", "/v1/read/project/threads", (("content-type", "application/json"),),
        json.dumps({"binding": {**client.binding.document(), "report_scope": client.scope},
                    "request": payload}).encode(),
    )))
    assert response.status == 400
    assert state["calls"] == [] and state.get("resolved", 0) == 0


@pytest.mark.parametrize("change,status", [
    ("account", 503), ("endpoint", 503), ("withdrawn", 403),
    ("caller", 404), ("expired", 403), ("binding", 409), ("owner", 503),
])
def test_actual_scope_lifetime_and_owner_selection_precede_acquisition(reports, change, status):
    _, gateway, reader, client, caller, state, _ = reports
    if change in ("account", "endpoint"):
        state["binding"] = replace(state["binding"], scope={**dict(reader.scope), change: "different"})
    elif change == "withdrawn":
        state["active"] = False
    elif change == "caller":
        caller.principal = "caller-b"
    elif change == "expired":
        caller.expires = 1
    elif change == "binding":
        client.binding = replace(client.binding, namespace="other")
    elif change == "owner":
        def replaced():
            gateway.reports = MappingProxyType({})
        state["binding"] = replace(state["binding"], checkpoint=replaced)
    with pytest.raises(ReplayClientError) as caught:
        read(client)
    assert caught.value.http_status == status and not state["calls"]


@pytest.mark.parametrize("change,status", [("withdrawn", 403), ("deadline", 503)])
def test_post_acquisition_changes_prevent_publication(reports, change, status):
    state = reports[5]
    def during(value):
        if change == "withdrawn":
            state["active"] = False
        else:
            state["clock"] = 10
    state["change"] = during
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == status
    assert state["calls"] == [("threads", 7)]


@pytest.mark.parametrize("field,value", [("endpoint", "https://other.example"), ("provider", "gitea")])
def test_replacement_report_scope_refuses_before_new_owner_resolution_or_collection(reports, field, value):
    _, gateway, reader, client, _, state, _ = reports
    async def scenario():
        await client.capabilities()
        resolved = state["resolved"]
        changed = {**dict(reader.scope), field: value}
        state["binding"] = replace(state["binding"], scope=changed)
        gateway.reports = {(client.binding.principal, client.binding.target): ReportReader(
            reader.consumer_scope, changed, reader.resolve_binding, max_work_seconds=3,
        )}
        with pytest.raises(ReplayClientError) as caught:
            await client.read("diff", {"number": 7, "budget_seconds": 2})
        assert caught.value.http_status == 409
        assert state["resolved"] == resolved
    asyncio.run(scenario())
    assert not state["calls"]


def test_full_response_limit_never_truncates_native_fields(reports):
    reports[5]["change"] = lambda value: value.update(future="x" * BODY_BYTES)
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == 503
    assert reports[5]["calls"] == [("threads", 7)]


def test_unexpected_owner_failure_is_sanitized(reports):
    def broken(operation, number, remaining):
        raise TypeError("synthetic-secret-owner-diagnostic")
    reports[5]["binding"] = replace(reports[5]["binding"], collect=broken)
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == 503
    assert str(caught.value) == "report_unavailable"


def test_client_refuses_over_limit_response_without_persisting_or_truncating(reports):
    def oversized(response):
        if '"report":' not in response.body.decode():
            return response
        return SimpleNamespace(status=200, headers=response.headers, body=b" " * (BODY_BYTES + 1))
    reports[4].change = oversized
    with pytest.raises(ReplayClientError, match="invalid_report_response"):
        read(reports[3])


@pytest.mark.parametrize("change", [
    lambda value: value.update(profile="owner_executed_review_evidence/v1"),
    lambda value: value.update(binding={}),
    lambda value: value.update(number=True),
    lambda value: value.update(owner_exit=False),
    lambda value: value.update(observed_to=-1),
    lambda value: value.update(scope={**value["scope"], "endpoint": "other"}),
    lambda value: value["report"].update(provider="other"),
])
def test_client_validates_exchange_independently(reports, change):
    caller = reports[4]
    def malformed(response):
        value = json.loads(response.body)
        if "report" in value:
            change(value)
        return replace(response, body=json.dumps(value).encode())
    caller.change = malformed
    with pytest.raises(ReplayClientError, match="invalid_report_response"):
        read(reports[3])


def test_optional_rollback_refuses_without_fallback_and_keeps_legacy_watch(reports):
    _, gateway, _, client, caller, state, _ = reports
    async def scenario():
        with pytest.raises(ReplayClientError, match="capability_required"):
            await client.read("diff", {"number": 7, "budget_seconds": 1})
        await client.capabilities()
        gateway.reports = MappingProxyType({})
        with pytest.raises(ReplayClientError) as caught:
            await client.read("diff", {"number": 7, "budget_seconds": 1})
        assert caught.value.http_status == 404
        with pytest.raises(ReplayClientError, match="capability_required"):
            await client.read("diff", {"number": 7, "budget_seconds": 1})
        legacy = ReplayClient(caller, route_prefix="", target="project",
                              repo=client.binding.repo, binding=client.binding)
        assert (await legacy.capabilities()).binding == client.binding
    asyncio.run(scenario())
    assert not state["calls"]


def test_old_receiver_and_invalid_declaration_do_not_dispatch_report(reports):
    caller, client, state = reports[4], reports[3], reports[5]
    caller.change = lambda response: ServiceResponse(404)
    with pytest.raises(ReplayClientError) as caught:
        read(client)
    assert caught.value.code == "report_unsupported"
    assert len(caller.calls) == 1 and not state["calls"]
    caller.calls.clear()
    caller.change = lambda response: replace(response, body=json.dumps({
        **json.loads(response.body), "protocol": "old",
    }).encode())
    with pytest.raises(ReplayClientError, match="invalid_report_response"):
        read(client)
    assert len(caller.calls) == 1 and not state["calls"]


@pytest.mark.parametrize("failure", ["transport", "http", "invalid"])
def test_stale_discovery_failure_cannot_clear_a_newer_successful_selection(reports, failure):
    _, _, _, client, caller, state, _ = reports

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        count = 0
        class Overlap:
            async def request(self, *args, **kwargs):
                nonlocal count
                count += 1
                if count == 1:
                    started.set()
                    await release.wait()
                    if failure == "transport":
                        raise TransportError("synthetic-stale-failure")
                    return ServiceResponse(404) if failure == "http" else ServiceResponse(
                        200, (("content-type", "application/json"),), b"{}",
                    )
                return await caller.request(*args, **kwargs)
        client.caller = Overlap()
        old = asyncio.create_task(client.capabilities())
        await started.wait()
        await client.capabilities()
        release.set()
        with pytest.raises(ReplayClientError, match="report_discovery_superseded"):
            await old
        assert (await client.read("diff", {"number": 7, "budget_seconds": 2}))["owner_exit"] == 0
    asyncio.run(scenario())
    assert state["calls"] == [("diff", 7)]


@pytest.mark.parametrize("gap,status", [("deadline", 503), ("withdrawal", 403)])
def test_publication_is_rechecked_after_worker_finishes_before_coroutine_resumes(
    reports, monkeypatch, gap, status,
):
    reader, state = reports[2], reports[5]
    original = asyncio.to_thread
    async def delayed(function, *args, **kwargs):
        result = await original(function, *args, **kwargs)
        if function == reader.execute and args[0] == "threads":
            if gap == "deadline":
                state["clock"] = 10
            else:
                state["active"] = False
        return result
    monkeypatch.setattr(asyncio, "to_thread", delayed)
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == status
    assert state["calls"] == [("threads", 7)]


@pytest.mark.parametrize("gap,status", [("deadline", 503), ("withdrawal", 403)])
def test_publication_is_rechecked_after_prepared_response_handoff(reports, monkeypatch, gap, status):
    state = reports[5]
    original = asyncio.shield
    async def delayed(awaitable):
        result = await original(awaitable)
        if not isinstance(awaitable, asyncio.Task) and json.loads(result.body).get("operation") == "threads":
            if gap == "deadline":
                state["clock"] = 10
            else:
                state["active"] = False
        return result
    monkeypatch.setattr(asyncio, "shield", delayed)
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == status
    assert state["calls"] == [("threads", 7)]


@pytest.mark.parametrize("gap,status", [
    ("deadline", 503), ("withdrawal", 403), ("binding", 409), ("owner", 503),
])
def test_publication_is_rechecked_after_shielded_task_completion(reports, gap, status):
    gateway, reader, state = reports[1], reports[2], reports[5]
    original = gateway._completed
    def completed(task):
        original(task)
        if json.loads(task.result().body).get("operation") == "threads":
            if gap == "deadline":
                state["clock"] = 10
            elif gap == "withdrawal":
                state["active"] = False
            elif gap == "binding":
                gateway.bindings = {}
            else:
                gateway.reports = {("caller-a", "project"): ReportReader(
                    reader.consumer_scope, reader.scope, reader.resolve_binding, max_work_seconds=3,
                )}
    gateway._completed = completed
    with pytest.raises(ReplayClientError) as caught:
        read(reports[3])
    assert caught.value.http_status == status
    assert state["calls"] == [("threads", 7)]


@pytest.mark.parametrize("argv", [
    ["report", "diff", "--request", "-", "--account", "other"],
    ["report", "diff", "--req", "-"], ["report", "diff", "--request", "-", "--resolve"],
])
def test_strict_cli_unknown_flags_refuse_without_host(argv):
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


def test_native_parser_and_unbound_report_need_no_optional_fleet(monkeypatch, capsys):
    class NoFleet(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "fleet_manager" or fullname.startswith("fleet_manager."):
                raise ModuleNotFoundError("optional fleet deliberately absent")
    for name in tuple(sys.modules):
        if name == "fleet_manager" or name.startswith("fleet_manager."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, "agent_pull_requests.report_cli", raising=False)
    monkeypatch.setattr(sys, "meta_path", [NoFleet(), *sys.meta_path])
    assert cli.build_parser().parse_args(["diff", "--repo", "example/project", "--number", "7",
                                         "--worktrees-command", sys.executable]).command == "diff"
    assert cli.main(["report", "capabilities"]) == 3
    assert json.loads(capsys.readouterr().out)["error"] == "report_owner_unavailable"
