"""A reservation that precedes worker acceptance still fences task disposal."""

from pathlib import Path

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import Status, TaskError


def test_obsolete_target_sweep_and_supersession_preserve_reservation(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("review", repo="example", exclusive_key="review:7",
                        target_worktree="driver", target_machine="host", now=100)
    assert isinstance(task, Task)
    reservation, _ = queue.reserve_spawn(task.id, now=101)
    queue.record_spawn(reservation.key, session_handle="local-body:original")
    assert not queue.reap_orphaned_targets(set(), machine="host", grace_secs=0, now=102)["reaped"]
    with pytest.raises(TaskError, match="owner closeout"):
        queue.create("replacement", repo="example", exclusive_key="review:7",
                     supersede_exclusive_key=True)
    with pytest.raises(TaskError, match="retains assignment"):
        queue.abandon(task.id, permitted=True)
    assert queue.get(task.id) == task
    retained = queue.get_reservation(reservation.key)
    assert retained and retained.session_handle == "local-body:original"


def test_release_cannot_unassign_a_suspended_reserved_worker(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("review", repo="example")
    assert isinstance(task, Task)
    res, _ = queue.reserve_spawn(task.id)
    queue.record_spawn(res.key, session_handle="local-body:original")
    queue.claim_one("host/driver", task_id=task.id)
    queue.start(task.id, "host/driver", owner_session_id="original")
    queue.suspend(task.id, "host/driver", reason="awaiting update", cooldown_seconds=None)
    with pytest.raises(TaskError, match="retains assignment"):
        queue.release_suspended(task.id, "host/driver")
    retained = queue.get(task.id)
    assert retained and (retained.status, retained.owner, retained.owner_session_id) == (
        Status.SUSPENDED, "host/driver", "original",
    )


def test_failed_attempt_retains_native_published_only_in_acquisition(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("review", repo="example")
    assert isinstance(task, Task)
    res, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(res.key, "driver", ownership="targeted")
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
