"""Tests for the agent-dispatch coordinator HTTP API and client."""

from __future__ import annotations

import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue


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


def test_client_suspend_resume_release_routes(client, monkeypatch):
    from agent_dispatch import bridge

    monkeypatch.setattr(
        bridge, "resume_steered_owner", lambda *_args, **_kwargs: False
    )
    task = client.create("wait")
    owner = client.claim(worker_id="worker-1")["owner"]
    client.start(task["id"], owner)
    parked = client.suspend(
        task["id"], owner, reason="waiting for an external result"
    )
    assert parked["status"] == Status.SUSPENDED
    resumed = client.resume(task["id"], owner)
    assert resumed["status"] == Status.STARTED
    assert resumed["resume_woken"] is None
    assert resumed["resume_wake_status"] == "pending"
    client.suspend(task["id"], owner, reason="waiting again")
    released = client.release(
        task["id"], owner, reason="use a replacement"
    )
    assert released["status"] == Status.QUEUED
    assert released["owner"] is None


def test_client_resume_can_atomically_adopt_successor_session(
    client, monkeypatch
):
    from agent_dispatch import bridge, coordinator

    sessions = iter(["session-old", "session-new"])
    monkeypatch.setattr(
        coordinator, "_resolve_owner_session_id", lambda _owner: next(sessions)
    )
    monkeypatch.setattr(
        bridge, "resume_steered_owner", lambda *_args, **_kwargs: False
    )
    task = client.create("continue after handoff")
    owner = client.claim(worker_id="worker-1")["owner"]
    started = client.start(task["id"], owner)
    parked = client.suspend(task["id"], owner, reason="handoff")

    resumed = client.resume(
        task["id"],
        owner,
        wake=False,
        adopt_session=True,
        expected_owner_session_id=parked["owner_session_id"],
        expected_generation=parked["generation"],
    )

    assert started["owner_session_id"] == "session-old"
    assert resumed["owner_session_id"] == "session-new"
    assert resumed["generation"] == parked["generation"] + 1
    assert resumed["resume_wake_status"] == "not_requested"


def test_client_completes_suspended_task_without_wake(client, monkeypatch):
    from agent_dispatch import bridge

    def unexpected_wake(*_args, **_kwargs):
        raise AssertionError("terminal resolution must not wake the owner")

    monkeypatch.setattr(bridge, "resume_steered_owner", unexpected_wake)
    task = client.create("wait for condition")
    owner = client.claim(worker_id="worker-1")["owner"]
    client.start(task["id"], owner)
    client.suspend(task["id"], owner, reason="condition pending")

    done = client.complete(
        task["id"], owner, result_ref="condition:satisfied"
    )

    assert done["status"] == Status.COMPLETED
    assert done["result_ref"] == "condition:satisfied"
    assert done["owner"] is None


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


def test_progress_over_http(api):
    import json

    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1"})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    r = api.post(
        f"/tasks/{tid}/progress",
        json={"worker_id": "w1", "phase": "impl", "summary": "wired it", "pr": "pr/3"},
    )
    assert r.status_code == 200
    snap = json.loads(r.json()["latest_progress"])
    assert snap["phase"] == "impl" and snap["summary"] == "wired it" and snap["pr"] == "pr/3"
    # wrong owner is rejected
    assert api.post(
        f"/tasks/{tid}/progress", json={"worker_id": "w2", "summary": "nope"}
    ).status_code == 409


def test_activity_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    reservation = api.post(
        "/spawn-reservations", json={"task_id": tid, "reserved_by": "sup"}
    ).json()["reservation"]
    key = reservation["key"]
    api.post(
        f"/spawn-reservations/{key}/spawned",
        json={"session_handle": "local-body:s1"},
    )
    active = api.post(
        f"/tasks/{tid}/activity",
        json={"activity": "ACTIVE", "reservation_key": key},
    )
    assert active.status_code == 200
    assert active.json()["activity"] == "ACTIVE"
    assert active.json()["activity_updated_at"] is not None
    invalid = api.post(
        f"/tasks/{tid}/activity",
        json={"activity": "IDLE", "reservation_key": key},
    )
    assert invalid.status_code == 409


def test_goal_and_progress_log_over_http(api):
    # A goal-bearing task carries goal + done_criteria on the row...
    r = api.post(
        "/tasks",
        json={
            "title": "pursue",
            "goal": "reach the goal",
            "done_criteria": "it is done",
        },
    )
    tid = r.json()["id"]
    assert r.json()["goal"] == "reach the goal"
    assert r.json()["done_criteria"] == "it is done"

    api.post("/claim", json={"worker_id": "w1"})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    api.post(
        f"/tasks/{tid}/progress",
        json={"worker_id": "w1", "phase": "plan", "summary": "first"},
    )
    api.post(
        f"/tasks/{tid}/progress",
        json={"worker_id": "w1", "phase": "impl", "summary": "second"},
    )

    # ...and the append-only progress log accumulates every beat in order.
    log = api.get(f"/tasks/{tid}/progress-log").json()
    assert [(r["phase"], r["summary"]) for r in log] == [
        ("plan", "first"),
        ("impl", "second"),
    ]


def test_progress_log_missing_task_is_404(api):
    assert api.get("/tasks/nope/progress-log").status_code == 404


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


def test_list_comma_separated_status(api):
    api.post("/tasks", json={"title": "q"})
    api.post("/tasks", json={"title": "draft", "proposed": True})
    got = api.get("/tasks", params={"status": "queued,proposed"}).json()
    assert {t["title"] for t in got} == {"q", "draft"}
    only_q = api.get("/tasks", params={"status": "queued"}).json()
    assert [t["title"] for t in only_q] == ["q"]


def test_sweep_endpoint_excludes_abandoned(api):
    api.post("/tasks", json={"title": "live"})
    gone = api.post("/tasks", json={"title": "dead"}).json()
    api.post("/tasks/" + gone["id"] + "/abandon", json={"permitted": True})
    swept = api.get("/tasks", params={"sweep": True}).json()
    titles = {t["title"] for t in swept}
    assert "live" in titles and "dead" not in titles


def test_repo_param_scopes_list_sweep_find(api):
    # POST carries an explicit repo lane; the RepoDefaultingQueue only defaults
    # when repo is omitted, so these land in distinct lanes.
    api.post("/tasks", json={"title": "widget work", "repo": "example.com/acme/widget"})
    api.post("/tasks", json={"title": "gadget work", "repo": "example.com/acme/gadget"})
    widget = api.get("/tasks", params={"repo": "example.com/acme/widget"}).json()
    assert [t["title"] for t in widget] == ["widget work"]
    swept = api.get(
        "/tasks", params={"sweep": True, "repo": "example.com/acme/gadget"}
    ).json()
    assert [t["title"] for t in swept] == ["gadget work"]
    found = api.get(
        "/tasks", params={"q": "work", "repo": "example.com/acme/widget"}
    ).json()
    assert [t["title"] for t in found] == ["widget work"]


def test_claim_is_repo_scoped(api):
    api.post("/tasks", json={"title": "widget task", "repo": "example.com/acme/widget"})
    api.post("/tasks", json={"title": "gadget task", "repo": "example.com/acme/gadget"})
    # a worker in the gadget lane only ever claims the gadget task
    claimed = api.post(
        "/claim", json={"worker_id": "w", "repo": "example.com/acme/gadget"}
    ).json()
    assert claimed["title"] == "gadget task"
    again = api.post(
        "/claim", json={"worker_id": "w2", "repo": "example.com/acme/gadget"}
    ).json()
    assert again is None  # nothing else in this lane; the widget task is invisible


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


def test_client_recover(client, monkeypatch):
    monkeypatch.setattr("agent_dispatch.tracking.liveness_verdict", lambda *a, **k: "gone")
    t = client.create("leased")
    client.claim("m/wt")  # owner is a machine/worktree so liveness resolves
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
    # Windows CI may be heavily loaded by sibling coordinator tests; the
    # 0.3s sweep remains the behavior under test, while this is only the
    # outer observation budget.
    deadline = time.time() + 15
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


def _boot(app):
    """Boot an app on an ephemeral port; return (url, stop). Mirrors server_url."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    probe = DispatchClient(url)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            probe.health()
            break
        except Exception:
            time.sleep(0.05)
    else:
        probe.close()
        raise RuntimeError("coordinator did not start")
    probe.close()

    def stop():
        server.should_exit = True
        thread.join(timeout=5)

    return url, stop


def test_background_gc_auto_recovers_gone_owner(tmp_path, monkeypatch):
    # 0.3s GC interval: a held task whose owner is confirmed gone returns to
    # queued on its own, with no manual recover.
    from agent_dispatch.coordinator import create_app

    monkeypatch.setattr("agent_dispatch.tracking.liveness_verdict", lambda *a, **k: "gone")
    q = TaskQueue(tmp_path / "tasks.db")
    url, stop = _boot(create_app(q, sweep_interval=0.3, enable_mcp=False))
    try:
        c = DispatchClient(url)
        tid = c.create("leased")["id"]
        assert c.claim(worker_id="m/wt")["id"] == tid
        assert c.get(tid)["status"] == Status.CLAIMED
        deadline = time.time() + 5
        while time.time() < deadline:
            if c.get(tid)["status"] == Status.QUEUED:
                break
            time.sleep(0.2)
        assert c.get(tid)["status"] == Status.QUEUED  # GC requeued it automatically
        assert c.get(tid)["owner"] is None
        c.close()
    finally:
        stop()


def test_gc_disabled_by_default(tmp_path, monkeypatch):
    # gc_interval=0 (default) -> a held task is NOT auto-recovered even if its
    # owner is gone; only a manual recover (or an enabled GC loop) requeues it.
    from agent_dispatch.coordinator import create_app

    monkeypatch.setattr("agent_dispatch.tracking.liveness_verdict", lambda *a, **k: "gone")
    q = TaskQueue(tmp_path / "tasks.db")
    url, stop = _boot(create_app(q))  # no sweep_interval -> no GC loop
    try:
        c = DispatchClient(url)
        tid = c.create("leased")["id"]
        c.claim(worker_id="m/wt")
        time.sleep(0.5)  # no GC loop running, so nothing requeues on its own
        assert c.get(tid)["status"] == Status.CLAIMED
        assert c.recover()["recovered"] == 1  # manual recover still works
        assert c.get(tid)["status"] == Status.QUEUED
        c.close()
    finally:
        stop()


def test_cli_consume_completes_and_prints_payload(server_url, client, monkeypatch, capsys):
    """``agent-dispatch consume`` drives a proposed handoff to completed and
    prints its payload -- then a second consume of the now-spent baton is
    REFUSED (exit 3, stop notice), never replaying the finished work."""
    import argparse

    from agent_dispatch import __main__
    from tests._helpers import TEST_REPO

    task = client.create(
        "handoff",
        proposed=True,
        labels=["handoff"],
        target_worktree="wt-1",
        payload_inline="BRIEF-BODY",
        repo=TEST_REPO,
    )
    tid = task["id"]
    assert client.get(tid)["status"] == Status.PROPOSED

    monkeypatch.setattr(__main__, "_client", lambda args: DispatchClient(server_url))
    monkeypatch.setattr(__main__, "_scope_repo", lambda args: TEST_REPO)
    args = argparse.Namespace(
        task_id=tid,
        worker_id=None,
        machine="m1",
        worktree="wt-1",
        repo=None,
        result_ref=None,
        url=None,
        token=None,
    )

    assert __main__._cmd_consume(args) == 0
    assert "BRIEF-BODY" in capsys.readouterr().out
    done = client.get(tid)
    assert done["status"] == Status.COMPLETED
    # owner is cleared on completion (the lease is released); the result_ref
    # proves the successor's identity owned it through the complete transition.
    assert done["result_ref"] == "consumed:wt-1"

    # Debounce: consuming the now-completed handoff again is refused (exit 3)
    # with a stop notice instead of a replayed brief.
    assert __main__._cmd_consume(args) == 3
    out = capsys.readouterr().out
    assert "already COMPLETED" in out
    assert "BRIEF-BODY" not in out
    assert client.get(tid)["status"] == Status.COMPLETED


# -- satellite presence registry ---------------------------------------------


def test_satellite_register_and_list(api):
    r = api.post(
        "/satellites/register",
        json={
            "machine": "field-laptop",
            "worktrees": ["wt-a"],
            "capabilities": ["logger"],
            "agent_versions": {"agent-dispatch": "0.1.0-dev104"},
        },
    )
    assert r.status_code == 200
    entry = r.json()
    assert entry["machine"] == "field-laptop"
    assert entry["expires_at"] > entry["last_seen"]

    listing = api.get("/satellites").json()
    assert [e["machine"] for e in listing] == ["field-laptop"]


def test_satellite_heartbeat_updates_status(api):
    api.post("/satellites/register", json={"machine": "book2"})
    r = api.post(
        "/satellites/book2/heartbeat",
        json={"status": {"wt-a": {"turn_state": "active"}}},
    )
    assert r.status_code == 200
    assert r.json()["status"] == {"wt-a": {"turn_state": "active"}}


def test_satellite_heartbeat_unknown_is_404(api):
    r = api.post("/satellites/ghost/heartbeat", json={})
    assert r.status_code == 404


def test_satellite_deregister(api):
    api.post("/satellites/register", json={"machine": "book2"})
    r = api.delete("/satellites/book2")
    assert r.status_code == 200
    assert r.json() == {"deregistered": True}
    assert api.get("/satellites").json() == []


def test_satellite_deregister_absent(api):
    r = api.delete("/satellites/never")
    assert r.json() == {"deregistered": False}


# -- fleet directory endpoints -----------------------------------------------


def test_directory_register_and_list(api):
    r = api.post(
        "/directory/register",
        json={"instance": "mantis-counter", "role": "coordinator", "epoch": 3},
    )
    assert r.status_code == 200
    entry = r.json()
    assert entry["instance"] == "mantis-counter"
    assert entry["role"] == "coordinator"
    assert entry["epoch"] == 3

    listing = api.get("/directory").json()
    assert [e["instance"] for e in listing] == ["mantis-counter"]


def test_directory_list_filters_by_role(api):
    api.post("/directory/register", json={"instance": "peer-1", "role": "peer"})
    api.post("/directory/register", json={"instance": "sat-1", "role": "satellite"})
    sats = api.get("/directory", params={"role": "satellite"}).json()
    assert [e["instance"] for e in sats] == ["sat-1"]


def test_directory_coordinator_endpoint(api):
    assert api.get("/directory/coordinator").json() is None
    api.post(
        "/directory/register",
        json={"instance": "c-old", "role": "coordinator", "epoch": 1},
    )
    api.post(
        "/directory/register",
        json={"instance": "c-new", "role": "coordinator", "epoch": 9},
    )
    coord = api.get("/directory/coordinator").json()
    assert coord["instance"] == "c-new"
    assert coord["epoch"] == 9


def test_directory_heartbeat_unknown_is_404(api):
    r = api.post("/directory/ghost/heartbeat", json={})
    assert r.status_code == 404


def test_directory_deregister(api):
    api.post("/directory/register", json={"instance": "peer-1"})
    assert api.delete("/directory/peer-1").json() == {"deregistered": True}
    assert api.get("/directory").json() == []


def test_satellite_facade_tags_role_and_shows_in_directory(api):
    # A satellite registered via the /satellites facade appears in the unified
    # directory with role=satellite.
    api.post("/satellites/register", json={"machine": "book2"})
    sats = api.get("/satellites").json()
    assert [e["instance"] for e in sats] == ["book2"]
    assert sats[0]["role"] == "satellite"
    directory = api.get("/directory", params={"role": "satellite"}).json()
    assert [e["instance"] for e in directory] == ["book2"]
