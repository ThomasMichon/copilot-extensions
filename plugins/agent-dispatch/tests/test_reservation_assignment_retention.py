"""A reservation that precedes worker acceptance still fences task disposal."""

from pathlib import Path

import pytest

from agent_dispatch.ordinary_agent_storage import record_created_agent
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import Status, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from tests._helpers import required
from tests.test_pool_placement import place, pool, publish, record_native


def test_obsolete_target_sweep_and_supersession_preserve_reservation(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(
        "review",
        repo="example",
        exclusive_key="review:7",
        source="emitter",
        origin_ref="review:7",
        target_worktree="driver",
        target_machine="host",
        now=100,
    )
    assert isinstance(task, Task)
    reservation, _ = queue.reserve_spawn(task.id, now=101)
    queue.record_spawn(reservation.key, session_handle="local-body:original")
    snapshot = queue.relevance_snapshot(task.id)
    result = queue.reconcile_task_relevance(
        task.id,
        request_id="obsolete",
        scope=RelevanceScope("example", "emitter", "review:7"),
        expected_generation=task.generation,
        expected_revision=snapshot["revision"],
        verdict="obsolete",
        reason="source closed",
        observation_ref="source:closed",
        reporter="emitter",
    )
    assert result["decision"] == "closeout_pending"
    assert not queue.reap_orphaned_targets(set(), machine="host", grace_secs=0, now=102)["reaped"]
    with pytest.raises(TaskError, match="owner closeout"):
        queue.create("replacement", repo="example", exclusive_key="review:7",
                     supersede_exclusive_key=True)
    with pytest.raises(TaskError, match="retains assignment"):
        queue.abandon(task.id, permitted=True)
    with pytest.raises(TaskError, match="pending source closeout"):
        queue.request_spawn_release(reservation.key)
    assert required(queue.get(task.id)).status == Status.QUEUED
    retained = queue.get_reservation(reservation.key)
    assert retained and retained.session_handle == "local-body:original"


@pytest.mark.parametrize("suspended", [False, True])
@pytest.mark.parametrize("placed", [False, True], ids=["accounting-only", "allocator-placed"])
def test_voluntary_return_requires_retirement_only_for_placement(
    tmp_path: Path, placed: bool, suspended: bool,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("review", repo="example", labels=["worker"], target_worktree="driver")
    assert isinstance(task, Task)
    if placed:
        publish(queue, pool())
        res, _ = place(queue, task)
    else:
        res, _ = queue.reserve_spawn(task.id, reserved_by="allocator", driver="cli")
    retained = record_native(queue, res, worktree="driver", native="original")
    if not placed:
        # Legacy capacity adoption records a lane, not an allocator's task claim.
        with queue._connect() as conn:
            conn.execute("INSERT INTO pool_reservation_bindings VALUES (?,?,?,?,?)",
                         (res.key, "worker", "host-a", "linux", "[]"))
        queue.claim_one("host-a/driver", task_id=task.id, worktree="driver", machine="host-a")
    queue.start(
        task.id, "host-a/driver", owner_session_id="original",
        expected_generation=required(queue.get(task.id)).generation,
    )
    if suspended:
        queue.suspend(task.id, "host-a/driver", reason="waiting")
    before = required(queue.get(task.id))
    operation = queue.release_suspended if suspended else queue.yield_task
    with pytest.raises(TaskError, match="owned by"):
        operation(task.id, "host-a/independent")
    if placed:
        with pytest.raises(TaskError, match="retains assignment"):
            operation(task.id, "host-a/driver")
        assert queue.get(task.id) == before
    else:
        returned = operation(task.id, "host-a/driver")
        assert returned.status == Status.QUEUED
        assert returned.owner_session_id is returned.owner is None
    assert queue.get_reservation(res.key) == retained


@pytest.mark.parametrize("bridge", [False, True], ids=["user-worker", "ordinary-bridge"])
def test_independent_worker_can_yield_and_accept_next_task_with_wip(
    tmp_path: Path, bridge: bool,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    workspace = tmp_path / "worktree-a"
    workspace.mkdir()
    wip = workspace / "unfinished.txt"
    wip.write_text("unfinished work\n", encoding="utf-8")
    task = queue.create("review", repo="example")
    assert isinstance(task, Task)
    if bridge:
        res, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="ordinary-bridge")
        queue.record_spawn_allocation(res.key, "example")
        record_created_agent(queue, res.key, task_generation=task.generation, agent_info={
            "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
            "connection": {"native_session_id": "native-a", "worktree_id": "worktree-a",
                           "repo": "example", "target_dir": str(workspace)},
        })
        queue.record_spawn(res.key, session_handle="local-body:bridge-a", startup_complete=True,
                           startup_revision=res.startup_revision)
    queue.claim_one("host-a/worktree-a", task_id=task.id)
    queue.start(task.id, "host-a/worktree-a", owner_session_id="native-a")
    reservation = queue.latest_reservation(task.id)
    assert bool(reservation and reservation.agent_info) is bridge
    assert queue.yield_task(task.id, "host-a/worktree-a").status == Status.QUEUED
    next_task = queue.create("different goal", repo="example")
    assert isinstance(next_task, Task)
    assert queue.claim_one("host-a/worktree-a", task_id=next_task.id) is not None
    started = queue.start(next_task.id, "host-a/worktree-a", owner_session_id="native-a")
    assert (started.owner, started.owner_session_id) == ("host-a/worktree-a", "native-a")
    assert queue.latest_reservation(task.id) == reservation
    assert wip.read_text(encoding="utf-8") == "unfinished work\n"


def test_failed_attempt_retains_native_published_only_in_acquisition(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("review", repo="example")
    assert isinstance(task, Task)
    publish(queue, pool())
    res, _ = place(queue, task)
    queue.record_spawn_worktree(res.key, "driver", ownership="targeted", driver="cli",
                                execution_machine="host-a")
    with queue._connect() as conn:
        conn.execute(
            "INSERT INTO acquisition_receipts VALUES (?,?,?,?,?,?,?,?)",
            (res.key, task.id, task.generation, "driver", "bridge", "native", "acp", "delivery"),
        )
    failed = queue.fail_spawn(res.key)
    assert failed.session_handle is None and failed.agent_info is None
    before = queue.get(task.id)
    with pytest.raises(TaskError, match="retains assignment"):
        queue.abandon(task.id, permitted=True)
    assert queue.get(task.id) == before
