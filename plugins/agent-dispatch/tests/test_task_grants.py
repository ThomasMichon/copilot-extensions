"""Trusted renewal preserves ownership and never delegates minting to workers."""

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, verify_task_scope_token
from tests._helpers import TEST_REPO, RepoDefaultingQueue


def auth(secret):
    scheme = "Bear" + "er "
    return {"Authorization": scheme + secret}


@pytest.fixture
def grant_api(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    task = queue.create("renewable work", repo=TEST_REPO)
    queue.claim_one("worker", task_id=task.id, repo=TEST_REPO)
    task = queue.start(task.id, "worker", owner_session_id="native-session")
    with TestClient(create_app(
        queue, token="fleet", control_token="control", enable_mcp=False,
    )) as api:
        yield api, queue, task, {
            "worker_id": task.owner, "owner_session_id": task.owner_session_id,
            "expected_generation": task.generation, "expected_epoch": 0,
        }


def grant(api, task, body, headers=None):
    return api.post(
        f"/tasks/{task.id}/task-grant", json=body,
        headers=headers if headers is not None else auth("control"),
    )


def test_task_grant_renews_without_ownership_lease_or_audit_changes(grant_api, monkeypatch):
    api, queue, task, body = grant_api
    events = queue.events(task.id)
    monkeypatch.setattr("agent_dispatch.task_authority.time.time", lambda: 1_000_000)
    first = grant(api, task, body)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    first_claims = verify_task_scope_token("control", first.json()["task_token"], now=1_000_000)
    monkeypatch.setattr("agent_dispatch.task_authority.time.time", lambda: 1_000_060)
    second = grant(api, task, body)
    assert second.status_code == 200, second.text
    second_claims = verify_task_scope_token(
        "control", second.json()["task_token"], now=1_000_060,
    )
    assert first.json()["task_token"] != second.json()["task_token"]
    assert second_claims.exp == first_claims.exp + 60 == second.json()["expires_at"]
    assert (second_claims.task_id, second_claims.worker_id, second_claims.owner_session_id,
            second_claims.generation, second_claims.epoch) == (
        task.id, task.owner, task.owner_session_id, task.generation, 0,
    )
    assert queue.get(task.id) == task
    assert queue.events(task.id) == events
    assert "task_token" not in api.get(f"/tasks/{task.id}", headers=auth("fleet")).text


@pytest.mark.parametrize("headers,status", [
    ({}, 401), (auth("fleet"), 403), (auth("wrong"), 401),
])
def test_task_grant_requires_control_authority(grant_api, headers, status):
    api, _queue, task, body = grant_api
    assert grant(api, task, body, headers).status_code == status


def test_task_worker_cannot_renew_even_with_a_control_header(grant_api):
    api, _queue, task, body = grant_api
    token = grant(api, task, body).json()["task_token"]
    assert grant(api, task, body, {TASK_SCOPE_HEADER: token}).status_code == 403
    assert grant(api, task, body, {
        **auth("control"), TASK_SCOPE_HEADER: token,
    }).status_code == 403


@pytest.mark.parametrize("change", [
    {"worker_id": "other"}, {"owner_session_id": "other"},
    {"expected_generation": 999}, {"expected_epoch": 999},
])
def test_task_grant_rejects_changed_incarnation(grant_api, change):
    api, queue, task, body = grant_api
    assert grant(api, task, {**body, **change}).status_code == 409
    assert queue.get(task.id) == task


@pytest.mark.parametrize("change", [
    {"worker_id": ""}, {"owner_session_id": ""}, {"expected_generation": -1},
    {"expected_epoch": -1}, {"expected_generation": True},
    {"expected_epoch": "0"}, {"ttl_seconds": 999999},
])
def test_task_grant_rejects_invalid_fences(grant_api, change):
    api, queue, task, body = grant_api
    assert grant(api, task, {**body, **change}).status_code == 422
    assert queue.get(task.id) == task


@pytest.mark.parametrize("field", ["expected_epoch", "expected_generation", "owner_session_id"])
def test_task_grant_requires_explicit_incarnation(grant_api, field):
    api, _queue, task, body = grant_api
    del body[field]
    assert grant(api, task, body).status_code == 422


@pytest.mark.parametrize("transition", ["complete", "yield", "suspend"])
def test_task_grant_refuses_nonheld_tasks(grant_api, transition):
    api, _queue, task, body = grant_api
    response = api.post(
        f"/tasks/{task.id}/{transition}",
        json={"worker_id": "worker", "reason": "waiting"}, headers=auth("fleet"),
    )
    assert response.status_code == 200, response.text
    assert grant(api, task, body).status_code == 409


def test_task_grant_after_snapshot_race_is_unusable(grant_api, monkeypatch):
    api, queue, task, body = grant_api
    get = queue.get

    def supersede(task_id):
        snapshot = get(task_id)
        queue.yield_task(task_id, "worker")
        return snapshot

    with monkeypatch.context() as patch:
        patch.setattr(queue, "get", supersede)
        response = grant(api, task, body)
    assert response.status_code == 200, response.text
    assert api.get(
        f"/tasks/{task.id}", headers={TASK_SCOPE_HEADER: response.json()["task_token"]},
    ).status_code == 403
    assert queue.get(task.id).status == "queued"


def test_task_grant_checks_live_epoch_and_old_grants_are_fenced(grant_api):
    api, queue, task, body = grant_api
    directory = api.app.state.directory
    directory.register(
        "central", role="coordinator", epoch=4, machine="central", capabilities=[],
    )
    assert grant(api, task, body).status_code == 409
    response = grant(api, task, {**body, "expected_epoch": 4})
    assert response.status_code == 200, response.text
    token = response.json()["task_token"]
    assert verify_task_scope_token("control", token).epoch == 4
    assert api.get(f"/tasks/{task.id}", headers={TASK_SCOPE_HEADER: token}).status_code == 200
    directory.register(
        "central", role="coordinator", epoch=5, machine="central", capabilities=[],
    )
    assert grant(api, task, {**body, "expected_epoch": 4}).status_code == 409
    assert api.get(f"/tasks/{task.id}", headers={TASK_SCOPE_HEADER: token}).status_code == 403
    assert queue.get(task.id) == task


def test_task_grant_requires_bound_native_session_without_starting(grant_api):
    api, queue, _task, _body = grant_api
    task = queue.create("waiting for native worker", repo=TEST_REPO)
    queue.claim_one("worker", task_id=task.id, repo=TEST_REPO)
    task = queue.get(task.id)
    body = {
        "worker_id": "worker", "owner_session_id": "new-native-session",
        "expected_generation": task.generation, "expected_epoch": 0,
    }
    assert grant(api, task, body).status_code == 409
    bound = queue.bind_owner_session(
        task.id, "worker", "new-native-session", expected_generation=task.generation,
    )
    response = grant(api, bound, body)
    assert response.status_code == 200, response.text
    assert queue.get(task.id) == bound
    assert bound.status == "claimed" and bound.started_at is None


def test_expired_coordinator_presence_cannot_restore_stale_grants(grant_api, monkeypatch):
    api, queue, task, body = grant_api
    directory = api.app.state.directory
    clock = 1000.0
    monkeypatch.setattr(directory, "_clock", lambda: clock)
    standalone_token = grant(api, task, body).json()["task_token"]
    directory.register("central", role="coordinator", epoch=4)
    old_token = grant(api, task, {**body, "expected_epoch": 4}).json()["task_token"]
    # No guard read is needed to retain the newly registered coordinator epoch.
    directory.register("central", role="coordinator", epoch=5)
    clock += directory.ttl_seconds + 1
    assert directory.reap() == 1
    assert directory.discover_coordinator() is None
    for epoch in (0, 4):
        assert grant(api, task, {**body, "expected_epoch": epoch}).status_code == 409
    for token in (standalone_token, old_token):
        assert api.get(
            f"/tasks/{task.id}", headers={TASK_SCOPE_HEADER: token},
        ).status_code == 403
    restarted_queue = RepoDefaultingQueue(queue.db_path)
    with TestClient(create_app(
        restarted_queue, token="fleet", control_token="control", enable_mcp=False,
    )) as restarted:
        for epoch in (0, 4):
            assert grant(restarted, task, {**body, "expected_epoch": epoch}).status_code == 409
        for token in (standalone_token, old_token):
            assert restarted.get(
                f"/tasks/{task.id}", headers={TASK_SCOPE_HEADER: token},
            ).status_code == 403
        assert grant(restarted, task, {**body, "expected_epoch": 5}).status_code == 200
    assert queue.get(task.id) == task


def test_task_grant_unconfigured_signer_fails_closed(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    with TestClient(create_app(queue, token="fleet", enable_mcp=False)) as api:
        response = api.post("/tasks/missing/task-grant", headers=auth("fleet"), json={
            "worker_id": "worker", "owner_session_id": "session",
            "expected_generation": 0, "expected_epoch": 0,
        })
        assert response.status_code == 503


def test_task_grant_client_supplies_only_control_authority(grant_api):
    import httpx

    api, _queue, task, body = grant_api

    def send(request):
        assert request.headers["authorization"] == auth("control")["Authorization"]
        assert TASK_SCOPE_HEADER not in request.headers
        response = api.request(
            request.method, request.url.path, content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(response.status_code, content=response.content)

    with DispatchClient(
        "http://coordinator.test", token="fleet", control_token="control",
        transport=httpx.MockTransport(send),
    ) as client:
        response = client.task_grant(
            task.id, body["worker_id"], body["owner_session_id"],
            expected_generation=body["expected_generation"], expected_epoch=0,
        )
    assert verify_task_scope_token("control", response["task_token"]).task_id == task.id
