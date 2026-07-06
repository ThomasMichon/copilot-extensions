"""Tests for the agent-dispatch coordinator HTTP API and client."""

from __future__ import annotations

import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status, TaskQueue


@pytest.fixture
def app(tmp_path):
    return create_app(TaskQueue(tmp_path / "tasks.db"))


@pytest.fixture
def api(app):
    return TestClient(app)


@pytest.fixture
def client(app):
    # Drive the real (synchronous) DispatchClient against a live uvicorn server.
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    c = DispatchClient(f"http://127.0.0.1:{port}")
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            c.health()
            break
        except Exception:  # server still starting up
            time.sleep(0.05)
    else:
        raise RuntimeError("coordinator did not start")

    yield c

    c.close()
    server.should_exit = True
    thread.join(timeout=5)


# -- coordinator routes ------------------------------------------------------


def test_health(api):
    r = api.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_get(api):
    r = api.post("/tasks", json={"title": "work", "prompt": "go"})
    assert r.status_code == 200
    task = r.json()
    assert task["status"] == Status.QUEUED
    got = api.get(f"/tasks/{task['id']}").json()
    assert got["title"] == "work"


def test_get_missing_is_404(api):
    assert api.get("/tasks/nope").status_code == 404


def test_full_lifecycle_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    claimed = api.post("/claim", json={"worker_id": "w1"}).json()
    assert claimed["id"] == tid and claimed["status"] == Status.CLAIMED
    started = api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"}).json()
    assert started["status"] == Status.STARTED
    done = api.post(
        f"/tasks/{tid}/complete", json={"worker_id": "w1", "result_ref": "pr/1"}
    ).json()
    assert done["status"] == Status.COMPLETED


def test_claim_empty_returns_null(api):
    assert api.post("/claim", json={"worker_id": "w1"}).json() is None


def test_illegal_transition_is_409(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    # cannot start a task that was never claimed
    r = api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    assert r.status_code == 409


def test_abandon_requires_permission_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    assert api.post(f"/tasks/{tid}/abandon", json={"permitted": False}).status_code == 409
    ok = api.post(f"/tasks/{tid}/abandon", json={"permitted": True, "reason": "dup"})
    assert ok.status_code == 200 and ok.json()["status"] == Status.ABANDONED


def test_proposed_not_claimable_then_approved(api):
    tid = api.post("/tasks", json={"title": "draft", "proposed": True}).json()["id"]
    assert api.post("/claim", json={"worker_id": "w1"}).json() is None
    api.post(f"/tasks/{tid}/approve")
    assert api.post("/claim", json={"worker_id": "w1"}).json()["id"] == tid


def test_capability_gate_over_http(api):
    api.post("/tasks", json={"title": "log", "requires": ["logger"]})
    assert api.post("/claim", json={"worker_id": "w1"}).json() is None
    got = api.post("/claim", json={"worker_id": "w1", "capabilities": ["logger"]}).json()
    assert got is not None


def test_list_and_find(api):
    api.post("/tasks", json={"title": "alpha task"})
    api.post("/tasks", json={"title": "beta task"})
    assert len(api.get("/tasks").json()) == 2
    found = api.get("/tasks", params={"q": "alpha"}).json()
    assert len(found) == 1 and found[0]["title"] == "alpha task"


# -- auth --------------------------------------------------------------------


def test_bearer_auth_enforced(tmp_path):
    app = create_app(TaskQueue(tmp_path / "t.db"), token="secret")
    api = TestClient(app)
    assert api.get("/health").status_code == 401
    ok = api.get("/health", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert api.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


# -- the DispatchClient against the app -------------------------------------


def test_client_round_trip(client):
    t = client.create("via client", requires=["review"])
    assert t["status"] == Status.QUEUED
    assert client.claim("w1") is None  # lacks capability
    claimed = client.claim("w1", ["review"])
    assert claimed["id"] == t["id"]
    client.start(t["id"], "w1")
    done = client.complete(t["id"], "w1", result_ref="pr/9")
    assert done["status"] == Status.COMPLETED
    trail = [e["to_status"] for e in client.events(t["id"])]
    assert trail == [Status.QUEUED, Status.CLAIMED, Status.STARTED, Status.COMPLETED]


def test_client_error_maps_to_dispatch_error(client):
    with pytest.raises(DispatchError) as exc:
        client.get("missing")
    assert exc.value.status_code == 404


def test_client_recover(client):
    t = client.create("leased")
    client.claim("w1", lease_seconds=0)  # already expired
    assert client.recover()["recovered"] == 1
    assert client.get(t["id"])["status"] == Status.QUEUED
