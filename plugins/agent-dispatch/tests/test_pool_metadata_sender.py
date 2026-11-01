"""Original-request metadata transport with synthetic sources and real fences."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import nullcontext
from dataclasses import replace
from threading import Event
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_pool_catalog import _scope_payload, pool_catalog_contract
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.pool_publication_response import read_pool_metadata_result
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.registrations import RegistrationKind
from agent_dispatch.single_instance import SingletonAdmissionError
from tests.test_pool_publication_custody import _apply, _settle, owner as _owner_fixture
from tests.test_pool_source_collection import _declaration

owner = _owner_fixture


def _sender(daemon: Any, queue: Any) -> list[dict]:
    sent: list[dict] = []

    def send(request: Any, *, boundary: Any) -> Any:
        with boundary():
            sent.append(request.to_dict())
            return _apply(queue, request)

    daemon.client.send_pool_metadata = send
    return sent


def test_explicit_send_begin_publish_and_unavailable_preserve_all_attribution(owner: Any) -> None:
    daemon, queue, _calls = owner
    sent = _sender(daemon, queue)
    begin = daemon.prepare_pool_metadata_request()
    assert not sent and not queue.pool_catalog_snapshot().scopes
    enrolled = daemon.send_pool_metadata_request()
    assert enrolled.publisher_id == begin.inputs.publisher_id
    assert enrolled.state == "pending" and enrolled.revision == 0
    publish = daemon.prepare_pool_metadata_request()
    published = daemon.send_pool_metadata_request()
    assert published.definitions == publish.inputs.snapshot.definitions
    daemon.overrides_source = lambda: {"invalid": 1}
    unavailable = daemon.prepare_pool_metadata_request()
    result = daemon.send_pool_metadata_request()
    assert result.state == "unavailable" and unavailable.reason
    assert sent == [begin.to_dict(), publish.to_dict(), unavailable.to_dict()]
    assert sent[-1]["definitions"] == []
    assert result.definitions == published.definitions
    assert result.definitions_revision == published.definitions_revision
    assert result.definitions_generation == published.definitions_generation
    assert not queue.pool_catalog_snapshot().ready
    assert daemon._pool_publisher_custody.settled == (unavailable, result)


def test_send_does_not_prepare_or_replace_a_request(owner: Any) -> None:
    daemon, _queue, calls = owner
    with pytest.raises(PoolPublicationError, match="original prepared"):
        daemon.send_pool_metadata_request()
    assert not calls and daemon._pool_metadata_flight is None


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_lost_accepted_reply_recovers_original_even_after_inputs_and_lock_change(
    owner: Any, operation: str,
) -> None:
    daemon, queue, calls = owner
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    writes = []

    def lost(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            _apply(queue, request)
            raise httpx.ReadError("synthetic reply lost")

    daemon.client.send_pool_metadata = lost
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    first = queue.pool_catalog_snapshot().scopes[0]
    daemon.client.set_regs([])
    daemon.release_singleton()
    assert daemon.acquire_singleton()
    calls_before = len(calls)
    recovered = daemon.send_pool_metadata_request()
    assert recovered == first and len(calls) == calls_before + 1
    assert writes == [original] and request.to_dict() == original
    assert daemon._pool_publisher_custody.settled == (request, recovered)
    assert recovered.valid_until == first.valid_until
    with pytest.raises(PoolPublicationError, match="unsettled"):
        daemon.prepare_pool_metadata_request()


def test_unaccepted_reply_retries_same_request_only_under_original_fences(owner: Any) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    tries = 0
    writes = []

    def retry(request: Any, *, boundary: Any) -> Any:
        nonlocal tries
        tries += 1
        with boundary():
            writes.append(request.to_dict())
            if tries == 1:
                raise httpx.ConnectError("synthetic transport unavailable")
            return _apply(queue, request)

    daemon.client.send_pool_metadata = retry
    with pytest.raises(httpx.ConnectError):
        daemon.send_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is request
    result = daemon.send_pool_metadata_request()
    assert writes == [original, original] and result.generation == 1


def test_wrong_completed_ack_retains_request_then_recovers_actual_original(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    writes = []

    def wrong(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            return replace(_apply(queue, request), input_revision="wrong-reply")

    daemon.client.send_pool_metadata = wrong
    with pytest.raises(PoolPublicationError, match="original request fences"):
        daemon.send_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is request
    assert daemon._pool_metadata_flight is None
    result = daemon.send_pool_metadata_request()
    assert result.input_revision == request.input_revision
    assert writes == [request.to_dict()]


@pytest.mark.parametrize("change", ["declaration", "override", "registration", "publisher", "release"])
def test_original_fences_rechecked_after_transport_preflight_before_post(owner: Any, change: str) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    writes = []

    def send(request: Any, *, boundary: Any) -> Any:
        if change == "declaration":
            daemon.declared_source = lambda: DeclarationRead((_declaration(priority=4),), True)
        elif change == "override":
            daemon.overrides_source = lambda: {"logical:repo:example:worker": {"disabled": True}}
        elif change == "registration":
            queue.register_registration(
                RegistrationKind.SUPERVISED_LANE, {"all_repos": True}, reg_id="changed",
            )
        elif change == "publisher":
            queue.begin_pool_publication("host-a", "default", "successor", expected_generation=0)
        else:
            daemon.release_singleton()
        with boundary():
            writes.append(request.to_dict())
            return _apply(queue, request)

    daemon.client.send_pool_metadata = send
    with pytest.raises((PoolPublicationError, SingletonAdmissionError)):
        daemon.send_pool_metadata_request()
    assert writes == [] and daemon._pool_publisher_custody.pending is request
    assert request.to_dict() == original


def test_timeout_before_effect_keeps_future_and_expired_worker_cannot_send(owner: Any) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    started, finish = Event(), Event()
    read = daemon.client.pool_catalog_metadata

    def slow() -> dict:
        started.set()
        assert finish.wait(2)
        return read()

    daemon.client.pool_catalog_metadata = slow
    daemon._reconcile_call_budget = 0.03
    try:
        with pytest.raises(TimeoutError):
            daemon.send_pool_metadata_request()
        assert started.is_set()
        flight = daemon._pool_metadata_flight
        assert flight.future in daemon._probe_inflight and not flight.future.done()
        daemon.release_singleton()
        finish.set()
        with pytest.raises(SingletonAdmissionError):
            flight.future.result(timeout=1)
        assert not sent and daemon._pool_publisher_custody.pending is request
        assert not queue.pool_catalog_snapshot().scopes
    finally:
        finish.set()


def test_pending_post_survives_shutdown_and_late_result_only_settles_custody(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_daemon

    daemon, queue, calls = owner
    request = daemon.prepare_pool_metadata_request()
    started, finish = Event(), Event()
    writes = []

    def slow(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            result = _apply(queue, request)
            started.set()
            assert finish.wait(2)
            return result

    daemon.client.send_pool_metadata = slow
    daemon._reconcile_call_budget = 0.05
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    try:
        with pytest.raises((TimeoutError, PoolPublicationError)):
            daemon.send_pool_metadata_request()
        assert started.is_set()
        flight = daemon._pool_metadata_flight
        assert not daemon.shutdown()
        assert flight.future in daemon._probe_inflight
        assert daemon._retired_bounded_executors
        assert daemon._pool_publisher_custody.pending is request
        finish.set()
        result = flight.future.result(timeout=1)
        daemon.release_singleton()
        calls_before = len(calls)
        assert daemon.send_pool_metadata_request() == result
        assert len(calls) == calls_before and len(writes) == 1
        assert daemon._pool_publisher_custody.pending is None
        assert daemon._lock.acquisition is None
        with pytest.raises(SingletonAdmissionError):
            daemon.prepare_pool_metadata_request()
    finally:
        finish.set()


def test_shutdown_fences_delayed_preflight_even_with_original_lock_still_held(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_daemon

    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    started, finish = Event(), Event()
    read = daemon.client.pool_catalog_metadata

    def slow() -> dict:
        started.set()
        assert finish.wait(2)
        return read()

    daemon.client.pool_catalog_metadata = slow
    daemon._reconcile_call_budget = 1
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(daemon.send_pool_metadata_request)
        try:
            assert started.wait(1)
            assert not daemon.shutdown()
            assert daemon._pool_metadata_closed.is_set()
            finish.set()
            with pytest.raises(PoolPublicationError, match="admission is closed"):
                future.result(timeout=1)
            assert not sent and daemon._pool_publisher_custody.pending is request
            assert daemon._lock.acquisition == request.inputs.publisher_id
        finally:
            finish.set()


def test_two_callers_share_one_original_future_without_duplicate_post(owner: Any) -> None:
    daemon, queue, _calls = owner
    daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    started, finish, second = Event(), Event(), Event()
    read = daemon.client.pool_catalog_metadata
    reads = 0

    def slow() -> dict:
        nonlocal reads
        reads += 1
        if reads == 1:
            started.set()
            assert finish.wait(2)
        return read()

    daemon.client.pool_catalog_metadata = slow
    daemon._reconcile_call_budget = 1

    def another() -> Any:
        second.set()
        return daemon.send_pool_metadata_request()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(daemon.send_pool_metadata_request)
        assert started.wait(1)
        later = executor.submit(another)
        assert second.wait(1)
        finish.set()
        assert first.result(timeout=1) == later.result(timeout=1)
    assert len(sent) == 1 and daemon._pool_publisher_custody.pending is None


@pytest.mark.parametrize("field", [
    "version", "generation", "definitions_revision", "valid_until",
    "missing_freshness", "oversized_freshness", "definitions", "scope",
])
def test_malformed_response_never_becomes_acknowledgment(owner: Any, field: str) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    body = {"protocol": "pool_catalog_transport/v1", "version": 1, "scope": _scope_payload(result)}
    if field == "version":
        body["version"] = True
    elif field == "scope":
        body["scope"].pop("reason")
    else:
        body["scope"][field] = {
            "generation": True, "definitions_revision": True,
            "valid_until": float("nan"), "definitions": [{}, result.definitions[0].to_dict()],
            "missing_freshness": None, "oversized_freshness": 10 ** 1000,
        }[field]
        if field in ("missing_freshness", "oversized_freshness"):
            body["scope"]["valid_until"] = body["scope"].pop(field)
    with pytest.raises(PoolPublicationError):
        read_pool_metadata_result(body)
    assert daemon._pool_publisher_custody.pending is request


@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("supported", [False, True])
def test_real_client_negotiates_before_boundary_and_uses_original_control_body(
    owner: Any, operation: str, supported: bool,
) -> None:
    daemon, queue, _calls = owner
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    paths, admitted = [], []

    def boundary() -> Any:
        admitted.append(True)
        return nullcontext()

    def handler(wire: httpx.Request) -> httpx.Response:
        paths.append(wire.url.path)
        if wire.url.path == "/health":
            return httpx.Response(200, json={
                "pool_catalog_transport": pool_catalog_contract("control") if supported else None,
            })
        scheme = "Be" + "arer"
        assert wire.headers["Authorization"] == " ".join((scheme, "control"))
        assert json.loads(wire.content) == request.to_dict()
        result = _apply(queue, request)
        return httpx.Response(200, json={
            "protocol": "pool_catalog_transport/v1", "version": 1,
            "scope": {**_scope_payload(result), "additive": "accepted"},
        })

    with DispatchClient("http://example.test", control_token="control", transport=httpx.MockTransport(handler)) as client:
        if supported:
            result = client.send_pool_metadata(request, boundary=boundary)
            assert result.publisher_id == request.inputs.publisher_id
            assert paths == ["/health", f"/pool-catalog/scopes/{operation}"] and admitted == [True]
        else:
            with pytest.raises(DispatchUpgradeRequired):
                client.send_pool_metadata(request, boundary=boundary)
            assert paths == ["/health"] and not admitted


@pytest.mark.parametrize("status", [401, 403, 409, 422, 503])
def test_client_write_refusal_is_explicit_and_never_legacy_replayed(owner: Any, status: int) -> None:
    daemon, _queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    paths = []

    def handler(wire: httpx.Request) -> httpx.Response:
        paths.append(wire.url.path)
        if wire.url.path == "/health":
            return httpx.Response(200, json={"pool_catalog_transport": pool_catalog_contract("control")})
        return httpx.Response(status, json={"detail": {"code": "synthetic-refusal"}})

    with DispatchClient("http://example.test", control_token="control", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError) as caught:
            client.send_pool_metadata(request, boundary=nullcontext)
    assert caught.value.status_code == status
    assert paths == ["/health", "/pool-catalog/scopes/begin"]
    assert daemon._pool_publisher_custody.pending is request


def test_sender_round_trip_through_actual_provider_retains_unknown_publish(owner: Any) -> None:
    daemon, queue, _calls = owner
    server = TestClient(create_app(
        queue, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    lost = False
    writes = []

    def handler(wire: httpx.Request) -> httpx.Response:
        nonlocal lost
        response = server.request(
            wire.method, str(wire.url), headers=dict(wire.headers), content=wire.content,
        )
        if wire.method == "POST":
            writes.append(json.loads(wire.content))
            if wire.url.path.endswith("/publish") and not lost:
                lost = True
                assert response.status_code == 200
                raise httpx.ReadError("synthetic accepted reply lost")
        return httpx.Response(response.status_code, json=response.json())

    with server, DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(handler),
    ) as client:
        daemon.client.pool_catalog_metadata = client.pool_catalog_metadata
        daemon.client.send_pool_metadata = client.send_pool_metadata
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
        original = daemon.prepare_pool_metadata_request()
        with pytest.raises(httpx.ReadError):
            daemon.send_pool_metadata_request()
        before = queue.pool_catalog_snapshot()
        daemon.client.set_regs([])
        assert daemon.send_pool_metadata_request() == before.scopes[0]
        assert queue.pool_catalog_snapshot() == before
        assert writes[-1] == original.to_dict() and len(writes) == 2
        with queue._connect() as conn:
            assert conn.execute("SELECT count(*) FROM pool_publication_history").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM pool_publication_generations").fetchone()[0] == 1
