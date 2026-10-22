"""Configured Gateway caller/scope binding and cancellation custody."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import replace

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest
from single_instance_lease import OperationLock, OperationLockedError

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore

SCOPE = ConsumerScope("consumer-a", "account-a", "example/project")
OTHER = ConsumerScope("consumer-b", "account-b", "example/project")


@pytest.fixture
def gateway(tmp_path):
    store = ReplayStore(tmp_path.resolve(), "fleet/service/installation")
    store.provision()
    return WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=(
            WatchScopeBinding("caller-a", "project", SCOPE),
            WatchScopeBinding("caller-b", "project", OTHER),
        ),
    )


def request(operation="pending", payload=None, *, caller="caller-a", **kwargs):
    result = ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", caller, int(time.time()) + 60),
        "GET" if operation == "capabilities" else "POST", f"/v1/watch/project/{operation}",
        (("content-type", "application/json"),),
        json.dumps(payload).encode() if payload is not None else b"",
    )
    return replace(result, **kwargs)


def registration():
    return {"request_id": "original", "issued_at": time.time(), "number": 1, "until": ["merged"]}


def call(gateway, *args, **kwargs):
    async def scenario():
        response = await gateway.handle(request(*args, **kwargs))
        await gateway.drain(timeout=1)
        return response.status, json.loads(response.body)
    return asyncio.run(scenario())


def test_only_configured_scope_is_used_and_caps_do_not_expose_inventory(gateway):
    status, value = call(gateway, "capabilities")
    assert status == 200
    assert value["protocol"] == "agent-pull-requests.watch/v1"
    assert set(value) == {
        "protocol", "operations", "max_batch", "max_batch_bytes", "receipt_retention_seconds",
    }
    assert value["max_batch"] == 16 and value["max_batch_bytes"] == 24 * 1024
    assert value["receipt_retention_seconds"] == 86400
    assert not gateway.store.path.name == "watch-subscriptions.json"


@pytest.mark.parametrize("field", [
    "principal", "consumer", "account", "repo", "namespace", "notify", "argv", "delivery_url",
])
def test_body_identity_and_callback_fields_are_refused_without_commit(gateway, field):
    before = gateway.store.path.read_bytes()
    status, value = call(gateway, "register", {**registration(), field: "foreign"})
    assert (status, value) == (400, {"error": "invalid_watch_request"})
    assert gateway.store.path.read_bytes() == before


@pytest.mark.parametrize("change,status", [
    ({"target": "/v1/watch/project/pending?principal=caller-b"}, 404),
    ({"target": "/v1/watch/%70roject/pending"}, 404),
    ({"target": "/v1/watch/foreign/pending"}, 403),
    ({"method": "GET"}, 405),
    ({"body": b'{"limit":NaN}'}, 400),
    ({"body": b'{"limit":1,"limit":2}'}, 400),
    ({"body": b'[]'}, 400),
    ({"body": b'{"limit":true}'}, 400),
    ({"body": b'{"limit":17}'}, 400),
    ({"body": b"x" * (32 * 1024 + 1)}, 413),
    ({"headers": (("content-type", "text/plain"),), "body": b"{}"}, 415),
])
def test_strict_bounded_routes_and_json(gateway, change, status):
    before = gateway.store.path.read_bytes()
    assert call(gateway, **change)[0] == status
    assert gateway.store.path.read_bytes() == before


@pytest.mark.parametrize("field", ["fleet", "gateway", "slot", "principal", "expires_at"])
def test_foreign_or_expired_context_cannot_reach_store(gateway, field):
    incoming = request("register", registration())
    value = 1 if field == "expires_at" else "foreign"
    status, _ = call(gateway, context=replace(incoming.context, **{field: value}))
    assert status == 403
    assert json.loads(gateway.store.path.read_bytes())["records"] == []


def test_real_store_receipts_are_isolated_by_admitted_subject(gateway):
    async def scenario():
        original = registration()
        original["deadline_at"] = time.time() - 1

        async def send(operation, body=None, **kwargs):
            response = await gateway.handle(request(operation, body, **kwargs))
            assert response.status == 200
            return json.loads(response.body)

        receipt = await send("register", original)
        assert await send("register", original) == receipt
        inspect = {key: original[key] for key in ("request_id", "issued_at")}
        assert await send("inspect", inspect, caller="caller-b") == {"status": "uncertain"}
        assert await send("pending", caller="caller-b") == {"events": [], "more": False}
        pending = await send("pending")
        assert pending["events"][0]["timed_out"] is True
        completion = dict(inspect, registration_id=receipt["registration_id"],
                          event_id=pending["events"][0]["event_id"])
        assert await send("acknowledge", completion, caller="caller-b") == {"status": "uncertain"}
        assert await send("pending") == pending
        ack = await send("acknowledge", completion)
        assert ack["status"] == "acknowledged"
        assert await send("acknowledge", completion) == ack
        assert await send("pending") == {"events": [], "more": False}
        records = json.loads(gateway.store.path.read_bytes())["records"]
        assert len(records) == 1 and records[0]["scope"] == vars(SCOPE)
        await gateway.drain(timeout=1)
    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_corrupt_store_returns_uncertainty_not_new_acceptance(gateway, missing):
    if missing:
        gateway.store.path.unlink()
    else:
        gateway.store.path.write_text("private-invalid-data")
    status, value = call(gateway, "register", registration())
    assert (status, value) == (
        503, {"status": "uncertain", "reason": "watch_store_unavailable"},
    )
    assert not gateway.store.path.exists() if missing else (
        gateway.store.path.read_text() == "private-invalid-data"
    )


def test_cancelled_request_keeps_transaction_custody_and_drain_waits(gateway, monkeypatch):
    async def scenario():
        started, release = threading.Event(), threading.Event()
        execute = gateway.store.execute

        def held(*args, **kwargs):
            # Hold real custody while simulating an in-progress durable operation.
            with OperationLock(gateway.store.root / "watch-replay.lock"):
                started.set()
                assert release.wait(5)
            return execute(*args, **kwargs)

        monkeypatch.setattr(gateway.store, "execute", held)
        pending = asyncio.create_task(gateway.handle(request("register", registration())))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            with pytest.raises(OperationLockedError):
                with OperationLock(gateway.store.root / "watch-replay.lock"):
                    pass
            with pytest.raises(TimeoutError, match="not drained"):
                await gateway.drain(timeout=0.01)
            response = await gateway.handle(request())
            assert response.status == 503
            assert json.loads(response.body) == {"error": "watch_service_draining"}
            release.set()
            await gateway.drain(timeout=3)
            assert len(json.loads(gateway.store.path.read_bytes())["records"]) == 1
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await gateway.drain(timeout=3)
    asyncio.run(scenario())


def test_duplicate_or_mutable_policy_is_refused(gateway):
    kwargs = dict(fleet="fleet-a", gateway="gateway-a", slot="service-a")
    binding = WatchScopeBinding("caller-a", "project", SCOPE)
    with pytest.raises(ValueError, match="duplicate"):
        WatchGateway(gateway.store, bindings=(binding, binding), **kwargs)
    with pytest.raises(ValueError, match="immutable"):
        WatchGateway(gateway.store, bindings=[binding], **kwargs)
    with pytest.raises(TypeError):
        gateway.bindings[("caller-a", "project")] = OTHER


@pytest.mark.parametrize("change", [
    {"request_id": ""}, {"request_id": []}, {"issued_at": True},
    {"issued_at": 10**400}, {"number": True}, {"until": "merged"},
    {"until": ["unknown"]}, {"until": ["merged", "merged"]}, {"deadline_at": False},
])
def test_invalid_registration_never_commits(gateway, change):
    before = gateway.store.path.read_bytes()
    assert call(gateway, "register", dict(registration(), **change)) == (
        400, {"error": "invalid_watch_request"},
    )
    assert gateway.store.path.read_bytes() == before


@pytest.mark.parametrize("deadline", [2**53 + 1, 10**400])
def test_deadline_precision_refusal_keeps_other_admitted_caller_usable(gateway, deadline):
    async def scenario():
        original = registration()
        accepted = await gateway.handle(request("register", original, caller="caller-b"))
        assert accepted.status == 200
        receipt = json.loads(accepted.body)
        before = gateway.store.path.read_bytes()
        refused = await gateway.handle(request("register", dict(original, deadline_at=deadline)))
        assert refused.status == 400
        assert gateway.store.path.read_bytes() == before
        inspect = {key: original[key] for key in ("request_id", "issued_at")}
        response = await gateway.handle(request("inspect", inspect, caller="caller-b"))
        assert response.status == 200 and json.loads(response.body) == receipt
        pending = await gateway.handle(request(caller="caller-b"))
        assert pending.status == 200 and json.loads(pending.body) == {"events": [], "more": False}
        await gateway.drain(timeout=1)
    asyncio.run(scenario())


def test_work_is_bounded_per_channel_without_queuing_another_disk_worker(gateway, monkeypatch):
    async def scenario():
        release = threading.Event()
        calls = []

        def held(*args, **kwargs):
            calls.append(True)
            assert release.wait(5)
            return {"events": [], "more": False}

        monkeypatch.setattr(gateway.store, "execute", held)
        tasks = [asyncio.create_task(gateway.handle(request())) for _ in range(8)]
        try:
            await asyncio.sleep(0)
            response = await gateway.handle(request())
            assert response.status == 503
            assert json.loads(response.body) == {"error": "watch_service_busy"}
            assert len(gateway._tasks) == 8
            release.set()
            assert all(response.status == 200 for response in await asyncio.gather(*tasks))
            assert len(calls) == 8
            await gateway.drain(timeout=3)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await gateway.drain(timeout=3)
    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_drain_budget_does_not_change_admission(gateway, timeout):
    async def scenario():
        with pytest.raises(ValueError):
            await gateway.drain(timeout=timeout)
        response = await gateway.handle(request("capabilities"))
        assert response.status == 200
        await gateway.drain(timeout=1)
    asyncio.run(scenario())
