"""End-to-end provisional observations use the indexed current-driver lookup twice."""

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest

from agent_dispatch import provisional_driver_work, submission_readiness_observation
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from tests import test_task_relevance_closeout_transport as transport
from tests.test_pool_placement import place, pool, publish
from tests.test_task_relevance import report
from tests.test_task_relevance_binding import Assignment, _scope
from tests.test_task_relevance_closeout_readiness import Ready

managed_assignment = transport.managed_assignment


@pytest.fixture
def observer(managed_assignment: Assignment) -> Ready:
    queue, owner, original, _ = managed_assignment
    workspace = replace(original, machine="münchen")
    task = queue.create("Inspect result", repo=workspace.project, source="fixture",
                        require_verification=True, origin_ref="source", evaluator_ref="fixture")
    assert isinstance(task, Task)
    publish(queue, pool(machine="MÜNCHEN", capacity=2))
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key, workspace.worktree, ownership="created",
        execution_machine=workspace.machine,
        allocation_project=workspace.project, worktree_path=workspace.path, driver="cli",
        creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(reservation.key, session_handle="native", startup_complete=True,
                       startup_revision=reservation.startup_revision)
    queue.start(task.id, workspace.worker_id, owner_session_id="native",
                expected_generation=task.generation + 1)
    cast(Callable[..., object], report)(queue, task.id, scope=_scope(queue, task.id))
    return queue, owner, workspace, task, []


@pytest.mark.parametrize(("kind", "host", "blocked"), [
    ("assignment", "MÜNCHEN", True), ("assignment", "MÄLAGA", False),
    ("waiter", "MÜNCHEN", True), ("waiter", "MÄLAGA", False),
    ("placement", "MÜNCHEN", True),
    ("bare", None, True), ("bare-pin", "MÄLAGA", False),
    ("pin", "MÜNCHEN", True), ("pin", "MÄLAGA", False),
])
def test_exact_driver_identity_on_both_snapshots(
    observer: Ready, monkeypatch: pytest.MonkeyPatch, kind: str, host: str | None,
    blocked: bool,
) -> None:
    queue, _, workspace, _, _ = observer
    calls = 0

    def raced(*_: object) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            if kind == "placement":
                task = queue.create(
                    "Target", repo=workspace.project, target_worktree=workspace.worktree,
                )
                assert isinstance(task, Task)
                reservation, created = place(queue, task)
                assert created and reservation.worktree == workspace.worktree
            else:
                pinned = kind in {"pin", "bare-pin"}
                if kind in {"bare", "bare-pin"}:
                    owner = workspace.worktree
                elif kind == "pin":
                    owner = "worker-1"
                else:
                    owner = f"{host}/{workspace.worktree}"
                with queue._connect() as conn:
                    if kind == "waiter":
                        conn.execute(
                            "INSERT INTO run_waiters(task_id,generation,pid,owner,"
                            "owner_session_id,resume_worktree,state,created_at,updated_at)"
                            " VALUES ('other',1,0,?,'native',?,'active',1,1)", (owner, owner),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO tasks(id,status,owner,target_machine,target_worktree)"
                            " VALUES ('other','claimed',?,?,?)",
                            (owner, host if pinned else None,
                             workspace.worktree if pinned else None),
                        )
        return {"ready": True}

    monkeypatch.setattr(submission_readiness_observation, "observe", raced)
    with transport.api(observer) as api:
        statuses = [
            api.post(
                transport.path(observer, "/observe-provisional"),
                headers=transport.headers(observer), json=transport.body(observer),
            ).status_code
            for _ in range(2)
        ]
    assert (statuses, calls) == (([409, 409], 1) if blocked else ([200, 200], 2))


def test_settled_same_tree_reservations_have_bounded_cost(
    observer: Ready, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    queue, _, workspace, _, _ = observer
    original = provisional_driver_work.require_no_outstanding_driver_work
    costs: list[int] = []

    def measured(q: TaskQueue, conn: sqlite3.Connection, task: Task,
                 reservation: dict[str, Any], native: str) -> None:
        cost = 0

        def step() -> int:
            nonlocal cost
            cost += 1
            return 0

        conn.set_progress_handler(step, 1)
        try:
            original(q, conn, task, reservation, native)
        finally:
            conn.set_progress_handler(None, 0)
        costs.append(cost)

    monkeypatch.setattr(provisional_driver_work, "require_no_outstanding_driver_work", measured)
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    with transport.api(observer) as api:
        for start, end in ((0, 20), (20, 2000)):
            rows = [{"id": f"old-{i}", "tree": workspace.worktree} for i in range(start, end)]
            with queue._connect() as conn:
                conn.executemany("INSERT INTO tasks(id,status) VALUES (:id,'completed')", rows)
                conn.executemany(
                    "INSERT INTO spawn_reservations(key,task_id,attempt,state,worktree,"
                    "reserved_at,updated_at) VALUES (:id,:id,1,'settled',:tree,1,1)", rows,
                )
                conn.executemany(
                    "INSERT INTO task_pool_assignments(task_id,registration_id,machine,env,"
                    "reservation_key,task_generation,catalog_revision,assignment_released)"
                    " VALUES (:id,'pool','MÜNCHEN','linux',:id,1,1,1)", rows,
                )
                # The reverse join must not just trade settled-tree scans for live-pool scans.
                conn.executemany(
                    "INSERT INTO tasks(id,status,generation) VALUES ('new-'||:id,'claimed',1)",
                    rows,
                )
                conn.executemany(
                    "INSERT INTO spawn_reservations(key,task_id,attempt,state,worktree,"
                    "reserved_at,updated_at) VALUES ('new-'||:id,'new-'||:id,1,'reserving',"
                    "'another-tree',1,1)", rows,
                )
                conn.executemany(
                    "INSERT INTO task_pool_assignments(task_id,registration_id,machine,env,"
                    "reservation_key,task_generation,catalog_revision,assignment_released)"
                    " VALUES ('new-'||:id,'pool','MÜNCHEN','linux','new-'||:id,1,1,0)", rows,
                )
            response = api.post(transport.path(observer, "/observe-provisional"),
                                headers=transport.headers(observer), json=transport.body(observer))
            assert response.status_code == 200, response.text
    record_property("pre_post_steps_20_then_2000", costs)
    assert len(costs) == 4
    for small, large in zip(costs[:2], costs[2:], strict=True):
        assert large <= 2 * small, costs
