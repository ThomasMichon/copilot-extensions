"""Recovery boundaries use control authority, never task-scoped credentials."""

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.task_scope import TASK_SCOPE_HEADER
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_task_scope_auth import TOKEN, CONTROL_TOKEN, _auth, _claim_and_start


@pytest.fixture
def api(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    with TestClient(create_app(queue, token=TOKEN, control_token=CONTROL_TOKEN)) as client:
        yield client


def request(api, task_id):
    diagnostics = api.get(f"/tasks/{task_id}/diagnostics", headers=_auth(TOKEN))
    assert diagnostics.status_code == 200
    return {
        "expected_revision": diagnostics.json()["revision"], "request_id": "request-1",
        "actor": "operator", "reason": "explicit recovery",
    }


def test_control_recovery_has_no_worker_bearer_escalation(api):
    task_id = api.app.state.queue.create("work").id
    body = request(api, task_id)
    path = f"/tasks/{task_id}/recovery/reset_proposed"
    assert api.post(path, json=body, headers=_auth(TOKEN)).status_code == 403
    response = api.post(path, json=body, headers=_auth(CONTROL_TOKEN))
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "proposed"
    replay = api.post(path, json=body, headers=_auth(CONTROL_TOKEN))
    assert replay.status_code == 200
    assert replay.json()["replayed"]


def test_missing_revision_and_unrecognized_outcome_are_rejected(api):
    task_id = api.app.state.queue.create("work").id
    body = request(api, task_id)
    del body["expected_revision"]
    assert api.post(
        f"/tasks/{task_id}/recovery/reset_queued", json=body,
        headers=_auth(CONTROL_TOKEN),
    ).status_code == 422
    assert api.post(
        f"/tasks/{task_id}/wakes/reconcile",
        json={**request(api, task_id), "outbox": "wake_outbox",
              "wake_id": "wake-1", "outcome": "not_sent"},
        headers=_auth(CONTROL_TOKEN),
    ).status_code == 422


def test_task_token_cannot_inspect_or_control_recovery(api):
    task_id, token = _claim_and_start(api)
    headers = {TASK_SCOPE_HEADER: token}
    assert api.get(f"/tasks/{task_id}/diagnostics", headers=headers).status_code == 403
    assert api.post(
        f"/tasks/{task_id}/recovery/detach_agent",
        json=request(api, task_id), headers=headers,
    ).status_code == 403


def test_old_reset_default_and_target_detach_meanings_are_preserved(api):
    queue = api.app.state.queue
    task_id = queue.create("work", target_worktree="workspace").id
    reset = api.post(f"/tasks/{task_id}/reset", json={}, headers=_auth(TOKEN))
    assert reset.status_code == 200
    assert reset.json()["status"] == "proposed"
    assert reset.json()["target_worktree"] == "workspace"
    detached = api.post(f"/tasks/{task_id}/detach", headers=_auth(TOKEN))
    assert detached.status_code == 200
    assert detached.json()["target_worktree"] is None


def test_recovery_support_is_advertised_by_the_running_coordinator(api):
    health = api.get("/health", headers=_auth(TOKEN))
    assert health.status_code == 200
    assert health.json()["task_recovery"] == {
        "protocol": "task_recovery/v1",
        "operations": ["detach_agent", "reset_proposed", "reset_queued", "reconcile_wake"],
    }


def test_missing_control_configuration_does_not_fall_back_to_local_trust(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = queue.create("work").id
    with TestClient(create_app(queue, token=TOKEN)) as client:
        response = client.post(
            f"/tasks/{task_id}/recovery/reset_queued",
            json=request(client, task_id), headers=_auth(TOKEN),
        )
        assert response.status_code == 503
    assert queue.get(task_id).status == "queued"
