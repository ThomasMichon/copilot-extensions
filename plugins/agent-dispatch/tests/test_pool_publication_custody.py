"""Synthetic source/metadata qualification; no production enrollment or launch."""

from __future__ import annotations

import copy
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import MappingProxyType
from typing import Any

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.coordinator_pool_catalog import _catalog_payload, pool_catalog_contract
from agent_dispatch.companion import CompanionIndeterminate
from agent_dispatch.pool_instances import PoolInstanceDefinition
from agent_dispatch.pool_publication_custody import PoolCatalogRead, PoolPublicationError
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.queue import TaskQueue
from agent_dispatch.registrations import RegistrationKind
from agent_dispatch.single_instance import SingleInstance, SingletonAdmissionError
from tests.test_pool_source_collection import _daemon, _declaration, _registration


@pytest.fixture
def owner(tmp_path: Any, monkeypatch: Any) -> Any:
    from agent_dispatch import config

    monkeypatch.setattr(config, "run_dir", lambda: tmp_path / "run")
    queue = TaskQueue(tmp_path / "catalog.db")
    daemon = _daemon([_registration()])
    calls: list[dict] = []

    def read() -> dict:
        result = _catalog_payload(queue.pool_catalog_snapshot())
        calls.append(result)
        return result

    daemon.client.pool_catalog_metadata = read
    assert daemon.acquire_singleton()
    try:
        yield daemon, queue, calls
    finally:
        daemon.shutdown()
        daemon.release_singleton()


def _apply(queue: TaskQueue, request: Any) -> Any:
    body = request.to_dict()
    body.pop("version")
    if request.operation == "begin":
        return queue.begin_pool_publication(**body)
    body["definitions"] = tuple(PoolInstanceDefinition.from_dict(item) for item in body["definitions"])
    return queue.publish_pool_catalog_scope(**body)


def _settle(daemon: Any, queue: TaskQueue) -> Any:
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    daemon._pool_publisher_custody.acknowledge(request, result)
    return request, result


@pytest.mark.parametrize("operation,field", [
    ("begin", "enrollment_cohort_revision"),
    ("begin", "definitions_cohort_revision"),
    ("publish", "definitions_cohort_revision"),
])
def test_unfenced_original_request_cannot_acknowledge_fabricated_cohort_attribution(
    owner: Any, operation: str, field: str,
) -> None:
    daemon, queue, _calls = owner
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    before = queue.pool_catalog_snapshot()
    with pytest.raises(PoolPublicationError):
        daemon._pool_publisher_custody.acknowledge(request, replace(result, **{field: 1}))
    assert daemon._pool_publisher_custody.pending is request
    assert queue.pool_catalog_snapshot() == before
    daemon._pool_publisher_custody.acknowledge(request, result)
    assert daemon._pool_publisher_custody.pending is None


def test_acquisition_token_is_actual_idempotent_and_new_after_release(tmp_path: Any) -> None:
    lock = SingleInstance(tmp_path / "singleton.lock")
    assert lock.acquisition is None
    assert lock.acquire()
    original = lock.acquisition
    try:
        assert original
        assert lock.acquire() and lock.acquisition == original
        with lock.guard_acquisition(original):
            assert lock.acquisition == original
    finally:
        lock.release()
    assert lock.acquisition is None
    with pytest.raises(SingletonAdmissionError):
        with lock.guard_acquisition(original):
            pytest.fail("stale acquisition admitted")
    assert lock.acquire()
    try:
        assert lock.acquisition != original
    finally:
        lock.release()


def test_inherited_descriptor_is_not_child_admission_or_parent_unlock(
    tmp_path: Any, monkeypatch: Any,
) -> None:
    import os
    from agent_dispatch import single_instance

    lock = SingleInstance(tmp_path / "singleton.lock")
    assert lock.acquire()
    original = lock.acquisition
    parent_pid = os.getpid()
    # A distinct descriptor simulates the child's inherited open file description.
    inherited = SingleInstance(lock.lock_path)
    inherited._fd = os.dup(lock._fd)
    inherited._holder_pid = parent_pid
    inherited._acquisition = original
    monkeypatch.setattr(single_instance.os, "getpid", lambda: parent_pid + 1)
    assert inherited.acquisition is None
    with pytest.raises(SingletonAdmissionError):
        with inherited.guard_acquisition(original):
            pytest.fail("child inherited admission")
    inherited.release()
    monkeypatch.setattr(single_instance.os, "getpid", lambda: parent_pid)
    try:
        assert lock.acquisition == original
        assert not SingleInstance(lock.lock_path).acquire()
    finally:
        lock.release()


def test_release_from_other_thread_waits_for_original_acquisition_guard(tmp_path: Any) -> None:
    lock = SingleInstance(tmp_path / "singleton.lock")
    assert lock.acquire()
    acquisition = lock.acquisition
    started = Event()

    def release() -> None:
        started.set()
        lock.release()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with lock.guard_acquisition(acquisition):
            future = executor.submit(release)
            assert started.wait(1)
            assert not future.done()
            assert lock.acquisition == acquisition
        future.result(timeout=1)
    assert lock.acquisition is None


def test_no_lock_fake_lock_or_foreign_scope_is_admission(owner: Any, tmp_path: Any) -> None:
    daemon, _queue, calls = owner
    daemon.release_singleton()
    with pytest.raises(SingletonAdmissionError):
        daemon.capture_pool_publication_inputs()
    original = daemon._lock
    daemon._lock = type("FakeLock", (), {"acquire": lambda self: True})()
    assert daemon.acquire_singleton()
    with pytest.raises(SingletonAdmissionError):
        daemon.capture_pool_publication_inputs()
    foreign = SingleInstance(tmp_path / "foreign.lock")
    assert foreign.acquire()
    daemon._lock = foreign
    try:
        with pytest.raises(SingletonAdmissionError):
            daemon.capture_pool_publication_inputs()
    finally:
        foreign.release()
        daemon._lock = original
    assert calls == []


def test_complete_capture_brackets_same_effective_set_without_enrollment(owner: Any) -> None:
    daemon, queue, calls = owner
    inputs = daemon.capture_pool_publication_inputs()
    assert inputs.coherent
    assert inputs.publisher_id == daemon._lock.acquisition
    assert inputs.snapshot is daemon.pool_source_snapshot()
    assert inputs.snapshot.input_revision
    assert len(inputs.snapshot.definitions) == 1
    assert len(calls) == 2
    assert queue.pool_catalog_snapshot().scopes == ()
    assert daemon._pool_publisher_custody is None
    frozen = inputs.snapshot.input_revision
    daemon.client.set_regs([])
    daemon._desired()
    assert inputs.snapshot.input_revision == frozen
    assert not daemon.pool_source_snapshot().definitions


@pytest.mark.parametrize("source", ["declarations", "overrides"])
def test_local_change_during_collection_is_not_coherent(owner: Any, source: str) -> None:
    daemon, _queue, _calls = owner
    reads = 0

    def declarations() -> DeclarationRead:
        nonlocal reads
        reads += 1
        return DeclarationRead((_declaration(priority=reads),), True)

    def overrides() -> dict:
        nonlocal reads
        reads += 1
        return {"logical:repo:example:worker": {"disabled": reads > 1}}

    if source == "declarations":
        daemon.declared_source = declarations
    else:
        daemon.overrides_source = overrides
    inputs = daemon.capture_pool_publication_inputs()
    assert not inputs.coherent and "local-inputs-changed" in inputs.reasons
    assert reads == 2


@pytest.mark.parametrize("key", ["pool-catalog-before", "pool-local-recheck", "pool-catalog-after"])
def test_completed_older_future_does_not_qualify_a_new_capture(owner: Any, key: str) -> None:
    daemon, queue, _calls = owner
    first = daemon.capture_pool_publication_inputs()
    assert first.coherent
    future: Future = Future()
    future.set_result(
        (first.snapshot.input_revision, True) if key == "pool-local-recheck"
        else _catalog_payload(queue.pool_catalog_snapshot())
    )
    daemon._discovery_futures[key] = future
    daemon._probe_inflight.add(future)
    second = daemon.capture_pool_publication_inputs()
    assert not second.coherent
    assert any(reason.endswith("not-fresh") for reason in second.reasons)
    assert key not in daemon._discovery_futures
    assert daemon.capture_pool_publication_inputs().coherent


def test_unreadable_local_source_is_not_an_empty_enrollment(owner: Any) -> None:
    daemon, queue, _calls = owner

    def unavailable() -> dict:
        raise PermissionError("private-location")

    daemon.overrides_source = unavailable
    with pytest.raises(PoolPublicationError, match="complete inputs"):
        daemon.prepare_pool_metadata_request()
    assert queue.pool_catalog_snapshot().scopes == ()
    assert daemon._pool_publisher_custody.pending is None


@pytest.mark.parametrize("confirmed", [False, True])
def test_new_bounded_override_timeout_never_drops_confirmed_veto(owner: Any, confirmed: bool) -> None:
    daemon, _queue, _calls = owner
    veto = {"logical:repo:example:worker": {"disabled": True}}
    daemon.overrides_source = lambda: veto
    if confirmed:
        assert daemon._effective_registrations() == {}
    future: Future = Future()
    daemon._discovery_futures["overrides"] = future
    daemon._probe_inflight.add(future)
    daemon._reconcile_deadline = time.monotonic()
    try:
        if confirmed:
            assert daemon._effective_registrations() == {}
        else:
            with pytest.raises(CompanionIndeterminate, match="veto snapshot"):
                daemon._effective_registrations()
        assert not daemon.pool_source_snapshot().complete
        future.set_result(veto)
        daemon._reconcile_deadline = time.monotonic() + 1
        assert daemon._effective_registrations() == {}
        assert not daemon.pool_source_snapshot().complete
        daemon._reconcile_deadline = None
        assert daemon._effective_registrations() == {}
        assert daemon.pool_source_snapshot().complete
    finally:
        if not future.done():
            future.set_result(veto)
        daemon._reconcile_deadline = None


def test_coordinator_mutation_between_collection_and_recheck_blocks(owner: Any) -> None:
    daemon, queue, _calls = owner
    changes = 0

    def override() -> dict:
        nonlocal changes
        changes += 1
        queue.register_registration(
            RegistrationKind.SUPERVISED_LANE, {"all_repos": True, "max_concurrent": changes}, reg_id="changed",
        )
        return {}

    daemon.overrides_source = override
    inputs = daemon.capture_pool_publication_inputs()
    assert not inputs.coherent
    assert "coordinator-inputs-changed" in inputs.reasons
    with pytest.raises(PoolPublicationError):
        daemon.prepare_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is None


def test_unknown_begin_replays_original_not_successor_generation(owner: Any) -> None:
    daemon, queue, calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    first = _apply(queue, request)
    custody = daemon._pool_publisher_custody
    calls_before = len(calls)
    with pytest.raises(PoolPublicationError, match="unsettled"):
        daemon.prepare_pool_metadata_request()
    assert len(calls) == calls_before
    assert custody.pending is request
    assert request.to_dict() == original
    assert _apply(queue, request) == first
    custody.acknowledge(request, first)
    assert custody.pending is None
    assert custody.settled == (request, first)


def test_unknown_publish_keeps_original_bytes_and_does_not_renew_ttl(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    result = _apply(queue, request)
    daemon.client.set_regs([])
    assert request.to_dict() == original
    assert _apply(queue, request).valid_until == result.valid_until
    daemon._pool_publisher_custody.acknowledge(request, result)
    assert daemon._pool_publisher_custody.settled == (request, result)
    assert result.definitions == request.inputs.snapshot.definitions


def test_successor_position_cannot_rebase_original_custody(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    queue.begin_pool_publication("host-a", "default", "successor", expected_generation=1)
    with pytest.raises(PoolPublicationError, match="position changed"):
        daemon.prepare_pool_metadata_request()
    assert daemon._pool_publisher_custody.position.generation == 1
    assert daemon._pool_publisher_custody.pending is None


def test_reacquisition_does_not_drop_unknown_request_and_late_result_is_accounted(owner: Any) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    custody = daemon._pool_publisher_custody
    daemon.release_singleton()
    assert daemon.acquire_singleton()
    with pytest.raises(PoolPublicationError, match="unsettled"):
        daemon.prepare_pool_metadata_request()
    assert custody.pending is request
    custody.acknowledge(request, result)
    assert custody.settled == (request, result)
    with pytest.raises(PoolPublicationError, match="unsettled"):
        daemon.prepare_pool_metadata_request()


def test_unavailable_publish_retains_original_definition_generation(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    _request, published = _settle(daemon, queue)
    daemon.overrides_source = lambda: {"invalid": 1}
    unavailable, result = _settle(daemon, queue)
    assert unavailable.reason
    assert unavailable.to_dict()["definitions"] == []
    assert result.state == "unavailable"
    assert result.definitions == published.definitions
    assert result.definitions_revision == published.revision
    assert result.definitions_generation == published.generation
    assert not queue.pool_catalog_snapshot().ready


@pytest.mark.parametrize("field", ["publisher_id", "generation", "revision", "input_revision"])
def test_wrong_acknowledgment_keeps_original_request(owner: Any, field: str) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    wrong = replace(result, **{field: "foreign" if field.endswith("id") or field == "input_revision" else 99})
    with pytest.raises(PoolPublicationError):
        daemon._pool_publisher_custody.acknowledge(request, wrong)
    assert daemon._pool_publisher_custody.pending is request
    daemon._pool_publisher_custody.acknowledge(request, result)


def test_ordinary_desired_does_not_invoke_new_transport(owner: Any) -> None:
    daemon, queue, calls = owner
    assert daemon._desired()
    assert calls == []
    assert queue.pool_catalog_snapshot().scopes == ()


@pytest.mark.parametrize("contract", [
    None, {}, {"protocol": "future/v2"}, {**pool_catalog_contract("control"), "supported_versions": [True]},
    {**pool_catalog_contract("control"), "definition_versions": [2]},
    {**pool_catalog_contract("control"), "control_authority_configured": False},
])
def test_unsupported_running_contract_refuses_before_catalog_read(contract: Any) -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.path == "/health"
        return httpx.Response(200, json={"pool_catalog_transport": contract})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            client.pool_catalog_metadata()
    assert paths == ["/health"]


def test_control_only_read_uses_running_contract_and_rejects_coerced_revision() -> None:
    paths = []
    expected = {"protocol": "pool_catalog_transport/v1", "version": 1, "registration_revision": 4, "scopes": []}

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"pool_catalog_transport": pool_catalog_contract("control")})
        scheme = "Be" + "arer"
        assert request.headers["Authorization"] == " ".join((scheme, "control"))
        assert not request.url.query
        return httpx.Response(200, json=expected)

    with DispatchClient("http://example.test", control_token="control", transport=httpx.MockTransport(handler)) as client:
        assert client.pool_catalog_metadata() == expected
        expected["registration_revision"] = True
        with pytest.raises(PoolPublicationError):
            client.pool_catalog_metadata()
    assert paths == ["/health", "/pool-catalog", "/health", "/pool-catalog"]


@pytest.mark.parametrize("status", [401, 403, 503])
def test_catalog_auth_refusal_remains_explicit_without_legacy_fallback(status: int) -> None:
    from agent_dispatch.client import DispatchError

    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"pool_catalog_transport": pool_catalog_contract("control")})
        return httpx.Response(status, json={"detail": {"code": "control-refused"}})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError) as caught:
            client.pool_catalog_metadata()
    assert caught.value.status_code == status
    assert paths == ["/health", "/pool-catalog"]


def test_catalog_observation_refuses_duplicate_or_invalid_scope_without_subset() -> None:
    value = {"protocol": "pool_catalog_transport/v1", "version": 1, "registration_revision": 0, "scopes": [
        {"machine": "host-a", "env": "default", "publisher_id": "one", "generation": 1,
         "revision": 0, "state": "pending", "input_revision": None,
         "registration_revision": None, "valid_until": None, "definitions": [],
         "definitions_generation": None, "definitions_revision": None, "reason": None},
    ]}
    assert len(PoolCatalogRead.from_dict(value).positions) == 1
    duplicate = copy.deepcopy(value)
    duplicate["scopes"].append(copy.deepcopy(duplicate["scopes"][0]))
    with pytest.raises(PoolPublicationError):
        PoolCatalogRead.from_dict(duplicate)
    value["scopes"][0]["generation"] = True
    with pytest.raises(PoolPublicationError):
        PoolCatalogRead.from_dict(value)


@pytest.mark.parametrize("copyable", [False, True])
def test_valid_mapping_overrides_keep_veto_even_when_revision_copy_is_unavailable(
    owner: Any, copyable: bool,
) -> None:
    daemon, queue, _calls = owner

    class Uncopyable:
        def __deepcopy__(self, _memo: Any) -> Any:
            raise TypeError("uncopyable-private-value")

    veto = MappingProxyType({
        "logical:repo:example:worker": {
            "disabled": True, "reason": "operator hold" if copyable else Uncopyable(),
        },
    })
    daemon.overrides_source = lambda: veto
    assert daemon._desired() == {}
    inputs = daemon.capture_pool_publication_inputs()
    assert inputs.coherent == copyable
    assert not inputs.snapshot.definitions
    assert queue.pool_catalog_snapshot().scopes == ()
