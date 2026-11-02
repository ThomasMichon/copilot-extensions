"""Original transport and pre-effect supersession through existing owners."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from threading import Event
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.pool_cohort_preparation import PoolCohortPreparation
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.registrar_discovery import RegistrarSources
from agent_dispatch.supervisor_retirement import finish_serving
from tests.test_pool_cohort_preparation import _write
from tests.test_pool_metadata_sender import _sender
from tests.test_pool_publication_custody import owner as _owner_fixture

owner = _owner_fixture


@pytest.fixture
def wire_owner(owner: Any) -> Any:
    daemon, queue, _calls = owner
    paths: list[tuple[str, str, str]] = []
    gate: dict[str, Any] = {}
    with TestClient(create_app(
        queue, token="data", control_token="control", enable_mcp=False,
        verification_interval=0,
    )) as server:
        def handler(wire: httpx.Request) -> httpx.Response:
            paths.append((wire.url.host, wire.method, wire.url.path))
            hook = gate.get("before")
            if hook is not None:
                hook(wire)
            response = server.request(
                wire.method, str(wire.url), headers=dict(wire.headers), content=wire.content,
            )
            hook = gate.get("after")
            if hook is not None:
                hook(wire, response)
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://original.test", token="data", control_token="control",
            transport=httpx.MockTransport(handler),
        ) as original, DispatchClient(
            "http://replacement.test", token="data", control_token="control",
            transport=httpx.MockTransport(handler),
        ) as replacement:
            daemon.client = original
            yield daemon, queue, original, replacement, paths, gate


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_lost_reply_reconnect_recovers_only_original_client_and_closes_it_after_ack(
    wire_owner: Any, operation: str,
) -> None:
    daemon, queue, original, replacement, paths, gate = wire_owner
    if operation == "publish":
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
    request = daemon.prepare_pool_metadata_request()
    bodies = []

    def lose(wire: httpx.Request, response: Any) -> None:
        if wire.method == "POST":
            assert response.status_code == 200
            bodies.append(json.loads(wire.content))
            raise httpx.ReadError("synthetic original accepted reply lost")

    gate["after"] = lose
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    expected = queue.pool_catalog_snapshot().scopes[0]
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect() and daemon.client is replacement
    assert not original._http.is_closed
    paths.clear()
    result = daemon.send_pool_metadata_request()
    assert result == expected and bodies == [request.to_dict()]
    assert paths == [
        ("original.test", "GET", "/health"), ("original.test", "GET", "/pool-catalog"),
    ]
    assert original._http.is_closed and not replacement._http.is_closed
    assert daemon._pool_publisher_custody.settled == (request, expected)
    paths.clear()
    with pytest.raises(PoolPublicationError, match="target changed after settlement"):
        daemon.prepare_pool_metadata_request()
    assert not paths


def test_inflight_original_post_survives_reconnect_and_stays_in_shutdown_inventory(
    wire_owner: Any,
) -> None:
    daemon, queue, original, replacement, paths, gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    entered, finish = Event(), Event()

    def block(wire: httpx.Request, response: Any) -> None:
        if wire.method == "POST":
            assert response.status_code == 200
            entered.set()
            assert finish.wait(3)

    gate["after"] = block
    daemon._reconcile_call_budget = 0.1
    try:
        with pytest.raises(TimeoutError):
            daemon.send_pool_metadata_request()
        assert entered.is_set()
        flight = daemon._pool_metadata_flight
        assert flight.future in daemon._probe_inflight and not flight.future.done()
        daemon._client_factory = lambda: replacement
        assert daemon._reconnect()
        assert not original._http.is_closed
        with pytest.raises(PoolPublicationError, match="in progress"):
            daemon.discard_unaccepted_pool_metadata_request(request)
        finish.set()
        expected = flight.future.result(timeout=1)
        daemon._reconcile_call_budget = 1
        assert daemon.send_pool_metadata_request() == expected
        assert queue.pool_catalog_snapshot().scopes[0] == expected
        assert not any(host == "replacement.test" for host, _method, _path in paths)
        assert original._http.is_closed
    finally:
        finish.set()


def test_reconnect_during_preflight_cannot_post_via_either_client_then_unposted_can_refresh(
    wire_owner: Any,
) -> None:
    daemon, queue, original, replacement, paths, gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    daemon._client_factory = lambda: replacement

    def reconnect(wire: httpx.Request) -> None:
        if wire.url.path == "/pool-catalog":
            gate.clear()
            assert daemon._reconnect()

    gate["before"] = reconnect
    with pytest.raises(PoolPublicationError, match="client changed before POST"):
        daemon.send_pool_metadata_request()
    assert not queue.pool_catalog_snapshot().scopes
    assert not original._http.is_closed
    assert not any(method == "POST" for _host, method, _path in paths)
    daemon.discard_unaccepted_pool_metadata_request(request)
    assert original._http.is_closed
    newer = daemon.prepare_pool_metadata_request()
    assert newer is not request
    daemon.send_pool_metadata_request()
    assert paths[-1] == ("replacement.test", "POST", "/pool-catalog/scopes/begin")


def test_bound_client_target_drift_refuses_io_but_never_posted_request_can_be_discarded(
    wire_owner: Any,
) -> None:
    daemon, _queue, original, _replacement, paths, _gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    paths.clear()
    original._http.base_url = "http://changed.test"
    with pytest.raises(PoolPublicationError, match="original coordinator client target"):
        daemon.send_pool_metadata_request()
    assert not paths and daemon._pool_publisher_custody.pending is request
    daemon.discard_unaccepted_pool_metadata_request(request)
    newer = daemon.prepare_pool_metadata_request()
    assert newer is not request and daemon._pool_metadata_binding.target == "http://changed.test"


def test_changed_client_during_preparation_retains_no_request(owner: Any) -> None:
    daemon, _queue, _calls = owner
    original = daemon.client
    read = original.pool_catalog_metadata

    def swap() -> dict:
        result = read()
        daemon.client = object()
        return result

    original.pool_catalog_metadata = swap
    with pytest.raises(PoolPublicationError, match="changed during preparation"):
        daemon.prepare_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is None
    assert daemon._pool_metadata_binding is None
    daemon.client = original


@pytest.mark.parametrize("failure", ["source", "unsupported"])
def test_pre_admission_failure_can_be_superseded_without_replaying_original(
    owner: Any, failure: str,
) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    read = daemon.client.pool_catalog_metadata
    if failure == "source":
        daemon.client.set_regs([])
    else:
        def unsupported() -> dict:
            raise DispatchUpgradeRequired("synthetic old runtime")
        daemon.client.pool_catalog_metadata = unsupported
    with pytest.raises((PoolPublicationError, DispatchUpgradeRequired)):
        daemon.send_pool_metadata_request()
    assert not sent
    with pytest.raises(PoolPublicationError, match="original pending"):
        daemon.discard_unaccepted_pool_metadata_request(replace(request, generation=99))
    daemon.discard_unaccepted_pool_metadata_request(request)
    assert daemon._pool_publisher_custody.pending is None
    assert not queue.pool_catalog_snapshot().scopes
    daemon.client.pool_catalog_metadata = read
    newer = daemon.prepare_pool_metadata_request()
    daemon.send_pool_metadata_request()
    assert sent == [newer.to_dict()]


@pytest.mark.parametrize("error", ["disconnect", "generic409", "malformed"])
def test_any_admitted_post_retains_ambiguity_even_when_current_catalog_has_no_result(
    owner: Any, error: str,
) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()

    def unknown(_request: Any, *, boundary: Any) -> Any:
        with boundary():
            if error == "disconnect":
                raise httpx.ReadError("synthetic unknown remote operation")
            if error == "generic409":
                raise DispatchError(409, "unqualified proxy refusal")
            raise PoolPublicationError("synthetic malformed response")

    daemon.client.send_pool_metadata = unknown
    with pytest.raises((httpx.ReadError, DispatchError, PoolPublicationError)):
        daemon.send_pool_metadata_request()
    assert not queue.pool_catalog_snapshot().scopes
    with pytest.raises(PoolPublicationError, match="may have been accepted"):
        daemon.discard_unaccepted_pool_metadata_request(request)
    assert daemon._pool_publisher_custody.pending is request


def test_successful_unacknowledged_flight_cannot_be_discarded(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    _sender(daemon, queue)
    wait = daemon._wait_pool_metadata_result

    def late(flight: Any, deadline: float) -> Any:
        wait(flight, deadline)
        raise TimeoutError("synthetic completed caller timeout")

    monkeypatch.setattr(daemon, "_wait_pool_metadata_result", late)
    with pytest.raises(TimeoutError):
        daemon.send_pool_metadata_request()
    with pytest.raises(PoolPublicationError, match="unacknowledged"):
        daemon.discard_unaccepted_pool_metadata_request(request)
    monkeypatch.setattr(daemon, "_wait_pool_metadata_result", wait)
    daemon.send_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is None


def test_final_retirement_closes_retained_original_after_failed_rpc(
    wire_owner: Any,
) -> None:
    daemon, _queue, original, replacement, _paths, gate = wire_owner
    daemon.prepare_pool_metadata_request()

    def lose(wire: httpx.Request, _response: Any) -> None:
        if wire.method == "POST":
            raise httpx.ReadError("synthetic lost result")

    gate["after"] = lose
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    finish_serving(daemon, single_instance=False)
    assert original._http.is_closed and replacement._http.is_closed
    assert daemon._pool_metadata_binding.closed
    assert daemon._pool_publisher_custody.pending is not None


@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("prior_unknown", [False, True])
def test_actual_metadata_rejection_can_supersede_only_without_earlier_ambiguity(
    wire_owner: Any, operation: str, prior_unknown: bool,
) -> None:
    daemon, queue, original, _replacement, _paths, gate = wire_owner
    if operation == "publish":
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
    request = daemon.prepare_pool_metadata_request()
    send = original.send_pool_metadata
    if prior_unknown:
        def unknown(_request: Any, *, boundary: Any) -> Any:
            with boundary():
                raise httpx.ReadError("synthetic original handler may still be running")
        original.send_pool_metadata = unknown
        with pytest.raises(httpx.ReadError):
            daemon.send_pool_metadata_request()
        original.send_pool_metadata = send

    def stale(wire: httpx.Request) -> None:
        if wire.method != "POST":
            return
        gate.clear()
        if operation == "begin":
            queue.begin_pool_publication("host-a", "default", "other", expected_generation=0)
        else:
            queue.register_registration("supervised-lane", {"all_repos": True}, reg_id="new-input")

    gate["before"] = stale
    with pytest.raises(DispatchError) as caught:
        daemon.send_pool_metadata_request()
    assert caught.value.is_pool_catalog_rejection(operation)
    if prior_unknown:
        with pytest.raises(PoolPublicationError, match="may have been accepted"):
            daemon.discard_unaccepted_pool_metadata_request(request)
        assert daemon._pool_publisher_custody.pending is request
    else:
        daemon.discard_unaccepted_pool_metadata_request(request)
        assert daemon._pool_publisher_custody.pending is None


@pytest.mark.parametrize("posted", [False, True])
def test_cohort_discard_separates_pre_effect_source_change_from_unknown_write(
    tmp_path: Any, monkeypatch: Any, posted: bool,
) -> None:
    from agent_dispatch.queue import TaskQueue

    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    queue = TaskQueue(tmp_path / "queue.db")
    custody = PoolCohortPreparation(RegistrarSources(base=base), queue)
    request = custody.prepare()
    if posted:
        project = queue.observe_pool_configured_cohort

        def unknown(*args: Any, **kwargs: Any) -> Any:
            result = project(*args, **kwargs)
            raise OSError(f"synthetic committed projection {result.revision} lost")

        monkeypatch.setattr(queue, "observe_pool_configured_cohort", unknown)
        with pytest.raises(OSError):
            custody.project(request)
        with pytest.raises(PoolPublicationError, match="may have been accepted"):
            custody.discard_unaccepted(request)
        assert custody.recover() is not None
        assert custody.settled[0] is request
    else:
        _write(base, revision=1, members=[])
        with pytest.raises(PoolPublicationError, match="source changed"):
            custody.project(request)
        custody.discard_unaccepted(request)
        assert custody.pending is None and queue.pool_configured_cohort().revision == 0
        assert custody.prepare().observation.members == ()


def test_cohort_inflight_write_cannot_be_discarded(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch.queue import TaskQueue

    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    queue = TaskQueue(tmp_path / "queue.db")
    custody = PoolCohortPreparation(RegistrarSources(base=base), queue)
    request = custody.prepare()
    entered, finish = Event(), Event()
    project = queue.observe_pool_configured_cohort

    def blocked(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert finish.wait(3)
        return project(*args, **kwargs)

    monkeypatch.setattr(queue, "observe_pool_configured_cohort", blocked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        write = executor.submit(custody.project, request)
        try:
            assert entered.wait(1)
            discard = executor.submit(custody.discard_unaccepted, request)
            with pytest.raises(TimeoutError):
                discard.result(timeout=0.05)
            finish.set()
            result = write.result(timeout=1)
            with pytest.raises(PoolPublicationError, match="original pending"):
                discard.result(timeout=1)
            assert custody.settled == (request, result) and custody.pending is None
        finally:
            finish.set()


@pytest.mark.parametrize("change", ["before", "after"])
@pytest.mark.parametrize("prior_unknown", [False, True])
def test_cohort_http_rejection_checks_actual_history_before_permitting_discard(
    tmp_path: Any, monkeypatch: Any, change: str, prior_unknown: bool,
) -> None:
    from agent_dispatch.queue import TaskQueue

    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    sources = RegistrarSources(base=base)
    queue = TaskQueue(tmp_path / "queue.db")
    project = queue.observe_pool_configured_cohort

    def changed_after(*args: Any, **kwargs: Any) -> Any:
        result = project(*args, **kwargs)
        _write(base, revision=1, members=[])
        return result

    if change == "after":
        monkeypatch.setattr(queue, "observe_pool_configured_cohort", changed_after)
    with TestClient(create_app(
        queue, control_token="control", enable_mcp=False, verification_interval=0,
        cohort_sources=sources, wake_is_active=lambda: True,
    )) as server:
        def handler(wire: httpx.Request) -> httpx.Response:
            if change == "before" and wire.url.path.endswith("/project"):
                _write(base, revision=1, members=[])
            response = server.request(
                wire.method, str(wire.url), headers=dict(wire.headers), content=wire.content,
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://cohort.test", control_token="control", transport=httpx.MockTransport(handler),
        ) as client:
            custody = PoolCohortPreparation(sources, client)
            request = custody.prepare()
            if prior_unknown:
                observe = client.observe_pool_configured_cohort

                def unknown(*_args: Any, boundary: Any, **_kwargs: Any) -> Any:
                    with boundary():
                        raise httpx.ReadError("synthetic original projection may still run")

                client.observe_pool_configured_cohort = unknown
                with pytest.raises(httpx.ReadError):
                    custody.project(request)
                client.observe_pool_configured_cohort = observe
            with pytest.raises(DispatchError) as caught:
                custody.project(request)
            assert caught.value.is_pool_catalog_rejection("cohort-project")
            assert custody.pending is request
            if change == "after":
                assert queue.pool_configured_cohort().revision == 1
                with pytest.raises(PoolPublicationError, match="may have been accepted"):
                    custody.discard_unaccepted(request)
                assert custody.recover() is not None
                assert custody.settled[0] is request
            else:
                assert queue.pool_configured_cohort().revision == 0
                if prior_unknown:
                    with pytest.raises(PoolPublicationError, match="may have been accepted"):
                        custody.discard_unaccepted(request)
                    assert custody.pending is request
                else:
                    custody.discard_unaccepted(request)
                    assert custody.pending is None


def test_local_cohort_transaction_refusal_can_be_discarded(tmp_path: Any) -> None:
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.queue_records import TaskError

    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    queue = TaskQueue(tmp_path / "queue.db")
    custody = PoolCohortPreparation(RegistrarSources(base=base), queue)
    request = custody.prepare()
    queue.observe_pool_configured_cohort(
        replace(request.observation, source_revision="a" * 64, revision=1),
        expected_revision=0,
    )
    with pytest.raises(TaskError):
        custody.project(request)
    custody.discard_unaccepted(request)
    assert custody.pending is None and queue.pool_configured_cohort().revision == 1
