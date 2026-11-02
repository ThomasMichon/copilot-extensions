"""Explicit caller negotiation crosses actual parser and coordinator boundaries."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import asdict

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agent_dispatch import __main__ as cli
from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue import Status, TaskQueue
from agent_dispatch.queue_pool_placement import pool_placement_contract
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import pool, publish, record_native, task

q = placement_controls.q


@pytest.fixture
def api(q: TaskQueue) -> Iterator[TestClient]:
    with TestClient(placement_controls.create_app(
        q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    )) as client:
        yield client


def connected(
    api: TestClient, requests: list[httpx.Request], *, control: bool = True,
    rewrite: Callable[[httpx.Request, httpx.Response], httpx.Response] | None = None,
) -> DispatchClient:
    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = api.request(
            request.method, str(request.url), headers=dict(request.headers), content=request.content,
        )
        result = httpx.Response(response.status_code, content=response.content)
        return rewrite(request, result) if rewrite else result

    return DispatchClient(
        "http://testserver", token="data", control_token="control" if control else None,
        transport=httpx.MockTransport(send),
    )


def test_real_placement_and_data_only_worker_acceptance(q: TaskQueue, api: TestClient) -> None:
    revision = publish(q, pool())
    requested = task(q)
    requests: list[httpx.Request] = []
    with connected(api, requests) as allocator:
        result = allocator.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision)
    reservation = q.get_reservation(result["reservation"]["key"])
    assert q.get(requested.id).status == Status.CLAIMED
    assert not reservation.session_handle and not reservation.worktree
    assert result["reserved"] is True
    record_native(q, reservation)
    with connected(api, requests, control=False) as worker:
        for _ in range(2):
            accepted = worker.start_pool_task(
                requested.id, "host-a/worktree", owner_session_id="native",
                expected_generation=result["assignment"]["task_generation"],
            )
            assert accepted["status"] == Status.STARTED
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/pool-placements/contract"), ("POST", "/pool-placements"),
        ("GET", "/health"), ("POST", f"/tasks/{requested.id}/pool-start"),
        ("GET", "/health"), ("POST", f"/tasks/{requested.id}/pool-start"),
    ]
    assert requests[2].headers["Authorization"] == placement_controls.headers("data")["Authorization"]
    assert len(q.list_reservations(task_id=requested.id)) == 1
    assert len(q.attachment_history(requested.id)) == 1


@pytest.mark.parametrize("operation", ["place", "start"])
@pytest.mark.parametrize("contract", [
    None, [], {}, {"protocol": "pool_placement/v2"},
    {**pool_placement_contract(), "supported_versions": [True]},
    {**pool_placement_contract(), "supported_versions": [2]},
    {**pool_placement_contract(), "supported_drivers": ["ordinary-bridge"]},
    {**pool_placement_contract(), "atomic_claim_before_spawn": 1},
    {**pool_placement_contract(), "affirmative_worker_start": False},
])
def test_missing_or_incompatible_live_contract_never_writes(operation: str, contract: object) -> None:
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"pool_placement": contract} if operation == "start" else contract)

    with DispatchClient("http://example.test", transport=httpx.MockTransport(send)) as client:
        with pytest.raises(DispatchUpgradeRequired):
            if operation == "place":
                client.place_pool_task("task", reserved_by="allocator", catalog_revision=3)
            else:
                client.start_pool_task("task", "host/worktree", owner_session_id="native", expected_generation=7)
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("status", [401, 403, 404, 405, 409, 501, 503])
def test_contract_refusals_do_not_fall_back(status: int) -> None:
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"detail": "refused"})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(send)) as client:
        with pytest.raises(DispatchError) as error:
            client.place_pool_task("task", reserved_by="allocator", catalog_revision=3)
    assert error.value.status_code == (426 if status in {404, 405, 501} else status)
    assert len(requests) == 1 and requests[0].method == "GET"


def test_support_is_not_cached_across_provider_rollback(q: TaskQueue, api: TestClient) -> None:
    revision = publish(q, pool(capacity=2))
    first, second = task(q), task(q)
    requests: list[httpx.Request] = []
    rolled_back = False

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if rolled_back and request.url.path == "/pool-placements/contract":
            return httpx.Response(404, json={"detail": "legacy provider"})
        return response

    with connected(api, requests, rewrite=rewrite) as client:
        client.place_pool_task(first.id, reserved_by="allocator", catalog_revision=revision)
        rolled_back = True
        with pytest.raises(DispatchUpgradeRequired):
            client.place_pool_task(second.id, reserved_by="allocator", catalog_revision=revision)
    assert q.get(first.id).status == Status.CLAIMED and q.get(second.id).status == Status.QUEUED
    assert sum(r.method == "POST" for r in requests) == 1


@pytest.mark.parametrize("field,value", [
    ("reserved", 1), ("version", True), ("assignment.task_id", "other"),
    ("assignment.task_generation", True), ("assignment.catalog_revision", -1),
    ("assignment.machine", ""), ("reservation.key", "different"),
    ("reservation.reserved_by", "different"), ("reservation.state", "spawned"),
    ("reservation.startup_revision", 0), ("reservation.release_requested", True),
])
def test_invalid_placement_reply_keeps_unknown_claim_without_replay(
    q: TaskQueue, api: TestClient, field: str, value: object,
) -> None:
    revision = publish(q, pool())
    requested = task(q)
    requests: list[httpx.Request] = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.url.path == "/pool-placements":
            result = deepcopy(response.json())
            prefix, separator, key = field.partition(".")
            (result[prefix] if separator else result)[key if separator else prefix] = value
            return httpx.Response(200, json=result)
        return response

    with connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchError, match="outcome is unknown") as error:
            client.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision)
        assert error.value.status_code == 502
    assert q.get(requested.id).status == Status.CLAIMED
    assert len(q.list_reservations(task_id=requested.id)) == 1
    assert sum(r.method == "POST" for r in requests) == 1
    with connected(api, requests) as client:
        replay = client.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision - 1)
    assert replay["reserved"] is False
    assert replay["assignment"]["catalog_revision"] == revision


@pytest.mark.parametrize("generation,native", [(0, "native"), (-1, "native"), (7, None), (7, " ")])
def test_cli_pool_start_requires_real_native_and_generation_before_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    generation: int, native: str | None,
) -> None:
    monkeypatch.setattr(cli, "_client", lambda *_: pytest.fail("invalid start reached coordinator"))
    argv = ["start", "task", "--pool-generation", str(generation)]
    if native is not None:
        argv += ["--owner-session-id", native]
    args = cli.build_parser().parse_args(argv)
    assert args.func(args) == 2
    assert "--pool-generation requires" in capsys.readouterr().err


def test_cli_pool_start_uses_current_native_generation_and_does_not_claim(
    q: TaskQueue, api: TestClient, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    revision = publish(q, pool())
    requested = task(q)
    placed = q.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision)
    record_native(q, placed.reservation)
    requests: list[httpx.Request] = []
    monkeypatch.setattr(cli, "_client", lambda *_: connected(api, requests, control=False))
    monkeypatch.setattr(cli, "_resolve_owner", lambda *_args, **_kw: "host-a/worktree")
    args = cli.build_parser().parse_args([
        "start", requested.id, "--pool-generation", str(placed.assignment.task_generation),
        "--owner-session-id", "native",
    ])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == Status.STARTED
    assert json.loads(requests[-1].content) == {
        "worker_id": "host-a/worktree", "owner_session_id": "native",
        "expected_generation": placed.assignment.task_generation, "expected_owner_session_id": None,
        "pool_placement_version": 1,
    }
    assert [r.url.path for r in requests] == ["/health", f"/tasks/{requested.id}/pool-start"]


@pytest.mark.parametrize("wrong", ["worker", "native", "generation"])
def test_real_provider_refuses_wrong_acceptance_without_fallback(
    q: TaskQueue, api: TestClient, wrong: str,
) -> None:
    revision = publish(q, pool())
    requested = task(q)
    placed = q.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision)
    record_native(q, placed.reservation)
    before = q.get(requested.id)
    requests: list[httpx.Request] = []
    with connected(api, requests, control=False) as client:
        with pytest.raises(DispatchError) as error:
            client.start_pool_task(
                requested.id, "host-a/other" if wrong == "worker" else "host-a/worktree",
                owner_session_id="other" if wrong == "native" else "native",
                expected_generation=placed.assignment.task_generation + (wrong == "generation"),
            )
    assert error.value.status_code == 409 and q.get(requested.id) == before
    assert len(requests) == 2


def test_explicit_pool_start_cannot_silently_accept_an_interactive_claim(q: TaskQueue, api: TestClient) -> None:
    requested = task(q)
    claimed = q.claim_one("host-a/worktree", task_id=requested.id)
    requests: list[httpx.Request] = []
    with connected(api, requests, control=False) as client:
        with pytest.raises(DispatchError, match="allocator-owned pool assignment") as error:
            client.start_pool_task(
                requested.id, "host-a/worktree", owner_session_id="native",
                expected_generation=claimed.generation,
            )
        assert error.value.status_code == 409
        assert q.get(requested.id) == claimed
        assert client.start(requested.id, "host-a/worktree")["status"] == Status.STARTED


@pytest.mark.parametrize("versions", [None, [], [True], [2]])
def test_start_requires_live_explicit_assignment_enforcement(versions: object) -> None:
    requests = []
    contract = {**pool_placement_contract(), "explicit_start_versions": versions}

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"pool_placement": contract})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(send)) as client:
        with pytest.raises(DispatchUpgradeRequired, match="assignment start enforcement"):
            client.start_pool_task("task", "host/worktree", owner_session_id="native", expected_generation=7)
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.parametrize("field,value", [
    ("id", "other"), ("status", "claimed"), ("owner", "host-a/other"),
    ("owner_session_id", "other-native"), ("generation", True), ("generation", 999),
])
def test_invalid_acceptance_reply_is_unknown_and_same_request_can_be_observed(
    q: TaskQueue, api: TestClient, field: str, value: object,
) -> None:
    revision = publish(q, pool())
    requested = task(q)
    placed = q.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision)
    record_native(q, placed.reservation)
    requests: list[httpx.Request] = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.method == "POST":
            result = response.json()
            result[field] = value
            return httpx.Response(200, json=result)
        return response

    options = {"owner_session_id": "native", "expected_generation": placed.assignment.task_generation}
    with connected(api, requests, control=False, rewrite=rewrite) as client:
        with pytest.raises(DispatchError, match="outcome is unknown") as error:
            client.start_pool_task(requested.id, "host-a/worktree", **options)
        assert error.value.status_code == 502
    before = q.get(requested.id)
    assert before.status == Status.STARTED
    assert sum(r.method == "POST" for r in requests) == 1
    with connected(api, requests, control=False) as client:
        assert client.start_pool_task(requested.id, "host-a/worktree", **options)["status"] == Status.STARTED
    assert q.get(requested.id) == before
    assert len(q.attachment_history(requested.id)) == 1


@pytest.mark.parametrize("version", [None, True, 0, 2, "1"])
def test_explicit_start_wire_version_is_strict_without_mutation(
    q: TaskQueue, api: TestClient, version: object,
) -> None:
    requested = task(q)
    before = q.claim_one("host-a/worktree", task_id=requested.id)
    response = api.post(f"/tasks/{requested.id}/pool-start", headers=placement_controls.headers("data"), json={
        "worker_id": "host-a/worktree", "owner_session_id": "native",
        "expected_generation": before.generation, "pool_placement_version": version,
    })
    assert response.status_code == 422 and q.get(requested.id) == before


def test_legacy_start_does_not_require_new_health_or_contract() -> None:
    requests = []

    def send(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST" and request.url.path == "/tasks/task/start"
        assert json.loads(request.content) == {"worker_id": "host/worktree"}
        return httpx.Response(200, json={"id": "task", "status": "started"})

    with DispatchClient("http://legacy.test", transport=httpx.MockTransport(send)) as client:
        assert client.start("task", "host/worktree")["status"] == Status.STARTED
    assert len(requests) == 1


def test_placement_keeps_prior_provider_contract_and_additive_response_compatibility(
    q: TaskQueue, api: TestClient,
) -> None:
    revision = publish(q, pool())
    requested = task(q)
    requests: list[httpx.Request] = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        result = response.json()
        if request.url.path == "/pool-placements/contract":
            result.pop("explicit_start_versions")
            result["supported_versions"].append(2)
        result["future_optional_property"] = "ignored"
        return httpx.Response(response.status_code, json=result)

    with connected(api, requests, rewrite=rewrite) as client:
        result = client.place_pool_task(requested.id, reserved_by="allocator", catalog_revision=revision)
    assert result["reserved"] is True and q.get(requested.id).status == Status.CLAIMED


class LegacyStartBody(BaseModel):
    worker_id: str
    owner_session_id: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None


def test_rollback_between_health_and_acceptance_refuses_before_mutation(
    q: TaskQueue, api: TestClient,
) -> None:
    requested = task(q)
    claimed = q.claim_one("host-a/worktree", task_id=requested.id)
    old_provider = FastAPI()
    legacy_starts = []

    @old_provider.post("/tasks/{task_id}/start")
    def legacy_start(task_id: str, body: LegacyStartBody) -> dict:
        legacy_starts.append(task_id)
        return asdict(q.start(
            task_id, body.worker_id, owner_session_id=body.owner_session_id,
            expected_generation=body.expected_generation,
            expected_owner_session_id=body.expected_owner_session_id,
        ))

    requests = []
    with TestClient(old_provider) as prior:
        def send(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            provider = api if request.method == "GET" else prior
            response = provider.request(
                request.method, str(request.url), headers=dict(request.headers), content=request.content,
            )
            return httpx.Response(response.status_code, content=response.content)

        with DispatchClient("http://testserver", token="data", transport=httpx.MockTransport(send)) as client:
            with pytest.raises(DispatchError) as error:
                client.start_pool_task(
                    requested.id, "host-a/worktree", owner_session_id="native",
                    expected_generation=claimed.generation,
                )
            assert error.value.status_code == 404
            assert q.get(requested.id) == claimed and not legacy_starts
            assert [(r.method, r.url.path) for r in requests] == [
                ("GET", "/health"), ("POST", f"/tasks/{requested.id}/pool-start"),
            ]
            # The prior ordinary route really can start this claim if invoked.
            response = prior.post(f"/tasks/{requested.id}/start", json={
                "worker_id": "host-a/worktree", "owner_session_id": "native",
                "expected_generation": claimed.generation, "pool_placement_version": 1,
            })
            assert response.status_code == 200 and response.json()["status"] == Status.STARTED
            assert legacy_starts == [requested.id]
