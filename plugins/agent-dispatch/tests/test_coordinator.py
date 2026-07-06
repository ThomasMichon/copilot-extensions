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
def server_url(app):
    # Run a real uvicorn server on an ephemeral port so the sync client (and SSE)
    # can be exercised over real HTTP.
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    probe = DispatchClient(url)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            probe.health()
            break
        except Exception:  # server still starting up
            time.sleep(0.05)
    else:
        probe.close()
        raise RuntimeError("coordinator did not start")
    probe.close()

    yield url

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def client(server_url):
    c = DispatchClient(server_url)
    yield c
    c.close()


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


# -- SSE event stream --------------------------------------------------------


def test_sse_stream_delivers_lifecycle_events(server_url):
    streamer = DispatchClient(server_url)
    mutator = DispatchClient(server_url)
    received: list[dict] = []

    def collect():
        try:
            for ev in streamer.stream_events():
                received.append(ev)
                if ev.get("type") == "task.completed":
                    break
        except Exception:
            return  # stream closed / server stopped -- best effort

    t = threading.Thread(target=collect, daemon=True)
    t.start()

    # Deterministic readiness: wait until the streamer's subscription is
    # registered server-side before producing events.
    deadline = time.time() + 5
    while time.time() < deadline and mutator.health().get("subscribers", 0) < 1:
        time.sleep(0.05)
    assert mutator.health()["subscribers"] >= 1

    tid = mutator.create("streamed")["id"]
    mutator.claim("w1")
    mutator.start(tid, "w1")
    mutator.complete(tid, "w1")

    t.join(timeout=5)
    streamer.close()
    mutator.close()

    types = [e["type"] for e in received]
    assert "task.created" in types
    assert "task.claimed" in types
    assert "task.completed" in types
    created = next(e for e in received if e["type"] == "task.created")
    assert created["task"]["id"] == tid


def test_health_reports_zero_subscribers_initially(api):
    assert api.get("/health").json()["subscribers"] == 0


def test_claim_by_id_over_http(api):
    api.post("/tasks", json={"title": "a"})
    tid_b = api.post("/tasks", json={"title": "b"}).json()["id"]
    got = api.post("/claim", json={"worker_id": "w1", "task_id": tid_b}).json()
    assert got["id"] == tid_b
    # a different specific-id claim for an already-claimed task returns null
    assert api.post("/claim", json={"worker_id": "w2", "task_id": tid_b}).json() is None


def test_mine_over_http(api):
    api.post("/tasks", json={"title": "for-me", "target_worktree": "wt-1"})
    tid = api.post("/tasks", json={"title": "to-own"}).json()["id"]
    api.post("/claim", json={"machine": "m1", "worktree": "wt-1", "task_id": tid})
    r = api.get("/tasks/mine", params={"machine": "m1", "worktree": "wt-1"}).json()
    assert any(t["title"] == "for-me" for t in r["assigned"])
    assert any(t["id"] == tid and t["owner"] == "m1/wt-1" for t in r["owned"])


def test_claim_composes_owner_from_machine_worktree(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    got = api.post("/claim", json={"machine": "m1", "worktree": "wt-1", "task_id": tid}).json()
    assert got["owner"] == "m1/wt-1"


def test_claim_without_identity_is_422(api):
    api.post("/tasks", json={"title": "x"})
    assert api.post("/claim", json={"capabilities": []}).status_code == 422


def test_payload_endpoint_inline(api):
    tid = api.post("/tasks", json={"title": "t", "payload_inline": "small"}).json()["id"]
    r = api.get(f"/tasks/{tid}/payload").json()
    assert r["inline"] is True
    assert r["payload"] == "small"
    assert r["ref"] is None


def test_payload_endpoint_spilled_blob(api):
    big = "m" * 5000  # over the default 4096 threshold -> spills to a blob
    tid = api.post("/tasks", json={"title": "t", "payload_inline": big}).json()["id"]
    task = api.get(f"/tasks/{tid}").json()
    assert task["payload_inline"] is None
    assert task["payload_ref"].startswith("blob:")
    r = api.get(f"/tasks/{tid}/payload").json()
    assert r["inline"] is False
    assert r["payload"] == big


def test_payload_endpoint_missing_task_404(api):
    assert api.get("/tasks/nope/payload").status_code == 404
