"""Configured observations fence original metadata custody, not live admission."""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_pool_catalog import _catalog_payload, pool_catalog_contract
from agent_dispatch.pool_publication_custody import PoolCatalogRead, PoolPublicationError, PoolPublisherCustody
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.registrar_configuration import ConfiguredSupervisorCohort, SupervisorScopeIntent
from tests.test_pool_metadata_sender import _sender
from tests.test_pool_publication_custody import _apply, _settle, owner as _owner_fixture

owner = _owner_fixture


def _project(queue: Any, *, source: str = "a", configured_revision: int = 7,
             members: tuple[SupervisorScopeIntent, ...] | None = None) -> Any:
    observation = ConfiguredSupervisorCohort(
        "complete", source * 64, configured_revision,
        members if members is not None else (
            SupervisorScopeIntent("logical-host", "host-a", "default"),
            SupervisorScopeIntent("logical-host", "host-b", "wsl"),
        ),
    )
    return queue.observe_pool_configured_cohort(
        observation, expected_revision=queue.pool_catalog_snapshot().configured_cohort.revision,
    )


def test_whole_cohort_read_is_immutable_additive_and_not_enrollment_coverage(owner: Any) -> None:
    daemon, queue, _calls = owner
    projected = _project(queue)
    payload = _catalog_payload(queue.pool_catalog_snapshot())
    payload["configured_cohort"]["additive"] = True
    payload["configured_cohort"]["observation"]["members"][0]["additive"] = True
    parsed = PoolCatalogRead.from_dict(payload)
    assert parsed.configured_cohort == projected
    assert parsed.positions == () and len(parsed.configured_cohort.observation.members) == 2
    payload["configured_cohort"]["observation"]["members"].clear()
    assert len(parsed.configured_cohort.observation.members) == 2
    inputs = daemon.capture_pool_publication_inputs()
    assert inputs.coherent and inputs.catalog.configured_cohort == projected
    assert not queue.pool_catalog_snapshot().configured_ready


@pytest.mark.parametrize("kind", ["missing", "unprojected", "empty", "unconfirmed", "unavailable"])
def test_missing_legacy_uncertain_and_explicit_empty_are_not_conflated(owner: Any, kind: str) -> None:
    daemon, queue, _calls = owner
    if kind == "empty":
        _project(queue, members=())
        assert queue.pool_catalog_snapshot().configured_ready
    elif kind in ("unconfirmed", "unavailable"):
        queue.observe_pool_configured_cohort(
            ConfiguredSupervisorCohort(kind, None, None, (), "source-not-confirmed"),
            expected_revision=0,
        )
    if kind == "missing":
        def legacy() -> dict:
            value = _catalog_payload(queue.pool_catalog_snapshot())
            value.pop("configured_cohort")
            return value
        daemon.client.pool_catalog_metadata = legacy
    if kind in ("missing", "unprojected"):
        request = daemon.prepare_pool_metadata_request()
        assert request.expected_cohort_revision is None
        assert "expected_cohort_revision" not in request.to_dict()
        cohort = request.inputs.catalog.configured_cohort
        assert (cohort is None) == (kind == "missing")
    else:
        with pytest.raises(PoolPublicationError):
            daemon.prepare_pool_metadata_request()
        assert daemon._pool_publisher_custody.pending is None
    assert queue.pool_catalog_snapshot().scopes == ()


@pytest.mark.parametrize("change", [
    {"revision": True}, {"revision": -1}, {"observation": None}, {"observation": []},
    {"revision": 0}, {"observation_field": ("revision", True)},
    {"observation_field": ("source_revision", "not-a-digest")},
    {"observation_field": ("reason", "inconsistent")},
    {"observation_field": ("state", "future")},
    {"observation_field": ("members", None)},
    {"duplicate": True}, {"member_field": ("execution_machine", "")},
    {"member_field": ("env", " wsl")}, {"member_field": ("machine", None)},
    {"observation_field": ("state", "unavailable")},
])
def test_malformed_cohort_never_retains_a_valid_subset(owner: Any, change: dict) -> None:
    _daemon, queue, _calls = owner
    _project(queue)
    payload = _catalog_payload(queue.pool_catalog_snapshot())
    cohort = payload["configured_cohort"]
    if "observation_field" in change:
        key, value = change["observation_field"]
        cohort["observation"][key] = value
    elif "member_field" in change:
        key, value = change["member_field"]
        cohort["observation"]["members"][0][key] = value
    elif "duplicate" in change:
        cohort["observation"]["members"].append(copy.deepcopy(cohort["observation"]["members"][0]))
    else:
        cohort.update(change)
    with pytest.raises(PoolPublicationError):
        PoolCatalogRead.from_dict(payload)


def test_logical_identity_cannot_substitute_for_actual_execution_scope(owner: Any) -> None:
    daemon, queue, _calls = owner
    _project(queue, members=(SupervisorScopeIntent("host-a", "other-runtime", "default"),))
    with pytest.raises(PoolPublicationError):
        daemon.prepare_pool_metadata_request()
    assert not queue.pool_catalog_snapshot().scopes


def test_changed_whole_cohort_between_capture_reads_refuses_preparation(owner: Any) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    reads = 0

    def read() -> dict:
        nonlocal reads
        reads += 1
        if reads == 2:
            _project(queue, source="b")
        return _catalog_payload(queue.pool_catalog_snapshot())

    daemon.client.pool_catalog_metadata = read
    inputs = daemon.capture_pool_publication_inputs()
    assert "coordinator-inputs-changed" in inputs.reasons
    assert not inputs.coherent
    assert inputs.catalog.configured_cohort.revision == 2
    with pytest.raises(PoolPublicationError):
        PoolPublisherCustody(inputs.publisher_id).prepare(inputs)
    assert queue.pool_catalog_snapshot().scopes == ()


def test_original_wire_and_attribution_follow_cohort_without_reenrolling(owner: Any) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    sent = _sender(daemon, queue)
    begin = daemon.prepare_pool_metadata_request()
    assert begin.to_dict()["expected_cohort_revision"] == 1
    enrolled = daemon.send_pool_metadata_request()
    assert enrolled.enrollment_cohort_revision == 1
    _project(queue, source="b")
    publish = daemon.prepare_pool_metadata_request()
    assert publish.expected_cohort_revision == 2
    published = daemon.send_pool_metadata_request()
    assert published.enrollment_cohort_revision == 1
    assert published.definitions_cohort_revision == 2
    daemon.declared_source = lambda: DeclarationRead((), False, "source-scan-unavailable")
    _project(queue, source="c")
    unavailable = daemon.prepare_pool_metadata_request()
    result = daemon.send_pool_metadata_request()
    assert unavailable.reason and unavailable.to_dict()["definitions"] == []
    assert result.enrollment_cohort_revision == 1 and result.definitions_cohort_revision == 2
    assert result.definitions == published.definitions
    assert sent == [begin.to_dict(), publish.to_dict(), unavailable.to_dict()]


@pytest.mark.parametrize("operation", ["begin", "publish", "unavailable"])
@pytest.mark.parametrize("field", ["enrollment_cohort_revision", "definitions_cohort_revision"])
def test_forged_cohort_ack_retains_original_request_and_last_settled(
    owner: Any, operation: str, field: str,
) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    if operation != "begin":
        _settle(daemon, queue)
    if operation == "unavailable":
        _settle(daemon, queue)
        daemon.overrides_source = lambda: {"invalid": 1}
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    custody = daemon._pool_publisher_custody
    previous = custody.settled
    with pytest.raises(PoolPublicationError):
        custody.acknowledge(request, replace(result, **{field: 999}))
    assert custody.pending is request and custody.settled is previous
    custody.acknowledge(request, result)
    assert custody.settled == (request, result) and custody.pending is None


@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("change", ["removed", "unavailable", "pointer-bytes"])
def test_accepted_original_recovery_survives_changed_cohort_without_post_or_ttl(
    owner: Any, operation: str, change: str,
) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    body = request.to_dict()
    accepted = _apply(queue, request)
    if change == "removed":
        _project(queue, configured_revision=8, members=())
    elif change == "pointer-bytes":
        _project(queue, source="b")
    else:
        queue.observe_pool_configured_cohort(
            ConfiguredSupervisorCohort("unavailable", None, None, (), "read-failed"),
            expected_revision=1,
        )
    daemon.release_singleton()
    assert daemon.acquire_singleton()
    sent = _sender(daemon, queue)
    assert daemon.send_pool_metadata_request() == accepted
    assert sent == [] and request.to_dict() == body
    assert daemon._pool_publisher_custody.pending is None
    assert queue.pool_catalog_snapshot().scopes[0].valid_until == accepted.valid_until


@pytest.mark.parametrize("change", ["cohort-revision", "same-revision-members", "missing"])
def test_unaccepted_original_send_never_rebases_whole_cohort(owner: Any, change: str) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    request = daemon.prepare_pool_metadata_request()
    body = request.to_dict()
    if change == "cohort-revision":
        _project(queue, source="b")
    else:
        def changed() -> dict:
            value = _catalog_payload(queue.pool_catalog_snapshot())
            if change == "missing":
                value.pop("configured_cohort")
            else:
                value["configured_cohort"]["observation"]["members"][1]["machine"] = "changed-logical"
            return value
        daemon.client.pool_catalog_metadata = changed
    sent = _sender(daemon, queue)
    with pytest.raises(PoolPublicationError, match="fences changed"):
        daemon.send_pool_metadata_request()
    assert sent == [] and request.to_dict() == body
    assert daemon._pool_publisher_custody.pending is request
    assert queue.pool_catalog_snapshot().scopes == ()


@pytest.mark.parametrize("change", ["missing", "regressed", "same-revision-source", "enrollment"])
def test_settled_cohort_and_enrollment_cannot_silently_downgrade(owner: Any, change: str) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    _settle(daemon, queue)

    def changed() -> dict:
        value = _catalog_payload(queue.pool_catalog_snapshot())
        if change == "missing":
            value.pop("configured_cohort")
        elif change == "regressed":
            value["configured_cohort"] = {"revision": 0, "observation": None}
        elif change == "enrollment":
            value["scopes"][0]["enrollment_cohort_revision"] = 999
        else:
            value["configured_cohort"]["observation"]["source_revision"] = "f" * 64
        return value

    daemon.client.pool_catalog_metadata = changed
    with pytest.raises(PoolPublicationError):
        daemon.prepare_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is None


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_cohort_change_during_transport_preflight_refuses_post(owner: Any, operation: str) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    body = request.to_dict()
    writes = []

    def send(original: Any, *, boundary: Any) -> Any:
        _project(queue, source="b")
        with boundary():
            writes.append(original.to_dict())
            return _apply(queue, original)

    daemon.client.send_pool_metadata = send
    before = queue.pool_catalog_snapshot().scopes
    with pytest.raises(PoolPublicationError, match="fences changed"):
        daemon.send_pool_metadata_request()
    assert writes == [] and daemon._pool_publisher_custody.pending is request
    assert request.to_dict() == body and queue.pool_catalog_snapshot().scopes == before


@pytest.mark.parametrize("supported", [None, [], [True], [2], "1", [1]])
@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("fenced", [False, True])
def test_fenced_post_requires_running_optional_support_before_boundary(
    owner: Any, supported: Any, operation: str, fenced: bool,
) -> None:
    daemon, queue, _calls = owner
    if fenced:
        _project(queue)
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
            contract = pool_catalog_contract("control")
            contract["configured_cohort_fencing_versions"] = supported
            return httpx.Response(200, json={"pool_catalog_transport": contract})
        from agent_dispatch.coordinator_pool_catalog import _scope_payload

        assert json.loads(wire.content) == request.to_dict()
        return httpx.Response(200, json={
            "protocol": "pool_catalog_transport/v1", "version": 1,
            "scope": _scope_payload(_apply(queue, request)),
        })

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        capable = isinstance(supported, list) and any(type(item) is int and item == 1 for item in supported)
        if not fenced or capable:
            result = client.send_pool_metadata(request, boundary=boundary)
            daemon._pool_publisher_custody.acknowledge(request, result)
            assert paths == ["/health", f"/pool-catalog/scopes/{operation}"] and admitted == [True]
            assert daemon._pool_publisher_custody.pending is None
        else:
            with pytest.raises(DispatchUpgradeRequired):
                client.send_pool_metadata(request, boundary=boundary)
            assert paths == ["/health"] and admitted == []
            assert daemon._pool_publisher_custody.pending is request


def test_new_enrollment_preserves_prior_definition_cohort_and_freshness(owner: Any) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    _settle(daemon, queue)
    _request, published = _settle(daemon, queue)
    _project(queue, source="b")
    daemon.release_singleton()
    assert daemon.acquire_singleton()
    daemon._pool_publisher_custody = None
    _request, enrolled = _settle(daemon, queue)
    assert enrolled.enrollment_cohort_revision == 2
    assert enrolled.definitions_cohort_revision == 1
    assert enrolled.definitions == published.definitions
    assert enrolled.valid_until == published.valid_until
    _request, complete = _settle(daemon, queue)
    assert complete.enrollment_cohort_revision == complete.definitions_cohort_revision == 2


def test_fenced_sender_uses_actual_http_parser_and_recovers_after_cohort_removal(owner: Any) -> None:
    daemon, queue, _calls = owner
    _project(queue)
    server = TestClient(create_app(
        queue, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    writes = []
    lose_reply = False

    def handler(wire: httpx.Request) -> httpx.Response:
        response = server.request(wire.method, str(wire.url), headers=dict(wire.headers), content=wire.content)
        if wire.method == "POST":
            body = json.loads(wire.content)
            writes.append(body)
            assert body["expected_cohort_revision"] == 1 and response.status_code == 200
            if lose_reply:
                raise httpx.ReadError("synthetic accepted reply lost")
        return httpx.Response(response.status_code, json=response.json())

    with server, DispatchClient("http://testserver", token="data", control_token="control",
                                transport=httpx.MockTransport(handler)) as client:
        daemon.client.pool_catalog_metadata = client.pool_catalog_metadata
        daemon.client.send_pool_metadata = client.send_pool_metadata
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
        request = daemon.prepare_pool_metadata_request()
        lose_reply = True
        with pytest.raises(httpx.ReadError):
            daemon.send_pool_metadata_request()
        before = queue.pool_catalog_snapshot().scopes[0]
        _project(queue, configured_revision=8, members=())
        assert daemon.send_pool_metadata_request() == before
        assert len(writes) == 2 and writes[-1] == request.to_dict()
        assert queue.pool_catalog_snapshot().scopes[0] == before
