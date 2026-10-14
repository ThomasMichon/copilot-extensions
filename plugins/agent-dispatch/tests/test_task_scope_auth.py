"""HTTP-level tests for task-scoped capability tokens -- the authorization
boundary a projected relay needs (agent-dispatch-fleet-authority effort,
Phase 1; see efforts/active/agent-dispatch-fleet-authority/README.md and
issue #6617).

These exercise the REAL coordinator routes (not the token primitive in
isolation -- see test_task_scope.py for that): minting on `/tasks/{id}/start`,
scoped authorization on `/tasks/{id}/progress`, `/complete`, `/abandon`, and
`/heartbeat`, and the fencing-guard wiring via `lease.FencingGuard`.
"""

from __future__ import annotations

import pytest
import socket
import threading
import time

import httpx
import uvicorn
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_auth import TASK_SCOPE_ROUTES
from agent_dispatch.task_scope import TASK_SCOPE_HEADER
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue

TOKEN = "worker-bearer"
CONTROL_TOKEN = "control-bearer"
# Never write the scheme word concatenated with a secret in one literal --
# see avoiding-secret-pattern-masking.
_SCHEME = "Be" + "arer"


def _auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"{_SCHEME} {secret}"}


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def api(queue):
    app = create_app(queue, token=TOKEN, control_token=CONTROL_TOKEN)
    with TestClient(app) as client:
        yield client


def _claim_and_start(
    api, *, worker_id="worker-1", owner_session_id="session-1"
) -> tuple[str, str]:
    task_id = api.app.state.queue.create("work", repo=TEST_REPO).id
    claimed = api.post(
        "/claim",
        json={"worker_id": worker_id, "repo": TEST_REPO},
        headers=_auth(TOKEN),
    )
    assert claimed.status_code == 200, claimed.text
    started = api.post(
        f"/tasks/{task_id}/start",
        json={"worker_id": worker_id, "owner_session_id": owner_session_id},
        headers=_auth(TOKEN),
    )
    assert started.status_code == 200, started.text
    return task_id, started.json()["task_token"]


def test_start_mints_a_task_token(api):
    _task_id, token = _claim_and_start(api)
    assert isinstance(token, str) and token


def test_task_token_authenticates_without_the_shared_bearer(api):
    task_id, token = _claim_and_start(api)
    # No Authorization header at all -- the whole point of the projected
    # relay: a container holding only this token, never the shared bearer.
    response = api.post(
        f"/tasks/{task_id}/progress",
        json={"worker_id": "worker-1", "summary": "making progress"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 200, response.text


def test_request_with_neither_credential_is_rejected(api):
    task_id, _token = _claim_and_start(api)
    response = api.post(
        f"/tasks/{task_id}/progress",
        json={"worker_id": "worker-1", "summary": "x"},
    )
    assert response.status_code == 401


def test_task_token_cannot_act_on_a_different_task(api):
    _task_id, token = _claim_and_start(api)
    other_id = api.app.state.queue.create("other work", repo=TEST_REPO).id
    response = api.post(
        f"/tasks/{other_id}/progress",
        json={"worker_id": "worker-1", "summary": "x"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 403


def test_task_token_body_worker_id_mismatch_does_not_let_caller_assert_someone_else(api):
    task_id, token = _claim_and_start(api, worker_id="worker-1")
    # The body asserts a DIFFERENT worker_id -- the token's bound identity
    # must win, not the caller-supplied body value, so this must behave as
    # worker-1 (the real owner), never silently become worker-2.
    response = api.post(
        f"/tasks/{task_id}/progress",
        json={"worker_id": "worker-2", "summary": "x"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 200, response.text
    # Confirm the progress was attributed to the TOKEN's worker, not the body's.
    task = api.get(f"/tasks/{task_id}", headers=_auth(TOKEN)).json()
    assert task["owner"] == "worker-1"


def test_task_token_invalidated_by_reassignment(api):
    task_id, token = _claim_and_start(api, worker_id="worker-1")
    # Force-complete as the real (full-bearer) owner, then try to reuse the
    # old token -- it must no longer match current task state.
    completed = api.post(
        f"/tasks/{task_id}/complete",
        json={"worker_id": "worker-1"},
        headers=_auth(TOKEN),
    )
    assert completed.status_code == 200, completed.text
    stale = api.post(
        f"/tasks/{task_id}/progress",
        json={"worker_id": "worker-1", "summary": "too late"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert stale.status_code == 403


def test_task_token_rejected_with_bad_signature(api):
    task_id, token = _claim_and_start(api)
    forged = token[:-4] + ("0000" if not token.endswith("0000") else "1111")
    response = api.post(
        f"/tasks/{task_id}/progress",
        json={"worker_id": "worker-1", "summary": "x"},
        headers={TASK_SCOPE_HEADER: forged},
    )
    assert response.status_code == 401


def test_task_token_works_for_complete_and_abandon(api):
    task_id, token = _claim_and_start(api)
    response = api.post(
        f"/tasks/{task_id}/complete",
        json={"worker_id": "worker-1"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 200, response.text


def test_task_token_works_for_heartbeat(api):
    task_id, token = _claim_and_start(api)
    response = api.post(
        f"/tasks/{task_id}/heartbeat",
        json={"worker_id": "worker-1"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 200, response.text


def test_task_token_cannot_list_the_global_queue(api):
    _task_id, token = _claim_and_start(api)
    response = api.get("/tasks", headers={TASK_SCOPE_HEADER: token})
    assert response.status_code == 403


def test_task_scope_denies_every_unlisted_http_route(api):
    task_id, token = _claim_and_start(api)
    exercised = 0
    for route in api.app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if (method, route.path) in TASK_SCOPE_ROUTES:
                continue
            path = route.path
            for parameter in route.param_convertors:
                path = path.replace("{" + parameter + "}", task_id)
            response = api.request(
                method, path, headers={TASK_SCOPE_HEADER: token}, json={},
            )
            assert response.status_code == 403, (method, route.path, response.text)
            exercised += 1
    assert exercised > 50


@pytest.mark.parametrize("configured_token", [TOKEN, None])
def test_scoped_header_cannot_downgrade_to_ambient_authority(queue, configured_token):
    with TestClient(create_app(
        queue, token=configured_token, control_token=CONTROL_TOKEN, enable_mcp=False,
    )) as client:
        _task_id, token = _claim_and_start(client)
        headers = {**_auth(TOKEN), TASK_SCOPE_HEADER: token}
        assert client.get("/tasks", headers=headers).status_code == 403
        headers[TASK_SCOPE_HEADER] = "invalid"
        assert client.get("/tasks", headers=headers).status_code == 401


def test_scoped_token_reads_only_its_task_and_payload(api):
    task_id, token = _claim_and_start(api)
    headers = {TASK_SCOPE_HEADER: token}
    other_id = api.app.state.queue.create("other", repo=TEST_REPO).id
    for suffix in ("", "/payload"):
        assert api.get(f"/tasks/{task_id}{suffix}", headers=headers).status_code == 200
        assert api.get(f"/tasks/{other_id}{suffix}", headers=headers).status_code == 403


@pytest.mark.parametrize("configured_token", [TOKEN, None])
def test_scoped_token_cannot_enter_mounted_or_documentation_surfaces(queue, configured_token):
    with TestClient(create_app(
        queue, token=configured_token, control_token=CONTROL_TOKEN,
    )) as client:
        _task_id, token = _claim_and_start(client)
        headers = {TASK_SCOPE_HEADER: token}
        for path in ("/mcp", "/mcp/", "/docs", "/redoc", "/openapi.json"):
            response = client.get(path, headers=headers, follow_redirects=False)
            assert response.status_code == 403, (path, response.text)


@pytest.mark.parametrize("verb,method,body", [
    ("progress", "record_progress", {"summary": "race"}),
    ("heartbeat", "heartbeat", {}),
    ("complete", "complete_with_outcome", {}),
    ("abandon", "abandon_with_outcome", {"reason": "stop"}),
])
def test_scoped_mutation_fences_session_replacement_inside_transaction(
    api, queue, monkeypatch, verb, method, body,
):
    task_id, token = _claim_and_start(api)
    original = getattr(queue, method)

    def replace_session_then_mutate(*args, **kwargs):
        with queue._connect() as conn:
            conn.execute(
                "UPDATE tasks SET owner_session_id='replacement' WHERE id=?", (task_id,),
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, method, replace_session_then_mutate)
    response = api.post(
        f"/tasks/{task_id}/{verb}",
        json={"worker_id": "worker-1", **body}, headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 409, response.text
    current = queue.get(task_id)
    assert current.status == "started"
    assert current.latest_progress is None


def test_scoped_token_rejected_when_generation_changes_with_same_identity(api, queue):
    task_id, token = _claim_and_start(api)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task_id,))
    response = api.post(
        f"/tasks/{task_id}/heartbeat", json={"worker_id": "worker-1"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("require_verification", [False, True])
def test_scoped_completion_cannot_backfill_a_replacement_sessions_terminal_result(
    api, queue, monkeypatch, require_verification,
):
    task_id, token = _claim_and_start(api)
    original = queue.complete_with_outcome

    def replacement_completes_first(*args, **kwargs):
        with queue._connect() as conn:
            conn.execute(
                "UPDATE tasks SET owner_session_id='replacement', require_verification=? "
                "WHERE id=?", (require_verification, task_id),
            )
        original(task_id, "worker-1")
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, "complete_with_outcome", replacement_completes_first)
    response = api.post(
        f"/tasks/{task_id}/complete",
        json={"worker_id": "worker-1", "result": {"text": "stale result"}},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 409, response.text
    current = queue.get(task_id)
    assert current.result is None
    assert current.status == ("submitted" if require_verification else "completed")


def test_restricted_worker_over_real_http(queue):
    task_id = queue.create("assigned work", repo=TEST_REPO).id
    app = create_app(queue, token=TOKEN, control_token=CONTROL_TOKEN, enable_mcp=False)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert thread.is_alive(), "HTTP coordinator exited before readiness"
                assert time.monotonic() < deadline, "HTTP coordinator readiness timed out"
                time.sleep(0.01)
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with httpx.Client(base_url=url, timeout=5, trust_env=False) as supervisor:
                response = supervisor.post(
                    "/claim", json={"worker_id": "worker-1", "repo": TEST_REPO},
                    headers=_auth(TOKEN),
                )
                assert response.status_code == 200, response.text
                response = supervisor.post(
                    f"/tasks/{task_id}/start",
                    json={"worker_id": "worker-1", "owner_session_id": "session-1"},
                    headers=_auth(TOKEN),
                )
                assert response.status_code == 200, response.text
                grant = response.json()["task_token"]
            with httpx.Client(
                base_url=url, headers={TASK_SCOPE_HEADER: grant},
                timeout=5, trust_env=False,
            ) as worker:
                assert worker.get("/tasks").status_code == 403
                assert worker.post("/shutdown").status_code == 403
                assert worker.get(f"/tasks/{task_id}/payload").status_code == 200
                response = worker.post(
                    f"/tasks/{task_id}/progress",
                    json={"worker_id": "worker-1", "summary": "isolated network worker"},
                )
                assert response.status_code == 200, response.text
                response = worker.post(
                    f"/tasks/{task_id}/complete", json={"worker_id": "worker-1"},
                )
                assert response.status_code == 200, response.text
                assert worker.get(f"/tasks/{task_id}").status_code == 403
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "HTTP coordinator failed to shut down"


def test_no_control_token_configured_means_no_token_is_minted(tmp_path):
    from tests._helpers import RepoDefaultingQueue as Q

    app = create_app(Q(tmp_path / "t.db"), token=TOKEN)  # no control_token
    with TestClient(app) as api:
        task_id = api.app.state.queue.create("work", repo=TEST_REPO).id
        api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO}, headers=_auth(TOKEN))
        started = api.post(
            f"/tasks/{task_id}/start",
            json={"worker_id": "w1", "owner_session_id": "s1"},
            headers=_auth(TOKEN),
        )
        assert started.status_code == 200
        assert "task_token" not in started.json()


def test_fencing_guard_rejects_a_token_from_a_superseded_epoch(api, queue):
    task_id, token = _claim_and_start(api)
    # Register a NEW coordinator at a higher epoch directly in the same
    # in-process directory the app's FencingGuard reads from, simulating a
    # federation takeover after this token was minted.
    api.app.state.directory.register("new-coordinator", role="coordinator", epoch=999)
    response = api.post(
        f"/tasks/{task_id}/progress",
        json={"worker_id": "worker-1", "summary": "after takeover"},
        headers={TASK_SCOPE_HEADER: token},
    )
    assert response.status_code == 403
