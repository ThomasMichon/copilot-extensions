"""Exercise unqualified coordinator launch observation without a real Bridge."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_dispatch import bridge
from agent_dispatch import ci_launch_observation as observations
from agent_dispatch.coordinator_execution_sender import CoordinatorExecutionSender
from agent_dispatch.coordinator_spawn import register_spawn_routes
from agent_dispatch.events import EventBus
from agent_dispatch.execution_scope_binding import resolve_execution_installation
from agent_dispatch.procutil import no_window_kwargs
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_custody import ExecutionSendIdentity, execution_scope_qualified
from agent_dispatch.queue_execution_runtimes import CoordinatorExecutionRuntime
from agent_dispatch.queue_records import TaskError
from tests.test_ci_repair import EVIDENCE, EXECUTION
from tests.test_execution_scope_binding import _governance


@pytest.fixture
def repair(tmp_path: Path) -> tuple[TaskQueue, Task]:
    queue = TaskQueue(tmp_path / "queue.db")
    return queue, make_repair(queue)


def make_repair(queue: TaskQueue) -> Task:
    lane, repo = "repair-lane", "github.com/example/project"
    repository = EVIDENCE["repository"]
    assert isinstance(repository, str)
    task = queue.create(
        "repair", repo=repo, target_repo=repository, source="ci-repair",
        origin_ref=lane, labels=["ci-repair"], require_verification=True,
        dedup_key=f"ci-repair:{EVIDENCE['repository']}:{EVIDENCE['signature']}",
        prompt=json.dumps({"schema": "agent-dispatch.ci-repair.v1", "failure": EVIDENCE,
                           "execution": EXECUTION}),
    )
    assert isinstance(task, Task)
    queue.register_registration("supervised-lane", {
        "repo": repo, "labels": ["ci-repair"], "ci_repair_bindings": [EXECUTION],
        "ci_repair_admission_command": ["synthetic-admission"],
    }, reg_id=lane)
    return task


def admitted(*args: object, request: dict[str, Any], **kwargs: object) -> dict[str, Any]:
    return {**request, "allowed": True, "policy_ref": "policy", "source_ref": "source",
            "inference_auth_ref": "infer", "repository_auth_ref": "checkout"}


def post(queue: TaskQueue, task: Task) -> dict[str, Any]:
    app = FastAPI()
    register_spawn_routes(app, queue, EventBus())
    with TestClient(app) as client:
        result = client.post("/spawn-reservations",
                             json={"task_id": task.id, "reserved_by": task.origin_ref})
    assert result.status_code == 200, result.text
    response: dict[str, Any] = result.json()
    assert response["reserved"] is False
    return response


@pytest.mark.parametrize("outcome", ["before-call", "reply-lost", "missing-id", "accepted",
                                     "settlement-error", "retarget-before", "retarget-during",
                                     "staging-race"])
def test_actual_unqualified_launch(
    repair: tuple[TaskQueue, Task], monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    queue, task = repair
    calls: list[str] = []
    tokens: list[str] = []

    def evidence(conn: sqlite3.Connection) -> tuple[object, ...]:
        conn.execute("BEGIN")
        assert task.repo and not execution_scope_qualified(conn, task.repo, "ci-repair")
        assert not conn.execute("SELECT 1 FROM execution_send_custody").fetchone()
        return tuple(conn.execute(
            "SELECT state,worktree,session_handle,startup_complete FROM spawn_reservations"
            " WHERE task_id=?", (task.id,),
        ).fetchone())

    def admission(*args: object, request: dict[str, Any], **kwargs: object) -> dict[str, Any]:
        if request["phase"] == "spawn":
            with queue._connect() as conn:
                assert evidence(conn) == ("reserving", None, None, 0)
                assert not conn.execute("SELECT 1 FROM ci_launch_observations").fetchone()
            if outcome == "before-call":
                raise OSError("no reply")
            if outcome == "retarget-before":
                with queue._connect() as conn:
                    conn.execute("UPDATE tasks SET prompt='{}' WHERE id=?", (task.id,))
            if outcome == "staging-race":
                res = queue.latest_reservation(task.id)
                assert res
                queue.record_staging_spawn(res.key, "local-body:other")
        return admitted(request=request)

    def launch(*args: object, **kwargs: object) -> SimpleNamespace:
        calls.append("launch")
        assert (kwargs["agent"], kwargs["target_dir"]) == (
            EXECUTION["venue"], EXECUTION["workspace"],
        )
        with queue._connect() as conn:
            assert evidence(conn) == ("reserving", None, None, 0)
            reservation = queue.latest_reservation(task.id)
            assert reservation is not None
            row = observations.observe(conn, reservation.key)
            assert row and row["task_id"] == task.id and row["task_generation"] == task.generation
            assert (row["venue"], row["workspace"]) == (kwargs["agent"], kwargs["target_dir"])
            assert row["state"] == "pending" and row["staging_handle"] is None
            tokens.append(row["attempt_id"])
        if outcome == "accepted":
            # A second caller enters while the first transport is still outstanding.
            assert task.origin_ref
            second = CoordinatorExecutionSender(
                queue, task, reservation, "other", queue.get_registration(task.origin_ref),
            )
            with pytest.raises(observations.ObservationConflict):
                with second.ci_launch(EXECUTION["venue"], EXECUTION["workspace"]):
                    pytest.fail("the second caller must not cross the transport boundary")
        if outcome == "retarget-during":
            with queue._connect() as conn:
                conn.execute("UPDATE tasks SET prompt='{}' WHERE id=?", (task.id,))
        if outcome in {"reply-lost", "retarget-during"}:
            raise OSError("no reply")
        if outcome == "missing-id":
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        return SimpleNamespace(returncode=0, stdout='{"session_id":"fixture"}', stderr="")

    def deliver(*args: object, **kwargs: object) -> bool:
        calls.append("deliver")
        return True

    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admission)
    monkeypatch.setattr(bridge, "spawn_worker", launch)
    monkeypatch.setattr(bridge, "resume_worker", deliver)
    if outcome == "settlement-error":
        def unavailable(*args: object) -> None:
            raise sqlite3.OperationalError("fixture")
        monkeypatch.setattr(observations, "settle", unavailable)
    body = post(queue, task)
    with TaskQueue(queue.db_path)._connect() as conn:
        row = observations.observe(conn, body["reservation"]["key"])
        if outcome in {"before-call", "retarget-before", "staging-race"}:
            assert row is None and not calls and not body["coordinator_launch"]["ok"]
            if outcome == "staging-race":
                assert evidence(conn) == ("reserving", None, "local-body:other", 0)
            return
        assert row and tokens == [row["attempt_id"]]
        assert (row["venue"], row["workspace"]) == (EXECUTION["venue"], EXECUTION["workspace"])
        if outcome in {"accepted", "settlement-error"}:
            assert body["coordinator_launch"]["ok"] and calls == ["launch", "deliver"]
            assert evidence(conn) == ("spawned", None, "local-body:fixture", 1)
            expected = ("pending", None) if outcome == "settlement-error" else (
                "accepted", "local-body:fixture",
            )
            assert (row["state"], row["staging_handle"]) == expected
        else:
            assert not body["coordinator_launch"]["ok"] and calls == ["launch"]
            assert evidence(conn) == ("failed", None, None, 0)
            assert row["state"] == "unknown" and row["staging_handle"] is None
    if outcome == "accepted":
        key = body["reservation"]["key"]
        queue.fail_spawn(key, detail="later lifecycle change")
        with queue._connect() as conn:
            row = observations.observe(conn, key)
            assert row and (row["state"], row["staging_handle"]) == (
                "accepted", "local-body:fixture",
            )


def die_in_transport(path: str, task_id: str) -> None:
    queue = TaskQueue(path)
    task = queue.get(task_id)
    assert task is not None
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("agent_dispatch.ci_repair_spawn._invoke", admitted)
        patch.setattr(bridge, "spawn_worker", lambda *a, **kw: os._exit(51))
        post(queue, task)


def test_process_loss_leaves_pending_without_replay(repair: tuple[TaskQueue, Task]) -> None:
    queue, task = repair
    child = subprocess.run(  # noqa: S603 -- owned interpreter and synthetic queue only
        [sys.executable, "-c", "import sys; from tests.test_unqualified_launch_observation"
         " import die_in_transport; die_in_transport(*sys.argv[1:])", str(queue.db_path), task.id],
        cwd=Path(__file__).resolve().parents[1], timeout=30, check=False, **no_window_kwargs(),
    )
    assert child.returncode == 51
    restarted = TaskQueue(queue.db_path)
    reservation = restarted.latest_reservation(task.id)
    assert reservation and task.origin_ref
    with restarted._connect() as conn:
        row = observations.observe(conn, reservation.key)
        assert row and row["state"] == "pending" and row["staging_handle"] is None
    with pytest.raises(TaskError, match="outstanding attempt"):
        observations.settle(restarted, reservation.key, "other-attempt", "local-body:other")
    replacement = CoordinatorExecutionSender(
        restarted, task, reservation, "restarted", restarted.get_registration(task.origin_ref),
    )
    with pytest.raises(TaskError, match="cannot authorize a resend"):
        with replacement.ci_launch(EXECUTION["venue"], EXECUTION["workspace"]):
            pytest.fail("an outstanding attempt cannot be replayed")
    # Even an independently qualified replacement cannot reuse this launch slot.
    assert task.repo
    restarted.handoff_producer_scope(task.repo, "ci-repair", producer_id="next",
                                     expected_generation=0)
    installation = resolve_execution_installation(_governance())
    binding = restarted.bind_execution_scope(installation, repo=task.repo, source="ci-repair",
                                             expected_producer_generation=1)
    runtime = restarted.enroll_coordinator_execution_runtime(
        binding, installation, CoordinatorExecutionRuntime("next", 1234, "start"),
    )
    runtime_id = runtime["runtime_id"]
    assert isinstance(runtime_id, str)
    identity = ExecutionSendIdentity(binding.scope, "new-send", runtime_id,
                                     reservation.key, task.generation, "launch")
    with pytest.raises(observations.ObservationConflict, match="already has"):
        restarted.admit_execution_send(identity, source="ci-repair",
                                       expected_producer_generation=1)
