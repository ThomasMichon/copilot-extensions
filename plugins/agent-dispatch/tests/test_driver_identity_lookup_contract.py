"""End-to-end provisional observations use the indexed current-driver lookup twice."""

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from agent_dispatch import submission_readiness_observation
from agent_dispatch.execution_effect_adapter import ExecutionScope
from agent_dispatch.execution_send_destination import ExecutionDestination
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_custody import ExecutionSendIdentity
from agent_dispatch.queue_execution_handoff import _scope_key
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


def test_legacy_writer_unknown_send_during_observation_blocks_both_snapshots(
    observer: Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, _, _, _, _ = observer
    scope = ExecutionScope("installation", "coordinator", "other", "fixture")
    old = ExecutionSendIdentity(scope, "old", "runtime", "retired", 1, "launch")
    calls = 0

    def observe(*_: object) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        with sqlite3.connect(queue.db_path) as writer:
            writer.execute(
                "INSERT INTO execution_send_custody VALUES (?,?,'other','fixture',1,'other',"
                "?,1,'launch',?,'unknown','reply lost',1,1)",
                (_scope_key(scope), old.send_id, old.reservation_key, old.encoded()),
            )
        return {"ready": True}

    monkeypatch.setattr(submission_readiness_observation, "observe", observe)
    with transport.api(observer) as api:
        for _ in range(2):
            response = api.post(transport.path(observer, "/observe-provisional"),
                                headers=transport.headers(observer), json=transport.body(observer))
            assert response.status_code == 409 and "immutable driver destination" in response.text
        assert calls == 1


@pytest.mark.parametrize("unresolved", [False, True], ids=["settled-tree", "unresolved-sends"])
def test_observation_request_sql_cost(
    observer: Ready, monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None], unresolved: bool,
) -> None:
    queue, _, workspace, _, _ = observer
    connect = queue._connect
    costs: list[int] = []
    steps = 0
    measuring = False

    def measured() -> sqlite3.Connection:
        conn = connect()

        def step() -> int:
            nonlocal steps
            steps += 1
            return 0

        if measuring:
            conn.set_progress_handler(step, 1)
        return conn

    def observe(*_: object) -> dict[str, bool]:
        nonlocal steps
        costs.append(steps)
        steps = 0
        return {"ready": True}

    monkeypatch.setattr(queue, "_connect", measured)
    monkeypatch.setattr(submission_readiness_observation, "observe", observe)
    with transport.api(observer) as api:
        for start, end in ((0, 20), (20, 2000)):
            rows = [{"id": f"old-{i}", "tree": "foreign" if unresolved else workspace.worktree}
                    for i in range(start, end)]
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
                if unresolved:
                    scope = ExecutionScope("installation", "coordinator", "other", "fixture")
                    for r in rows:
                        send = ExecutionSendIdentity(
                            scope, str(r["id"]), "runtime", str(r["id"]), 1, "launch",
                            destination=ExecutionDestination("driver", "other", "foreign"),
                        )
                        r["identity"] = send.encoded()
                        r["scope"] = _scope_key(scope)
                    conn.executemany(
                        "INSERT INTO wake_outbox(id,task_id,owner,generation,wake_seq,status,"
                        "created_at,updated_at,execution_send_state) VALUES (:id,:id,"
                        "'other/foreign',1,1,'failed',1,1,'unknown')", rows,
                    )
                    conn.executemany(
                        "INSERT INTO execution_send_custody(scope_key,send_id,repo,source,"
                        "producer_generation,task_id,reservation_key,task_generation,kind,"
                        "identity,state,detail,created_at,updated_at) VALUES (:scope,:id,"
                        "'other','fixture',1,:id,:id,1,'launch',:identity,'unknown','',1,1)", rows,
                    )
            headers, body = transport.headers(observer), transport.body(observer)
            steps, measuring = 0, True
            response = api.post(transport.path(observer, "/observe-provisional"),
                                headers=headers, json=body)
            measuring = False
            costs.append(steps)
            assert response.status_code == 200, response.text
    record_property("authorization_pre_and_post_steps_20_then_2000", costs)
    assert len(costs) == 4
    for small, large in zip(costs[:2], costs[2:], strict=True):
        assert large <= 2 * small, costs
