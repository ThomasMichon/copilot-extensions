"""Scoped selection refuses remapping before replay work, without changing v1."""

import asyncio
import json
import time
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_binding import SCOPED_PROTOCOL, ReplayBinding
from agent_pull_requests.watch_replay_client import ReplayClient, ReplayClientError, ReplayRequest
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore
from test_watch_replay_client import CAPABILITIES, RECEIPT, Caller

BINDING = ReplayBinding(
    "fleet-a", "gateway-a", "service-a", "installation-a", "caller-a", "project",
    "consumer-a", "account-a", "example/project",
)


@pytest.fixture
def gateway(tmp_path):
    store = ReplayStore(
        tmp_path.resolve(), BINDING.namespace,
        service_identity=ReplayServiceIdentity(BINDING.fleet, BINDING.gateway, BINDING.slot),
    )
    store.provision()
    return WatchGateway(
        store, fleet=BINDING.fleet, gateway=BINDING.gateway, slot=BINDING.slot,
        bindings=(WatchScopeBinding(
            BINDING.principal, BINDING.target,
            ConsumerScope(BINDING.consumer, BINDING.account, BINDING.repo),
        ),),
    )


def request(operation, *, binding=BINDING, payload=None, version="v2"):
    body = {"binding": binding.document(), "request": payload or {}}
    return ServiceRequest(
        CallerContext(BINDING.fleet, BINDING.gateway, BINDING.slot, BINDING.principal,
                      int(time.time()) + 60),
        "GET" if operation == "capabilities" else "POST",
        f"/{version}/watch/{BINDING.target}/{operation}",
        (("content-type", "application/json"),),
        b"" if operation == "capabilities" else json.dumps(body).encode(),
    )


def call(gateway, incoming):
    async def scenario():
        response = await gateway.handle(incoming)
        await gateway.drain(timeout=1)
        return response.status, json.loads(response.body)
    return asyncio.run(scenario())


def registration():
    return {"request_id": "original", "issued_at": time.time(), "number": 1, "until": ["merged"]}


def test_scoped_capabilities_describe_only_selected_binding_and_preserve_v1(gateway):
    async def scenario():
        for version in ("v1", "v2"):
            response = await gateway.handle(request("capabilities", version=version))
            expected = dict(CAPABILITIES)
            if version == "v2":
                expected.update(protocol=SCOPED_PROTOCOL, binding=BINDING.document())
            assert response.status == 200 and json.loads(response.body) == expected
        await gateway.drain(timeout=1)
    asyncio.run(scenario())


@pytest.mark.parametrize("field", list(BINDING.document()))
def test_each_wrong_expected_dimension_refuses_before_any_storage(gateway, monkeypatch, field):
    def forbidden(*args, **kwargs):
        raise AssertionError("mismatched binding reached storage")

    monkeypatch.setattr(gateway.store, "execute", forbidden)
    before = gateway.store.path.read_bytes()
    incoming = request("register", binding=replace(BINDING, **{field: "foreign"}),
                       payload=registration())
    assert call(gateway, incoming) == (409, {"error": "watch_binding_mismatch"})
    assert gateway.store.path.read_bytes() == before


@pytest.mark.parametrize("operation", ["register", "inspect", "pending", "acknowledge", "cancel"])
def test_every_post_requires_binding_even_for_lookup_and_timeout_sweep(gateway, monkeypatch, operation):
    def forbidden(*args, **kwargs):
        raise AssertionError("unbound operation reached storage")

    monkeypatch.setattr(gateway.store, "execute", forbidden)
    payload = {"request_id": "original", "issued_at": 100, "registration_id": "reg", "event_id": "evt"}
    payload = {
        "register": registration(), "inspect": {k: payload[k] for k in ("request_id", "issued_at")},
        "pending": {}, "cancel": {k: v for k, v in payload.items() if k != "event_id"},
        "acknowledge": payload,
    }[operation]
    incoming = request(operation, binding=replace(BINDING, namespace="foreign"), payload=payload)
    assert call(gateway, incoming) == (409, {"error": "watch_binding_mismatch"})


@pytest.mark.parametrize("body", [
    {}, {"request": {}}, {"binding": BINDING.document()}, {"binding": {}, "request": {}},
    {"binding": BINDING.document(), "request": []},
    {"binding": dict(BINDING.document(), extra="foreign"), "request": {}},
    {"binding": dict(BINDING.document(), account=1), "request": {}},
    {"binding": BINDING.document(), "request": {}, "extra": True},
])
def test_malformed_scoped_envelopes_are_not_legacy_requests(gateway, body):
    before = gateway.store.path.read_bytes()
    assert call(gateway, replace(request("pending"), body=json.dumps(body).encode())) == (
        400, {"error": "invalid_watch_request"},
    )
    assert gateway.store.path.read_bytes() == before


@pytest.mark.parametrize("change", ["scope", "namespace", "identity"])
def test_selection_is_rechecked_under_transaction_custody_before_decode(gateway, monkeypatch, change):
    lock = gateway.store._lock
    before = gateway.store.path.read_bytes()

    @contextmanager
    def changed_lock():
        with lock() as held:
            if change == "scope":
                gateway.bindings = MappingProxyType({
                    (BINDING.principal, BINDING.target):
                    ConsumerScope("other", BINDING.account, BINDING.repo),
                })
            elif change == "namespace":
                gateway.store.namespace = "other"
            else:
                gateway.store.service_identity = replace(gateway.store.service_identity, slot="other")
            yield held

    def forbidden(*args, **kwargs):
        raise AssertionError("changed binding reached disk decode")

    monkeypatch.setattr(gateway.store, "_lock", changed_lock)
    monkeypatch.setattr(gateway.store, "_decode", forbidden)
    assert call(gateway, request("register", payload=registration())) == (
        409, {"error": "watch_binding_mismatch"},
    )
    assert gateway.store.path.read_bytes() == before


def test_scoped_operations_refuse_legacy_unbound_volume(gateway):
    gateway.store.service_identity = None
    assert call(gateway, request("capabilities")) == (409, {"error": "watch_binding_mismatch"})


@pytest.mark.parametrize("field", ["service", "namespace"])
def test_valid_expected_binding_cannot_adopt_foreign_persisted_identity(gateway, field):
    state = json.loads(gateway.store.path.read_bytes())
    if field == "service":
        state["service"]["slot"] = "foreign"
    else:
        state["replay"]["namespace"] = "foreign"
    gateway.store.path.write_text(json.dumps(state))
    before = gateway.store.path.read_bytes()
    assert call(gateway, request("register", payload=registration())) == (
        503, {"status": "uncertain", "reason": "watch_store_unavailable"},
    )
    assert gateway.store.path.read_bytes() == before


def scoped_client(caller, *, binding=BINDING):
    return ReplayClient(caller, route_prefix="/api", target=BINDING.target,
                        repo=BINDING.repo, binding=binding)


def test_scoped_client_preserves_original_request_and_all_operation_payloads():
    async def scenario():
        caller = Caller(dict(CAPABILITIES, protocol=SCOPED_PROTOCOL, binding=BINDING.document()))
        selected = scoped_client(caller)
        capabilities = await selected.capabilities()
        assert capabilities.binding == BINDING and capabilities.protocol == SCOPED_PROTOCOL
        original = ReplayRequest("original", 100)
        caller.response = Caller(RECEIPT).response
        await selected.register(original, number=1, until=("merged",))
        await selected.inspect(original)
        caller.response = Caller({"events": [], "more": False}).response
        await selected.pending()
        caller.response = Caller(dict(RECEIPT, status="acknowledged", event_id="event")).response
        await selected.acknowledge(original, registration_id="registration", event_id="event")
        caller.response = Caller(dict(RECEIPT, status="cancelled")).response
        await selected.cancel(original, registration_id="registration")
        assert len(caller.calls) == 6
        assert caller.calls[0][0] == ("GET", "/api/v2/watch/project/capabilities")
        assert caller.calls[0][1]["body"] == b""
        for args, kwargs in caller.calls[1:]:
            assert args[0] == "POST" and args[1].startswith("/api/v2/watch/project/")
            envelope = json.loads(kwargs["body"])
            assert set(envelope) == {"binding", "request"}
            assert envelope["binding"] == BINDING.document()
            if not args[1].endswith("/pending"):
                assert envelope["request"]["request_id"] == original.request_id
                assert envelope["request"]["issued_at"] == original.issued_at
        with pytest.raises(FrozenInstanceError):
            capabilities.binding.namespace = "replacement"
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [
    CAPABILITIES, dict(CAPABILITIES, protocol=SCOPED_PROTOCOL),
    dict(CAPABILITIES, protocol=SCOPED_PROTOCOL, binding=dict(BINDING.document(), account="other")),
    dict(CAPABILITIES, protocol=SCOPED_PROTOCOL, binding=dict(BINDING.document(), extra=True)),
])
def test_scoped_client_refuses_legacy_or_different_capabilities_without_downgrade(value):
    caller = Caller(value)
    with pytest.raises(ReplayClientError, match="invalid_watch_capabilities"):
        asyncio.run(scoped_client(caller).capabilities())
    assert len(caller.calls) == 1


@pytest.mark.parametrize("status", [404, 409, 503])
def test_rollback_and_binding_refusal_never_retry_or_erase_prior_uncertainty(status):
    caller = Caller({"error": "private-value"}, status=status)
    with pytest.raises(ReplayClientError) as caught:
        asyncio.run(scoped_client(caller).register(
            ReplayRequest("original", 100), number=1, until=("merged",),
        ))
    assert caught.value.http_status == status and caught.value.outcome_unknown
    assert len(caller.calls) == 1 and "private" not in str(caught.value)


def test_binding_input_is_strict_detached_and_consistent_with_selected_route():
    document = BINDING.document()
    binding = ReplayBinding.from_document(document)
    document["namespace"] = "replacement"
    assert binding == BINDING
    for bad in (None, {}, dict(BINDING.document(), namespace=""), dict(BINDING.document(), target="../")):
        with pytest.raises(ValueError):
            ReplayBinding.from_document(bad)
    for bad in (BINDING.document(), replace(BINDING, target="other"), replace(BINDING, repo="other")):
        with pytest.raises(ValueError):
            scoped_client(Caller(), binding=bad)
