"""Coordination claims retain ownership without acquiring execution resources."""

import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError, TaskQueue, ProducerFenceError

REPO = "github.com/example/harness"


def test_coordination_survives_manual_lifecycle_and_owner_loss(tmp_path):
    path = tmp_path / "queue.db"
    q = TaskQueue(path)
    task = q.create(
        "owned work", repo=REPO, execution_mode="coordination", claim_as="host/worktree",
        labels=["general"], category="work", dedup_key="lane", now=1,
    )
    assert task.owner == "host/worktree"
    q.start(task.id, task.owner)
    q.yield_task(task.id, task.owner)
    q.claim_one("host/worktree", repo=REPO, task_id=task.id)
    q.start(task.id, "host/worktree")
    q.suspend(task.id, "host/worktree", reason="waiting")
    with pytest.raises(TaskError, match="coordination"):
        q.reserve_spawn(task.id, allow_suspended_reembodiment=True)
    q.release_suspended(task.id, "host/worktree")
    q.claim_one("host/worktree", repo=REPO, task_id=task.id, now=10)
    counts = q.reconcile_liveness(lambda *_: "gone", now=2000)
    assert counts["requeued"] == 1
    reopened = TaskQueue(path)
    assert reopened.get(task.id).execution_mode == "coordination"
    assert reopened.get(task.id).status == "queued"
    with pytest.raises(TaskError, match="coordination"):
        reopened.reserve_spawn(task.id)
    assert reopened.list_reservations(task_id=task.id) == []
    assert reopened.claim_one("host/other", repo=REPO, task_id=task.id).id == task.id


def test_legacy_rows_and_explicit_delegation_still_reserve(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, repo TEXT, "
            "created_at REAL, updated_at REAL, status TEXT)"
        )
        conn.execute("INSERT INTO tasks VALUES ('old','keep',?,1,2,'queued')", (REPO,))
    q = TaskQueue(path)
    assert q.get("old").execution_mode == "legacy"
    assert q.get("old").created_at == 1
    assert q.reserve_spawn("old")[1] is True
    for mode in ("legacy", "delegated"):
        task = q.create(mode, repo=REPO, execution_mode=mode, labels=["general"])
        assert q.reserve_spawn(task.id)[1] is True


def test_rollback_writer_cannot_change_intent_or_reserve(tmp_path):
    q = TaskQueue(tmp_path / "queue.db")
    task = q.create("claim", repo=REPO, execution_mode="coordination")
    control = q.create("execution", repo=REPO)
    reservation, _ = q.reserve_spawn(control.id)
    with q._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="intent is immutable"):
            conn.execute("UPDATE tasks SET execution_mode='legacy' WHERE id=?", (task.id,))
        with pytest.raises(sqlite3.IntegrityError, match="cannot allocate"):
            conn.execute(
                "INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at)"
                " VALUES ('old-writer',?,1,'reserving',1)", (task.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot allocate"):
            conn.execute(
                "UPDATE spawn_reservations SET task_id=? WHERE key=?",
                (task.id, reservation.key),
            )
        conn.execute("UPDATE tasks SET labels='[\"general\"]',category='maintenance' WHERE id=?", (task.id,))
    with pytest.raises(TaskError, match="coordination"):
        q.reserve_spawn(task.id)
    assert q.list_reservations(task_id=task.id) == []


def test_dedup_and_producer_replay_do_not_change_execution_intent(tmp_path):
    q = TaskQueue(tmp_path / "queue.db")
    task = q.create("claim", repo=REPO, execution_mode="coordination", dedup_key="lane")
    assert q.create("old caller", repo=REPO, dedup_key="lane").id == task.id
    with pytest.raises(TaskError, match="different execution intent"):
        q.create("execution", repo=REPO, execution_mode="delegated", dedup_key="lane")
    fence = q.handoff_producer_scope(REPO, "producer", producer_id="p", expected_generation=0)
    fields = dict(
        repo=REPO, source="producer", producer_scope={"repo": REPO, "source": "producer"},
        producer_id="p", producer_generation=1, producer_capability=fence.producer_capability,
        producer_request_id="request", execution_mode="coordination",
    )
    task = q.create("claim", **fields)
    assert q.create("claim", **fields).id == task.id
    with pytest.raises(ProducerFenceError):
        q.create("claim", **{**fields, "execution_mode": "delegated"})


def test_client_refuses_unsupported_mode_before_create_but_keeps_legacy_wire():
    requests = []

    def exchange(request):
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"id": "legacy"})

    with DispatchClient("http://testserver", transport=httpx.MockTransport(exchange)) as client:
        for mode in ("coordination", "delegated"):
            with pytest.raises(DispatchUpgradeRequired, match="execution intent"):
                client.create("claim", repo=REPO, execution_mode=mode)
        assert all(request.method == "GET" for request in requests)
        client.create("old caller", repo=REPO)
        assert "execution_mode" not in json.loads(requests[-1].content)


def test_actual_cli_client_http_roundtrip_and_spawn_rejection(tmp_path, monkeypatch, capsys):
    from agent_dispatch import __main__ as cli

    q = TaskQueue(tmp_path / "queue.db")
    with TestClient(create_app(q)) as http:
        assert http.get("/health").json()["task_execution_intent"] == 1

        def exchange(request):
            response = http.request(
                request.method, str(request.url), content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, json=response.json())

        monkeypatch.setattr(cli, "_client", lambda _: DispatchClient(
            "http://testserver", transport=httpx.MockTransport(exchange),
        ))
        monkeypatch.setattr(cli, "_enrich", lambda value: value)
        monkeypatch.setattr(cli, "_scope_repo", lambda _: REPO)
        monkeypatch.setattr(cli, "_owner_from_identity", lambda _: "host/worktree")
        assert cli.main(["create", "claim", "--claim", "--execution-mode", "coordination"]) == 0
        task = json.loads(capsys.readouterr().out)
        assert task["execution_mode"] == "coordination"
        assert task["owner"] == "host/worktree" and task["claimed_by_me"] is True
        assert cli.main(["create", "bad", "--execution-mode", "coordination", "--spawn"]) == 2
        assert "cannot use --spawn" in capsys.readouterr().err
        assert len(q.list()) == 1
        assert http.post("/tasks", json={
            "title": "bad", "repo": REPO, "execution_mode": "typo",
        }).status_code == 422
        with DispatchClient("http://testserver", transport=httpx.MockTransport(exchange)) as client:
            delegated = client.create("execute", repo=REPO, execution_mode="delegated")
        assert q.reserve_spawn(delegated["id"])[1] is True
    assert q.list_reservations(task_id=task["id"]) == []


def test_running_coordinator_schedules_only_delegated_execution(tmp_path):
    from agent_dispatch.supervisor import Supervisor
    from tests.test_mcp_http import _boot

    q = TaskQueue(tmp_path / "queue.db")
    url, stop = _boot(create_app(q, enable_mcp=False, verification_interval=0))
    spawned = []
    try:
        with DispatchClient(url) as client:
            claim = client.create(
                "ownership", repo=REPO, execution_mode="coordination", labels=["general"],
                claim_as="host/owner",
            )
            q.start(claim["id"], "host/owner")
            q.yield_task(claim["id"], "host/owner")
            delegated = client.create(
                "execute", repo=REPO, execution_mode="delegated", labels=["general"],
            )

            def inert_body(task):
                spawned.append(task["id"])
                q.claim_one("host/inert-worker", repo=REPO, task_id=task["id"])
                q.start(task["id"], "host/inert-worker")
                return True, {"session": "local-body:inert", "worktree": "inert-worker"}

            supervisor = Supervisor(
                client, spawn_fn=inert_body, repo=REPO, labels=["general"], max_concurrent=1,
            )
            assert supervisor.poll_once() == [delegated["id"]]
            assert spawned == [delegated["id"]]
            assert client.get(delegated["id"])["status"] == "started"
            assert client.get(claim["id"])["execution_mode"] == "coordination"
            assert q.list_reservations(task_id=claim["id"]) == []
    finally:
        stop()
