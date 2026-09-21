"""Tests for the agent-dispatch coordinator HTTP API and client."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import (
    DispatchClient,
    DispatchError,
    DispatchUpgradeRequired,
)
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def app(tmp_path):
    return create_app(TaskQueue(tmp_path / "tasks.db"))


@pytest.fixture
def api(app):
    return TestClient(app)


def test_resource_reservation_api_elects_binds_and_owner_releases(api):
    key = "forge:github:repository:example/project:issue:7"
    acquired = api.post(
        "/resource-reservations/acquire",
        json={"key": key, "owner": "loop:alpha", "ttl": 60},
    )
    assert acquired.status_code == 200
    assert acquired.json()["granted"]
    token = acquired.json()["reservation"]["token"]

    stale_same_owner = api.post(
        "/resource-reservations/acquire",
        json={"key": key, "owner": "loop:alpha", "ttl": 60},
    )
    assert not stale_same_owner.json()["granted"]
    assert "token" not in stale_same_owner.json()["reservation"]

    renewed = api.post(
        "/resource-reservations/acquire",
        json={
            "key": key,
            "owner": "loop:alpha",
            "token": token,
            "ttl": 60,
        },
    )
    assert renewed.json()["granted"]
    assert renewed.json()["reservation"]["token"] == token

    denied = api.post(
        "/resource-reservations/acquire",
        json={"key": key, "owner": "loop:beta", "ttl": 60},
    )
    assert denied.status_code == 200
    assert not denied.json()["granted"]
    assert denied.json()["reservation"]["owner"] == "loop:alpha"
    assert "token" not in denied.json()["reservation"]

    bound = api.post(
        "/resource-reservations/bind",
        json={
            "key": key,
            "owner": "loop:alpha",
            "token": token,
            "task_id": "task-7",
        },
    )
    assert bound.status_code == 200
    assert bound.json()["task_id"] == "task-7"

    refused = api.post(
        "/resource-reservations/release",
        json={"key": key, "owner": "loop:beta", "token": token},
    )
    assert refused.status_code == 200
    assert not refused.json()["released"]

    listed = api.get(
        "/resource-reservations", params={"owner_prefix": "loop:"}
    )
    assert listed.status_code == 200
    assert [item["key"] for item in listed.json()] == [key]

    released = api.post(
        "/resource-reservations/release",
        json={"key": key, "owner": "loop:alpha", "token": token},
    )
    assert released.status_code == 200
    assert released.json()["released"]


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
    owner = client.claim(worker_id="worker-1", repo=TEST_REPO)["owner"]
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
    owner = client.claim(worker_id="worker-1", repo=TEST_REPO)["owner"]
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
    owner = client.claim(worker_id="worker-1", repo=TEST_REPO)["owner"]
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


def test_health_loops_empty_when_sweep_disabled(api):
    # The `api` fixture's create_app has no sweep_interval -> no GC/reap loops,
    # but the field must still be present (an empty dict, never a KeyError).
    assert api.get("/health").json()["loops"] == {}


def test_health_includes_slot_descriptor_shape(api):
    # process-slot-ownership Phase 5: /health renders a "slot" descriptor
    # (process -> slot -> owner -> alive?) regardless of what state the
    # self-retire / abandoned-passive-reap loops happen to be in.
    slot = api.get("/health").json()["slot"]
    assert set(slot) == {
        "pid", "role", "active", "previous",
        "self_retire", "abandoned_passive_reap",
    }
    assert isinstance(slot["pid"], int)
    assert slot["role"] in ("active", "passive", "unknown")
    assert set(slot["self_retire"]) == {
        "enabled", "armed", "generation", "superseded", "confirms",
    }
    assert set(slot["abandoned_passive_reap"]) == {
        "enabled", "armed", "last_outcome",
    }


def test_slot_descriptor_unknown_role_without_routing_table(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agent_dispatch.coordinator import _slot_descriptor

    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "no-routing"))
    slot = _slot_descriptor(SimpleNamespace())
    assert slot["role"] == "unknown"
    assert slot["active"] is None
    assert slot["previous"] is None
    # No status ever published on app.state -> the descriptor still returns
    # the documented shape with conservative defaults, never a KeyError.
    assert slot["self_retire"] == {
        "enabled": False, "armed": False, "generation": None,
        "superseded": False, "confirms": 0,
    }
    assert slot["abandoned_passive_reap"] == {
        "enabled": False, "armed": False, "last_outcome": None,
    }


def test_slot_descriptor_reports_active_role_for_own_pid(tmp_path, monkeypatch):
    import os
    from types import SimpleNamespace

    from zdd import routing

    from agent_dispatch.coordinator import _slot_descriptor

    routing_dir = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing_dir))
    routing.publish_active(
        routing_dir, bind="127.0.0.1", port=9281, pid=os.getpid(), version="1.0",
    )
    slot = _slot_descriptor(SimpleNamespace())
    assert slot["role"] == "active"
    assert slot["active"]["pid"] == os.getpid()


def test_slot_descriptor_reports_unknown_role_when_active_pid_is_null(
    tmp_path, monkeypatch,
):
    """A malformed/legacy routing entry with no recorded pid must never be
    mistaken for "passive" -- there is nothing to compare against, so the
    owner question is genuinely unanswerable (Copilot review finding)."""
    import json
    from types import SimpleNamespace

    from agent_dispatch.coordinator import _slot_descriptor

    routing_dir = tmp_path / "routing"
    routing_dir.mkdir(parents=True)
    (routing_dir / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 9281, "pid": None}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing_dir))
    slot = _slot_descriptor(SimpleNamespace())
    assert slot["role"] == "unknown"
    assert slot["active"]["pid"] is None


def test_slot_descriptor_reports_unknown_role_when_active_pid_is_boolean(
    tmp_path, monkeypatch,
):
    """``bool`` is an ``int`` subclass in Python; a malformed entry with
    ``pid: true`` must not be mistaken for a real, comparable pid (Copilot
    review finding)."""
    import json
    from types import SimpleNamespace

    from agent_dispatch.coordinator import _slot_descriptor

    routing_dir = tmp_path / "routing"
    routing_dir.mkdir(parents=True)
    (routing_dir / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 9281, "pid": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing_dir))
    slot = _slot_descriptor(SimpleNamespace())
    assert slot["role"] == "unknown"


def test_slot_descriptor_reports_passive_role_for_other_active_pid(
    tmp_path, monkeypatch,
):
    from types import SimpleNamespace

    from zdd import routing

    from agent_dispatch.coordinator import _slot_descriptor

    routing_dir = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing_dir))
    routing.publish_active(
        routing_dir, bind="127.0.0.1", port=9281, pid=999999, version="1.0",
    )
    slot = _slot_descriptor(SimpleNamespace())
    assert slot["role"] == "passive"
    assert slot["active"]["pid"] == 999999


def test_self_retire_status_lifecycle_reflects_the_live_loop(tmp_path, monkeypatch):
    """Copilot review finding on PR #2963: prove the published status actually
    tracks the running self-retire loop end-to-end (armed -> generation ->
    superseded/confirms), not just the pure `_slot_descriptor` rendering of
    prebuilt state. Uses a real app lifespan with a shortened poll interval and
    a temporary routing table; `is_superseded` is monkeypatched (rather than
    faking a real listening successor) to keep this fast and deterministic."""
    import os
    import time

    from zdd import routing

    routing_dir = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing_dir))
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "1")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE_POLL_S", "0.05")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS", "20")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "0")

    my_pid = os.getpid()
    routing.publish_active(
        routing_dir, bind="127.0.0.1", port=9999, pid=my_pid, version="1.0",
    )

    # Patched BEFORE the app/lifespan starts: the loop's `from .self_retire
    # import is_superseded` binds this lambda once, for the coroutine's whole
    # lifetime, so flipping the mutable flag later (step 3) is what changes
    # its answer -- re-patching the module attribute after the loop has
    # already started would not reach the already-bound local name.
    import agent_dispatch.self_retire as self_retire_mod

    supersede_flag = {"value": False}
    monkeypatch.setattr(
        self_retire_mod, "is_superseded", lambda *a, **k: supersede_flag["value"],
    )

    app = create_app(TaskQueue(tmp_path / "t.db"))
    with TestClient(app) as client:

        def _wait(predicate, *, timeout=5.0):
            deadline = time.monotonic() + timeout
            last = None
            while time.monotonic() < deadline:
                last = client.get("/health").json()["slot"]["self_retire"]
                if predicate(last):
                    return last
                time.sleep(0.02)
            pytest.fail(f"condition not met within {timeout}s; last status={last}")

        # 1. Arms once it observes itself as the routing table's active pid,
        #    capturing its own generation.
        armed = _wait(lambda s: s["armed"])
        assert armed["generation"] is not None

        # 2. Not superseded while nothing supersedes it: the loop keeps polling
        #    and keeps writing `superseded: False` / `confirms: 0` -- proving
        #    the status is live-updated on every cycle, not written once and
        #    forgotten.
        for _ in range(3):
            status = client.get("/health").json()["slot"]["self_retire"]
            assert status["superseded"] is False
            assert status["confirms"] == 0
            time.sleep(0.05)

        # 3. Once superseded (simulated by flipping the patched predicate
        #    rather than standing up a real listening successor process),
        #    confirms climb toward the confirmation threshold and
        #    `superseded` flips true.
        supersede_flag["value"] = True
        status = _wait(lambda s: s["superseded"] and s["confirms"] >= 1)
        assert status["superseded"] is True
        assert status["confirms"] >= 1


def test_abandoned_passive_reap_status_lifecycle_reflects_the_live_loop(
    tmp_path, monkeypatch,
):
    """Copilot review finding on PR #2963: the abandoned-passive-reap loop's
    published status needs its own deterministic lifecycle proof, mirroring
    the self-retire coverage above -- it arms, then `last_outcome` reflects a
    real reap cycle's decision (here: a breadcrumb naming a pid that plainly
    is not a live coordinator, so the cycle's own no-op reasoning is what gets
    proven live-published, not fabricated)."""
    import json
    import os
    import time
    from datetime import datetime, timedelta, timezone

    from zdd import routing

    routing_dir = tmp_path / "routing"
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(routing_dir))
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "1")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP_POLL_S", "0.05")

    my_pid = os.getpid()
    routing.publish_active(
        routing_dir, bind="127.0.0.1", port=9999, pid=my_pid, version="1.0",
    )
    # An aged, non-terminal breadcrumb naming a pid that is not a live
    # coordinator process -- a deterministic, real (not monkeypatched) no-op
    # decision for reap_abandoned_passive_backstop to reach.
    routing_dir.mkdir(parents=True, exist_ok=True)
    aged = (datetime.now(timezone.utc) - timedelta(seconds=99999)).isoformat()
    (routing_dir / "cutover.json").write_text(
        json.dumps({
            "state": "started",
            "started_at": aged,
            "updated_at": aged,
            "pid": my_pid,
            "old": None,
            "new_port": 9281,
            "new_pid": 999999,
            "error": None,
        }),
        encoding="utf-8",
    )

    app = create_app(TaskQueue(tmp_path / "t.db"))
    with TestClient(app) as client:

        def _wait(predicate, *, timeout=5.0):
            deadline = time.monotonic() + timeout
            last = None
            while time.monotonic() < deadline:
                last = client.get("/health").json()["slot"]["abandoned_passive_reap"]
                if predicate(last):
                    return last
                time.sleep(0.02)
            pytest.fail(f"condition not met within {timeout}s; last status={last}")

        armed = _wait(lambda s: s["armed"])
        assert armed["last_outcome"] is None or isinstance(armed["last_outcome"], dict)

        outcome = _wait(lambda s: s["last_outcome"] is not None)["last_outcome"]
        assert outcome["reaped"] is False
        assert outcome["pid"] == 999999


def test_create_and_get(api):
    r = api.post(
        "/tasks",
        json={
            "title": "work",
            "prompt": "go",
            "exclusive_key": "resource:42",
        },
    )
    assert r.status_code == 200
    task = r.json()
    assert task["status"] == Status.QUEUED
    assert task["exclusive_key"] == "resource:42"
    got = api.get(f"/tasks/{task['id']}").json()
    assert got["title"] == "work"
    assert got["exclusive_key"] == "resource:42"


def test_get_missing_is_404(api):
    assert api.get("/tasks/nope").status_code == 404


def test_full_lifecycle_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    claimed = api.post(
        "/claim", json={"worker_id": "w1", "repo": TEST_REPO}
    ).json()
    assert claimed["id"] == tid and claimed["status"] == Status.CLAIMED
    started = api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"}).json()
    assert started["status"] == Status.STARTED
    done = api.post(
        f"/tasks/{tid}/complete", json={"worker_id": "w1", "result_ref": "pr/1"}
    ).json()
    assert done["status"] == Status.COMPLETED


def test_structured_result_is_full_on_show_bounded_in_bulk_and_retrievable(api):
    tid = api.post(
        "/tasks", json={"title": "x", "target_worktree": "wt-1"}
    ).json()["id"]
    owner = "m1/wt-1"
    api.post(
        "/claim",
        json={
            "worker_id": owner,
            "repo": TEST_REPO,
            "machine": "m1",
            "worktree": "wt-1",
        },
    )
    api.post(f"/tasks/{tid}/start", json={"worker_id": owner})
    result = {"summary": {"passed": 3}, "data": "x" * 60000}

    completed = api.post(
        f"/tasks/{tid}/complete",
        json={"worker_id": owner, "result_ref": "artifact/3", "result": result},
    )

    assert completed.status_code == 200
    assert completed.json()["result"] == result
    assert api.get(f"/tasks/{tid}").json()["result"] == result
    assert api.get(f"/tasks/{tid}/result").json() == {
        "task_id": tid,
        "ref": "artifact/3",
        "result": result,
    }
    for path in (
        "/tasks",
        "/tasks?q=x",
        "/tasks?sweep=true",
    ):
        response = api.get(path)
        assert response.status_code == 200
        assert len(response.content) < 20000
        assert ("x" * 1000).encode() not in response.content
        rows = response.json()
        if isinstance(rows, dict):
            rows = [task for group in rows.values() for task in group]
        row = next(task for task in rows if task["id"] == tid)
        assert row["has_result"] is True
        assert "result" not in row

    assigned = api.post(
        "/tasks", json={"title": "assigned", "target_worktree": "wt-1"}
    ).json()
    mine = api.get("/tasks/mine?machine=m1&worktree=wt-1").json()
    mine_row = next(
        task for group in mine.values() for task in group if task["id"] == assigned["id"]
    )
    assert mine_row["has_result"] is False
    assert "result" not in mine_row


def test_invalid_http_result_leaves_task_started(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})

    response = api.post(
        f"/tasks/{tid}/complete",
        content='{"worker_id":"w1","result_ref":"artifact/bad","result":{"value":NaN}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    task = api.get(f"/tasks/{tid}").json()
    assert task["status"] == Status.STARTED
    assert task["result_ref"] is None
    assert task["result"] is None


def test_oversized_http_result_leaves_task_started(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})

    response = api.post(
        f"/tasks/{tid}/complete",
        json={
            "worker_id": "w1",
            "result_ref": "artifact/large",
            "result": {"data": "x" * (64 * 1024)},
        },
    )

    assert response.status_code == 413
    task = api.get(f"/tasks/{tid}").json()
    assert task["status"] == Status.STARTED
    assert task["result_ref"] is None
    assert task["result"] is None


@pytest.mark.parametrize(
    "content",
    [
        '{"worker_id":"w1","result":',
        '{"worker_id":"w1","result":null}',
        '{"worker_id":"w1","result":"{\\"ok\\":true}"}',
    ],
)
def test_invalid_result_json_shapes_are_400_and_non_terminal(api, content):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})

    response = api.post(
        f"/tasks/{tid}/complete",
        content=content,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert api.get(f"/tasks/{tid}").json()["status"] == Status.STARTED


def test_result_validation_response_omits_rejected_input(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    sensitive = "do-not-echo-this-value"

    response = api.post(
        f"/tasks/{tid}/complete",
        json={"worker_id": "w1", "result": sensitive},
    )

    assert response.status_code == 400
    assert sensitive not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_non_result_validation_on_complete_remains_422(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]

    response = api.post(
        f"/tasks/{tid}/complete",
        json={"worker_id": {"not": "a string"}},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "worker_id"]


def test_result_size_validation_response_is_413_and_sanitized(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    sensitive = "sensitive-prefix-" + ("x" * (64 * 1024))

    response = api.post(
        f"/tasks/{tid}/complete",
        json={"worker_id": "w1", "result": {"data": sensitive}},
    )

    assert response.status_code == 413
    assert sensitive not in response.text
    assert all("input" not in error for error in response.json()["detail"])
    assert api.get(f"/tasks/{tid}").json()["status"] == Status.STARTED


def test_client_omits_none_result_for_older_coordinator():
    seen = {}

    def handler(request):
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"id": "t1", "status": Status.COMPLETED}
        )

    with DispatchClient(
        "http://coordinator", transport=httpx.MockTransport(handler)
    ) as client:
        client.complete("t1", "w1")

    assert "result" not in seen


def test_client_detects_coordinator_that_drops_structured_result():
    def handler(request):
        return httpx.Response(
            200, json={"id": "t1", "status": Status.COMPLETED}
        )

    with DispatchClient(
        "http://coordinator", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(DispatchUpgradeRequired, match="upgrade the coordinator"):
            client.complete("t1", "w1", result={"ok": True})


def test_progress_over_http(api):
    import json

    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
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
    assert invalid.status_code == 200
    assert invalid.json()["activity"] == "IDLE"


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

    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
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


def test_attachment_history_over_http(api):
    """durable-attachment-history over the wire: owner-session route ("
    "/tasks/{id}/owner-session) records durably queryable via GET
    /tasks/{id}/attachments, surviving a release that clears the current
    owner-session field."""
    r = api.post("/tasks", json={"title": "reviewed"})
    tid = r.json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    api.post(
        f"/tasks/{tid}/owner-session",
        json={"worker_id": "w1", "owner_session_id": "session-a"},
    )
    history = api.get(f"/tasks/{tid}/attachments").json()
    assert len(history) == 1
    assert history[0]["session_id"] == "session-a"
    assert history[0]["detached_at"] is None

    api.post(f"/tasks/{tid}/suspend", json={"worker_id": "w1", "reason": "stuck"})
    api.post(f"/tasks/{tid}/release", json={"worker_id": "w1", "reason": "reset"})
    history = api.get(f"/tasks/{tid}/attachments").json()
    assert len(history) == 1
    assert history[0]["session_id"] == "session-a"
    assert history[0]["detached_at"] is not None
    task = api.get(f"/tasks/{tid}").json()
    assert task["owner_session_id"] is None  # current owner cleared...
    # ...but the prior session's record is NOT discarded (the whole point).


def test_attachment_history_missing_task_is_404(api):
    assert api.get("/tasks/nope/attachments").status_code == 404


def test_tasks_for_session_reverse_lookup_over_http(api):
    """The reverse of test_attachment_history_over_http: given a session id,
    GET /sessions/{id}/tasks finds the task(s) it attached to."""
    r = api.post("/tasks", json={"title": "reviewed"})
    tid = r.json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    api.post(f"/tasks/{tid}/start", json={"worker_id": "w1"})
    api.post(
        f"/tasks/{tid}/owner-session",
        json={"worker_id": "w1", "owner_session_id": "session-a"},
    )
    found = api.get("/sessions/session-a/tasks").json()
    assert len(found) == 1
    assert found[0]["task_id"] == tid
    assert found[0]["detached_at"] is None


def test_tasks_for_session_unknown_session_is_empty_not_404(api):
    """A session id with no attachment anywhere is an empty list -- there is
    no single task to 404 against, unlike /tasks/{id}/attachments."""
    r = api.get("/sessions/never-seen-session/tasks")
    assert r.status_code == 200
    assert r.json() == []


def test_claim_empty_returns_null(api):
    assert api.post(
        "/claim", json={"worker_id": "w1", "repo": TEST_REPO}
    ).json() is None


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


def test_hold_and_unhold_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    held = api.post(f"/tasks/{tid}/hold", json={"reason": "pause", "actor": "op"})
    assert held.status_code == 200
    assert held.json()["hold_reason"] == "pause"
    unheld = api.post(f"/tasks/{tid}/unhold", json={"actor": "op"})
    assert unheld.status_code == 200
    assert unheld.json()["hold_reason"] is None


def test_hold_rejects_stale_expected_status_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    stale = api.post(
        f"/tasks/{tid}/hold",
        json={"reason": "pause", "actor": "op", "expected_status": "queued"},
    )
    assert stale.status_code == 409
    ok = api.post(
        f"/tasks/{tid}/hold",
        json={"reason": "pause", "actor": "op", "expected_status": "claimed"},
    )
    assert ok.status_code == 200


def test_reset_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/claim", json={"worker_id": "w1", "repo": TEST_REPO})
    reset = api.post(f"/tasks/{tid}/reset", json={"reason": "not like this"})
    assert reset.status_code == 200
    body = reset.json()
    assert body["status"] == Status.PROPOSED
    assert body["owner"] is None


def test_reset_refuses_terminal_and_held_over_http(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    api.post("/tasks/" + tid + "/abandon", json={"permitted": True})
    assert api.post(f"/tasks/{tid}/reset", json={}).status_code == 409

    tid2 = api.post("/tasks", json={"title": "y"}).json()["id"]
    api.post(f"/tasks/{tid2}/hold", json={"reason": "pause", "actor": "op"})
    assert api.post(f"/tasks/{tid2}/reset", json={}).status_code == 409


def test_proposed_not_claimable_then_approved(api):
    tid = api.post("/tasks", json={"title": "draft", "proposed": True}).json()["id"]
    assert api.post(
        "/claim", json={"worker_id": "w1", "repo": TEST_REPO}
    ).json() is None
    api.post(f"/tasks/{tid}/approve")
    assert api.post(
        "/claim", json={"worker_id": "w1", "repo": TEST_REPO}
    ).json()["id"] == tid


def test_capability_gate_over_http(api):
    api.post("/tasks", json={"title": "log", "requires": ["logger"]})
    assert api.post(
        "/claim", json={"worker_id": "w1", "repo": TEST_REPO}
    ).json() is None
    got = api.post(
        "/claim",
        json={
            "worker_id": "w1",
            "repo": TEST_REPO,
            "capabilities": ["logger"],
        },
    ).json()
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


def test_claim_requires_repo_or_explicit_all_repos(api):
    task = api.post("/tasks", json={"title": "scoped"}).json()

    missing = api.post("/claim", json={"worker_id": "w"})
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "claim_scope_required"

    conflicting = api.post(
        "/claim",
        json={"worker_id": "w", "repo": TEST_REPO, "all_repos": True},
    )
    assert conflicting.status_code == 422
    assert conflicting.json()["detail"]["code"] == "claim_scope_invalid"

    claimed = api.post(
        "/claim",
        json={"worker_id": "admin", "all_repos": True},
    )
    assert claimed.status_code == 200
    assert claimed.json()["id"] == task["id"]


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
    assert client.claim("w1", repo=TEST_REPO) is None  # lacks capability
    claimed = client.claim("w1", ["review"], repo=TEST_REPO)
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
    client.claim("m/wt", repo=TEST_REPO)  # owner is a machine/worktree so liveness resolves
    assert client.recover()["recovered"] == 1
    assert client.get(t["id"])["status"] == Status.QUEUED


# -- SSE event stream --------------------------------------------------------


def test_sse_stream_distinguishes_retry_recorded_result(server_url):
    streamer = DispatchClient(server_url)
    mutator = DispatchClient(server_url)
    received: list[dict] = []

    def collect():
        try:
            for ev in streamer.stream_events():
                received.append(ev)
                if ev.get("type") == "task.result_recorded":
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
    mutator.claim("w1", repo=TEST_REPO)
    mutator.start(tid, "w1")
    mutator.complete(tid, "w1")
    mutator.complete(tid, "w1", result={"summary": "done"})

    t.join(timeout=5)
    streamer.close()
    mutator.close()

    types = [e["type"] for e in received]
    assert "task.created" in types
    assert "task.claimed" in types
    assert "task.completed" in types
    assert "task.result_recorded" in types
    assert types.count("task.completed") == 1
    created = next(e for e in received if e["type"] == "task.created")
    assert created["task"]["id"] == tid
    completed = next(e for e in received if e["type"] == "task.completed")
    assert completed["task"]["has_result"] is False
    assert "result" not in completed["task"]
    recorded = next(
        e for e in received if e["type"] == "task.result_recorded"
    )
    assert recorded["task"]["has_result"] is True
    assert "result" not in recorded["task"]


def test_health_reports_zero_subscribers_initially(api):
    assert api.get("/health").json()["subscribers"] == 0


def test_claim_by_id_over_http(api):
    api.post("/tasks", json={"title": "a"})
    tid_b = api.post("/tasks", json={"title": "b"}).json()["id"]
    got = api.post(
        "/claim",
        json={"worker_id": "w1", "repo": TEST_REPO, "task_id": tid_b},
    ).json()
    assert got["id"] == tid_b
    # a different specific-id claim for an already-claimed task returns null
    assert api.post(
        "/claim",
        json={"worker_id": "w2", "repo": TEST_REPO, "task_id": tid_b},
    ).json() is None


def test_mine_over_http(api):
    api.post("/tasks", json={"title": "for-me", "target_worktree": "wt-1"})
    tid = api.post("/tasks", json={"title": "to-own"}).json()["id"]
    api.post(
        "/claim",
        json={
            "machine": "m1",
            "worktree": "wt-1",
            "repo": TEST_REPO,
            "task_id": tid,
        },
    )
    r = api.get("/tasks/mine", params={"machine": "m1", "worktree": "wt-1"}).json()
    assert any(t["title"] == "for-me" for t in r["assigned"])
    assert any(t["id"] == tid and t["owner"] == "m1/wt-1" for t in r["owned"])


def test_claim_composes_owner_from_machine_worktree(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    got = api.post(
        "/claim",
        json={
            "machine": "m1",
            "worktree": "wt-1",
            "repo": TEST_REPO,
            "task_id": tid,
        },
    ).json()
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
        assert c.claim(worker_id="m/wt", repo=TEST_REPO)["id"] == tid
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
        c.claim(worker_id="m/wt", repo=TEST_REPO)
        time.sleep(0.5)  # no GC loop running, so nothing requeues on its own
        assert c.get(tid)["status"] == Status.CLAIMED
        assert c.recover()["recovered"] == 1  # manual recover still works
        assert c.get(tid)["status"] == Status.QUEUED
        c.close()
    finally:
        stop()


def test_health_loops_report_liveness_gc_and_orphan_reap(tmp_path, monkeypatch):
    """The two coordinator polling loops record live status at ``GET /health``
    once sweep_interval is enabled -- the diagnosis surface requested for
    'where a stuck poller got stuck' rather than inferring it from an
    accumulating process census."""
    from agent_dispatch.coordinator import create_app

    monkeypatch.setattr("agent_dispatch.tracking.liveness_verdict", lambda *a, **k: "unknown")
    monkeypatch.setattr(
        "agent_dispatch.identity.resolve_machine", lambda: None
    )  # degrade-safe no-op for the orphan reaper
    q = TaskQueue(tmp_path / "tasks.db")
    url, stop = _boot(create_app(q, sweep_interval=0.2, enable_mcp=False))
    try:
        c = DispatchClient(url)
        deadline = time.time() + 5
        loops = {}
        while time.time() < deadline:
            loops = httpx.get(f"{url}/health", timeout=5).json()["loops"]
            if (
                loops.get("liveness_gc", {}).get("total_runs", 0) >= 1
                and loops.get("orphan_reap", {}).get("total_runs", 0) >= 1
            ):
                break
            time.sleep(0.1)
        assert loops["liveness_gc"]["total_runs"] >= 1
        assert loops["liveness_gc"]["in_progress"] is False
        assert loops["liveness_gc"]["last_error"] is None
        assert loops["liveness_gc"]["consecutive_failures"] == 0
        assert loops["orphan_reap"]["total_runs"] >= 1
        assert loops["orphan_reap"]["in_progress"] is False
        # The handoff-fallback loop is disabled by default (see
        # AGENT_DISPATCH_HANDOFF_FALLBACK) -- its health entry is still
        # present (never a KeyError) but never records a run.
        assert loops["handoff_fallback"]["total_runs"] == 0
        c.close()
    finally:
        stop()


def test_health_loops_report_handoff_fallback_when_enabled(tmp_path, monkeypatch):
    """Opting in (``handoff_fallback_enabled=True``) arms the coordinator's
    reconciliation loop alongside liveness GC / orphan reap, on the same
    ``sweep_interval`` cadence, and it is independently visible at ``GET
    /health`` -- the same 'where a stuck poller got stuck' diagnosis surface
    the other two loops already have."""
    from agent_dispatch.coordinator import create_app

    monkeypatch.setattr("agent_dispatch.tracking.liveness_verdict", lambda *a, **k: "unknown")
    monkeypatch.setattr("agent_dispatch.identity.resolve_machine", lambda: None)
    q = TaskQueue(tmp_path / "tasks.db")
    url, stop = _boot(
        create_app(
            q, sweep_interval=0.2, enable_mcp=False,
            handoff_fallback_enabled=True, handoff_fallback_grace=0.0,
        )
    )
    try:
        c = DispatchClient(url)
        deadline = time.time() + 5
        loops = {}
        while time.time() < deadline:
            loops = httpx.get(f"{url}/health", timeout=5).json()["loops"]
            if loops.get("handoff_fallback", {}).get("total_runs", 0) >= 1:
                break
            time.sleep(0.1)
        assert loops["handoff_fallback"]["total_runs"] >= 1
        assert loops["handoff_fallback"]["in_progress"] is False
        assert loops["handoff_fallback"]["last_error"] is None
        assert loops["handoff_fallback"]["consecutive_failures"] == 0
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
