"""Pending reviewer verification retains one durable task without index repair."""

from __future__ import annotations

import multiprocessing

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status, TaskQueue
from agent_dispatch.queue_dedup import DedupConflictError
from agent_dispatch.verification import evaluate_submitted_task

REPO = "github.com/example/project"
KEY = "recipe:reviewer:target=github.com/example/project#42"
LEGACY_KEY = "recipe:reviewer:base=main:pr=42:repo=example/project"


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _submitted(queue, *, key=KEY, held=False):
    task = queue.create(
        "review",
        repo=REPO,
        dedup_key=key,
        source="recipe",
        origin_ref="reviewer",
        require_verification=True,
        evaluator_ref="review-lifecycle",
        labels=["review"],
    )
    queue.claim_one("review-worker", repo=REPO, task_id=task.id)
    queue.start(task.id, "review-worker", owner_session_id=f"session-{task.id}")
    if held:
        queue.set_hold(task.id, reason="operator pause", actor="operator")
    submitted = queue.complete(task.id, "review-worker")
    assert submitted.status == Status.SUBMITTED
    return submitted


def test_pending_verification_dedup_keeps_actual_submission_and_read_only_detail(
    queue, monkeypatch
):
    original = _submitted(queue, held=True)
    assert (
        queue.create(
            "rediscovered",
            repo=REPO,
            dedup_key=KEY,
            require_verification=True,
            claim_as="other-worker",
        )
        == original
    )
    assert queue.lookup_dedup(KEY, repo=REPO) == original
    api = TestClient(create_app(queue))
    with queue._connect() as conn:
        before = "\n".join(conn.iterdump())
    connect = queue._connect

    def read_only_connection():
        conn = connect()
        conn.execute("PRAGMA query_only = ON")
        return conn

    monkeypatch.setattr(queue, "_connect", read_only_connection)
    try:
        expected = api.get(f"/tasks/{original.id}").json()
        response = api.get("/tasks", params={"repo": REPO, "dedup_key": KEY})
        assert response.status_code == 200
        assert response.json() == [expected]
        assert expected["hold_reason"] == "operator pause"
        assert expected["require_verification"] is True
    finally:
        api.close()
    with queue._connect() as conn:
        assert "\n".join(conn.iterdump()) == before


def _create_and_lookup(db_path, barrier, results):
    queue = TaskQueue(db_path)
    barrier.wait(timeout=15)
    ids = []
    for _ in range(3):
        ids.append(queue.create("rediscovered", repo=REPO, dedup_key=KEY).id)
        ids.append(queue.lookup_dedup(KEY, repo=REPO).id)
    results.put(ids)


def test_pending_verification_dedup_serializes_independent_process_creates(queue):
    original = _submitted(queue)
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(3)
    results = ctx.Queue()
    processes = [
        ctx.Process(target=_create_and_lookup, args=(queue.db_path, barrier, results))
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=15)
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert results.get(timeout=5) == [original.id] * 6
        assert results.get(timeout=5) == [original.id] * 6
        assert queue.list(repo=REPO) == [original]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
        results.close()
        results.join_thread()


def test_pending_verification_dedup_evaluator_rejection_retains_original(queue):
    original = _submitted(queue)
    queue.register_registration(
        "evaluator",
        {
            "repo": REPO,
            "evaluator_ref": "review-lifecycle",
            "evaluator_spec": {"rules": [{"reject": {"reason": "PR remains open"}}]},
        },
    )
    report = evaluate_submitted_task(
        queue,
        original.id,
        trigger="submitted",
        current_machine="host-a",
        current_env="default",
    )
    assert report["applied"][0]["decision"] == "reject"
    recovered = queue.get(original.id)
    assert recovered.id == original.id
    assert recovered.status != Status.SUBMITTED
    assert queue.lookup_dedup(KEY, repo=REPO) == recovered
    assert queue.create("rediscovered", repo=REPO, dedup_key=KEY) == recovered


@pytest.mark.parametrize("original_key", [KEY, LEGACY_KEY])
def test_pending_verification_dedup_preserves_legacy_reviewer_alias(queue, original_key):
    original = _submitted(queue, key=original_key)
    other_key = LEGACY_KEY if original_key == KEY else KEY
    assert (
        queue.create(
            "alias rediscovery",
            repo=REPO,
            dedup_key=other_key,
            source="recipe",
            origin_ref="reviewer",
        )
        == original
    )
    assert queue.lookup_dedup(original_key, repo=REPO) == original
    assert queue.lookup_dedup(other_key, repo=REPO) is None


@pytest.mark.parametrize("alias", [False, True])
def test_pending_verification_dedup_historical_conflict_is_local_not_startup_failure(queue, alias):
    first = _submitted(queue)
    second = _submitted(queue, key="other-subject")
    second_key = LEGACY_KEY if alias else KEY
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET dedup_key = ? WHERE id = ?", (second_key, second.id))
        index_before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'idx_tasks_dedup'"
        ).fetchone()[0]
    records_before = [queue.get(first.id), queue.get(second.id)]
    # Reopening a historical database must not rebuild a stronger index.
    reopened = TaskQueue(queue.db_path)
    assert [reopened.get(first.id), reopened.get(second.id)] == records_before
    with reopened._connect() as conn:
        assert (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'idx_tasks_dedup'"
            ).fetchone()[0]
            == index_before
        )
        before = "\n".join(conn.iterdump())
    if alias:
        assert reopened.lookup_dedup(KEY, repo=REPO).id == first.id
    else:
        with pytest.raises(DedupConflictError) as caught:
            reopened.lookup_dedup(KEY, repo=REPO)
        assert caught.value.task_ids == sorted([first.id, second.id])
    with pytest.raises(DedupConflictError) as caught:
        reopened.create(
            "rediscovered",
            repo=REPO,
            dedup_key=KEY,
            source="recipe",
            origin_ref="reviewer",
        )
    assert caught.value.repo == REPO and caught.value.dedup_key == KEY
    assert caught.value.task_ids == sorted([first.id, second.id])
    with reopened._connect() as conn:
        assert "\n".join(conn.iterdump()) == before
    api = TestClient(create_app(reopened))
    try:
        if not alias:
            response = api.get("/tasks", params={"repo": REPO, "dedup_key": KEY})
            assert response.status_code == 409
            assert response.json()["detail"] == caught.value.detail()
        response = api.post(
            "/tasks",
            json={
                "title": "rediscovered",
                "repo": REPO,
                "dedup_key": KEY,
                "source": "recipe",
                "origin_ref": "reviewer",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "dedup_conflict"
        assert reopened.create("unrelated", repo=REPO, dedup_key="unrelated").id
    finally:
        api.close()


@pytest.mark.parametrize("terminal", ["confirm", "abandon"])
def test_pending_verification_dedup_explicit_terminal_releases_key(queue, terminal):
    original = _submitted(queue)
    if terminal == "confirm":
        queue.confirm(original.id, actor="evaluator")
    else:
        queue.abandon(original.id, permitted=True, reason="target abandoned")
    assert queue.lookup_dedup(KEY, repo=REPO) is None
    assert queue.create("new generation", repo=REPO, dedup_key=KEY).id != original.id


def test_pending_verification_dedup_unverified_complete_releases_key(queue):
    original = queue.create("self-attested", repo=REPO, dedup_key=KEY)
    queue.claim_one("worker", repo=REPO, task_id=original.id)
    queue.start(original.id, "worker")
    assert queue.complete(original.id, "worker").status == Status.COMPLETED
    assert queue.lookup_dedup(KEY, repo=REPO) is None
    assert queue.create("new generation", repo=REPO, dedup_key=KEY).id != original.id
