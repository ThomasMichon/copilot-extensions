"""Whole configured-owner projection over real ASGI and isolated SQLite."""

from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.pool_cohort_preparation import PoolCohortPreparation
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.queue import TaskQueue
from agent_dispatch.registrar_discovery import RegistrarSources
from agent_dispatch.single_instance import SingleInstance
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests.test_pool_catalog_transport import _headers
from tests.test_pool_cohort_preparation import _write

PATHS = [
    ("GET", "/pool-catalog/cohort"),
    ("POST", "/pool-catalog/cohort/project"),
    ("POST", "/pool-catalog/cohort/recover"),
]


@pytest.fixture
def owner(tmp_path: Path) -> Any:
    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    queue = TaskQueue(tmp_path / "queue.db")
    server = TestClient(create_app(
        queue, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    requests: list[httpx.Request] = []
    after: list[Any] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = server.request(
            request.method, str(request.url), headers=request.headers, content=request.content,
        )
        if after:
            after.pop(0)(request, response)
        return httpx.Response(response.status_code, json=response.json())

    with DispatchClient(
        "http://coordinator", token="data", control_token="control",
        transport=httpx.MockTransport(handle),
    ) as client:
        sources = RegistrarSources(base=base)
        custody = PoolCohortPreparation(sources, client)
        yield base, sources, queue, server, client, custody, requests, after
    server.close()


def _wire(custody: PoolCohortPreparation) -> dict:
    return {"version": 1, **custody.prepare().to_dict()}


@pytest.mark.parametrize("method,path", PATHS)
@pytest.mark.parametrize("authority", ["data", "wrong", "task", "missing-control"])
def test_routes_require_existing_control_authority_before_mutation(
    owner: Any, method: str, path: str, authority: str,
) -> None:
    _base, _sources, queue, server, _client, custody, _requests, _after = owner
    body = _wire(custody)
    headers = _headers(authority)
    if authority == "task":
        headers = {
            **_headers(),
            TASK_SCOPE_HEADER: mint_task_scope_token(
                "control", task_id="task", worker_id="worker",
                owner_session_id="native", epoch=0, generation=1,
            ),
        }
    if authority == "missing-control":
        server = TestClient(create_app(
            queue, token="data", control_token=None, enable_mcp=False, verification_interval=0,
        ))
        headers = _headers("data")
    before = queue.pool_catalog_snapshot()
    response = server.request(method, path, headers=headers, json=body if method == "POST" else None)
    assert response.status_code == (503 if authority == "missing-control" else
                                    401 if authority == "wrong" else 403)
    assert queue.pool_catalog_snapshot() == before


def test_whole_configuration_projection_uses_actual_lock_and_existing_queue_history(
    owner: Any,
) -> None:
    base, _sources, queue, _server, _client, custody, requests, after = owner
    request = custody.prepare()
    assert request.coordinator_path == "http://coordinator"
    assert queue.pool_configured_cohort().revision == 0
    lock_checks = []

    def locked(http_request: httpx.Request, _response: Any) -> None:
        if http_request.url.path.endswith("/project"):
            contender = SingleInstance(base / "pointers.lock")
            try:
                assert not contender.acquire()
                lock_checks.append(True)
            finally:
                contender.release()

    after.extend([lambda *_: None, lambda *_: None, lambda *_: None, locked])
    result = custody.project(request)
    assert result.observation == request.observation and result.revision == 1
    assert custody.settled == (request, result) and custody.pending is None
    posted = [item for item in requests if item.url.path.endswith("/project")]
    assert len(posted) == 1 and json.loads(posted[0].content) == {
        "version": 1, **request.to_dict(),
    }
    assert lock_checks == [True]
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 1
        for table in ("tasks", "spawn_reservations", "pool_publication_scopes"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("committed", [False, True])
def test_unknown_reply_retains_original_then_history_only_recovery(
    owner: Any, monkeypatch: pytest.MonkeyPatch, committed: bool,
) -> None:
    base, sources, queue, server, client, custody, requests, _after = owner
    request = custody.prepare()
    original = request.to_dict()
    old_transport = client._http._transport
    writes = 0

    def lost(http_request: httpx.Request) -> httpx.Response:
        nonlocal writes
        if http_request.url.path.endswith("/project"):
            writes += 1
            if committed:
                response = server.post(
                    "/pool-catalog/cohort/project", headers=_headers(),
                    json=json.loads(http_request.content),
                )
                assert response.status_code == 200
            raise httpx.ReadError("synthetic original reply lost")
        return old_transport.handle_request(http_request)

    client._http._transport = httpx.MockTransport(lost)
    with pytest.raises(httpx.ReadError):
        custody.project(request)
    assert custody.pending is request and request.to_dict() == original
    client._http._transport = old_transport
    _write(base, revision=1, members=[])
    newer = sources.configured_supervisor_cohort()
    queue.observe_pool_configured_cohort(
        newer, expected_revision=1 if committed else 0,
    )
    if committed:
        monkeypatch.setattr(sources, "configured_supervisor_cohort", lambda: pytest.fail("new source read"))
        recovered = custody.project(request)
        assert recovered.revision == 1 and recovered.observation == request.observation
        assert custody.settled == (request, recovered)
    else:
        assert custody.recover() is None
        with pytest.raises(PoolPublicationError, match="source changed"):
            custody.project(request)
        assert custody.pending is request
    assert writes == 1 and not [item for item in requests if item.url.path.endswith("/project")]


@pytest.mark.parametrize("change", ["source", "target"])
def test_post_negotiation_owner_recheck_refuses_before_write(
    owner: Any, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    base, _sources, queue, _server, client, custody, requests, _after = owner
    request = custody.prepare()
    health = client.health
    reads = 0

    def changing_health() -> dict:
        nonlocal reads
        result = health()
        reads += 1
        if reads == 2:  # recovery negotiation, then original projection negotiation
            if change == "source":
                _write(base, revision=1)
            else:
                client._http.base_url = httpx.URL("http://different-coordinator")
        return result

    monkeypatch.setattr(client, "health", changing_health)
    with pytest.raises(PoolPublicationError, match="source changed|owner target"):
        custody.project(request)
    assert custody.pending is request and queue.pool_configured_cohort().revision == 0
    assert not [item for item in requests if item.url.path.endswith("/project")]


@pytest.mark.parametrize("capability", [None, [], [True], [2], "1"])
def test_missing_old_or_rolled_back_support_never_emits_new_requests(
    owner: Any, monkeypatch: pytest.MonkeyPatch, capability: object,
) -> None:
    _base, _sources, queue, _server, client, custody, requests, _after = owner
    request = custody.prepare()
    health = client.health()
    health["pool_catalog_transport"]["configured_cohort_projection_versions"] = capability
    monkeypatch.setattr(client, "health", lambda: health)
    before = len(requests)
    with pytest.raises(DispatchUpgradeRequired, match="projection"):
        custody.project(request)
    assert len(requests) == before and custody.pending is request
    assert queue.pool_configured_cohort().revision == 0
    # Existing metadata operations still negotiate their original capability.
    assert client.pool_catalog_metadata()["registration_revision"] == 0


@pytest.mark.parametrize("field,value", [
    ("version", True), ("expected_revision", "0"), ("expected_revision", -1),
    ("extra", []), ("observation.revision", True), ("observation.extra", "ignored"),
    ("observation.members", [{"machine": "logical", "execution_machine": "host", "env": "env", "extra": 1}]),
])
def test_strict_wire_refuses_coercion_and_unknown_fields_without_subset_mutation(
    owner: Any, field: str, value: object,
) -> None:
    _base, _sources, queue, server, _client, custody, _requests, _after = owner
    body = _wire(custody)
    parent, dot, key = field.partition(".")
    (body[parent] if dot else body)[key if dot else parent] = value
    response = server.post("/pool-catalog/cohort/project", headers=_headers(), json=body)
    assert response.status_code == 422
    assert queue.pool_configured_cohort().revision == 0


@pytest.mark.parametrize("change", ["duplicate", "bad-member", "bad-state", "bad-digest"])
def test_semantically_invalid_whole_observation_never_publishes_valid_subset(
    owner: Any, change: str,
) -> None:
    _base, _sources, queue, server, _client, custody, _requests, _after = owner
    body = _wire(custody)
    observation = body["observation"]
    if change == "duplicate":
        observation["members"].append(copy.deepcopy(observation["members"][0]))
    elif change == "bad-member":
        observation["members"][0]["execution_machine"] = " "
    elif change == "bad-state":
        observation["reason"] = "complete cannot have a reason"
    else:
        observation["source_revision"] = "bad"
    response = server.post("/pool-catalog/cohort/project", headers=_headers(), json=body)
    assert response.status_code == 409
    assert queue.pool_configured_cohort().revision == 0


def test_stale_cas_fails_without_rebase_and_recovery_cannot_write(owner: Any) -> None:
    base, sources, queue, server, client, custody, _requests, _after = owner
    request = custody.prepare()
    assert client.recover_pool_configured_cohort(
        request.observation, expected_revision=request.expected_revision,
    ) is None
    assert queue.pool_configured_cohort().revision == 0
    _write(base, revision=1, members=[])
    queue.observe_pool_configured_cohort(sources.configured_supervisor_cohort(), expected_revision=0)
    with pytest.raises(DispatchError) as error:
        client.observe_pool_configured_cohort(
            request.observation, expected_revision=0, boundary=nullcontext,
        )
    assert error.value.status_code == 409 and custody.pending is request
    assert queue.pool_configured_cohort().revision == 1
    assert server.get("/pool-catalog/cohort?machine=subset", headers=_headers()).status_code == 400


@pytest.mark.parametrize("state", ["legacy", "empty", "invalid-cohort"])
def test_distinct_source_states_and_satisfied_noop_survive_transport(owner: Any, state: str) -> None:
    base, _sources, queue, _server, _client, custody, _requests, _after = owner
    if state == "legacy":
        (base / "pointers.json").write_text("[]", encoding="utf-8")
    elif state == "empty":
        _write(base, members=[])
    else:
        _write(base, members=[{"machine": "incomplete"}])
    request = custody.prepare()
    result = custody.project(request)
    assert result.observation.state == {
        "legacy": "unconfirmed", "empty": "complete", "invalid-cohort": "unavailable",
    }[state]
    assert result.observation.members == ()
    noop = custody.prepare()
    assert custody.project(noop) == result
    assert queue.pool_configured_cohort() == result
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 1


@pytest.mark.parametrize("change", ["missing", "null", "boolean-revision", "wrong-members", "wrong-cas"])
def test_malformed_or_wrong_original_reply_retains_custody_then_recovers(
    owner: Any, change: str,
) -> None:
    _base, _sources, queue, _server, client, custody, _requests, _after = owner
    request = custody.prepare()
    transport = client._http._transport

    def corrupt(http_request: httpx.Request) -> httpx.Response:
        response = transport.handle_request(http_request)
        if http_request.url.path.endswith("/project"):
            result = response.json()
            if change == "missing":
                del result["cohort"]
            elif change == "null":
                result["cohort"] = None
            elif change == "boolean-revision":
                result["cohort"]["revision"] = True
            elif change == "wrong-cas":
                result["cohort"]["revision"] = 20
            else:
                result["cohort"]["observation"]["members"] = []
            return httpx.Response(200, json=result)
        return response

    client._http._transport = httpx.MockTransport(corrupt)
    with pytest.raises(PoolPublicationError):
        custody.project(request)
    assert custody.pending is request and queue.pool_configured_cohort().revision == 1
    client._http._transport = transport
    recovered = custody.recover()
    assert recovered.observation == request.observation and recovered.revision == 1
    assert custody.settled == (request, recovered)


@pytest.mark.parametrize("change", ["source", "target"])
def test_post_write_change_retains_original_accepted_history_without_false_settlement(
    owner: Any, change: str,
) -> None:
    base, _sources, queue, _server, client, custody, _requests, _after = owner
    request = custody.prepare()
    transport = client._http._transport

    def mutate(http_request: httpx.Request) -> httpx.Response:
        response = transport.handle_request(http_request)
        if http_request.url.path.endswith("/project"):
            if change == "source":
                _write(base, revision=1, members=[])
            else:
                client._http.base_url = httpx.URL("http://different-coordinator")
        return response

    client._http._transport = httpx.MockTransport(mutate)
    with pytest.raises(PoolPublicationError):
        custody.project(request)
    assert custody.pending is request and queue.pool_configured_cohort().revision == 1
    client._http.base_url = httpx.URL("http://coordinator")
    client._http._transport = transport
    recovered = custody.recover()
    assert recovered.observation == request.observation and recovered.revision == 1
    assert custody.settled == (request, recovered)


@pytest.mark.parametrize("action", ["snapshot", "recover", "project"])
@pytest.mark.parametrize("status", [404, 405, 501])
def test_provider_rollback_after_health_is_explicit_without_any_fallback(
    owner: Any, action: str, status: int,
) -> None:
    _base, _sources, queue, _server, client, custody, _requests, _after = owner
    request = custody.prepare()
    transport = client._http._transport
    calls = []

    def rollback(http_request: httpx.Request) -> httpx.Response:
        calls.append(http_request.url.path)
        if http_request.url.path == "/health":
            return transport.handle_request(http_request)
        return httpx.Response(status, json={"detail": "synthetic provider rollback"})

    client._http._transport = httpx.MockTransport(rollback)
    with pytest.raises(DispatchUpgradeRequired, match="no longer supports"):
        if action == "snapshot":
            client.pool_configured_cohort()
        elif action == "recover":
            client.recover_pool_configured_cohort(request.observation, expected_revision=0)
        else:
            client.observe_pool_configured_cohort(
                request.observation, expected_revision=0, boundary=nullcontext,
            )
    assert calls == ["/health", "/pool-catalog/cohort" + ("" if action == "snapshot" else f"/{action}")]
    assert queue.pool_configured_cohort().revision == 0 and custody.pending is request
