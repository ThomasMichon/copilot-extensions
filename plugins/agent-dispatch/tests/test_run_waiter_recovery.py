from __future__ import annotations

from agent_dispatch.run_waiter_recovery import recover_run_waiters
from tests._helpers import RepoDefaultingQueue as TaskQueue

TEST_HOST = "test-host"


def _suspended_task(queue: TaskQueue) -> str:
    task = queue.create("wait")
    queue.claim_one("worker-1", task_id=task.id)
    queue.start(task.id, "worker-1", owner_session_id="session-1")
    queue.suspend(task.id, "worker-1", reason="waiting on external state")
    return task.id


def test_recover_run_waiters_leaves_live_waiter_alone(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host=TEST_HOST,
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda pid: pid == 101,
        start_token_for_pid=lambda pid: "token-101" if pid == 101 else None,
        current_machine=TEST_HOST,
    )

    assert counts == {"checked": 1, "live": 1, "unknown": 0, "recovered": 0}
    assert queue.get_active_run_waiter(task_id) is not None


def test_recover_run_waiters_wakes_dead_waiter_and_releases_claim(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host=TEST_HOST,
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )
    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: False,
        start_token_for_pid=lambda _pid: None,
        current_machine=TEST_HOST,
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 0, "recovered": 1}
    assert queue.get_active_run_waiter(task_id) is None
    wakes = queue.list_run_waiter_wakes(task_id)
    assert len(wakes) == 1
    assert wakes[0].resume_worktree == "m/wt-1"
    assert wakes[0].status == "pending"


def test_superseded_waiter_drops_late_completion(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host=TEST_HOST,
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )
    queue.supersede_run_waiter(task_id, reason="event note wake")

    assert (
        queue.retire_run_waiter(
            task_id,
            generation=1,
            pid=101,
            host=TEST_HOST,
            start_token="token-101",
            reason="waiter completed",
        )
        is None
    )


def test_recover_run_waiters_treats_uncertain_tokens_as_unknown(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host=TEST_HOST,
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: True,
        start_token_for_pid=lambda _pid: None,
        current_machine=TEST_HOST,
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 1, "recovered": 0}
    assert queue.get_active_run_waiter(task_id) is not None


def test_recover_run_waiters_treats_hostless_records_as_unknown(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host=None,
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: False,
        start_token_for_pid=lambda _pid: None,
        current_machine=TEST_HOST,
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 1, "recovered": 0}
    assert queue.get_active_run_waiter(task_id) is not None


def test_recover_run_waiters_treats_missing_start_token_as_unknown(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host=TEST_HOST,
        start_token=None,
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: True,
        start_token_for_pid=lambda _pid: "token-101",
        current_machine=TEST_HOST,
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 1, "recovered": 0}
    assert queue.get_active_run_waiter(task_id) is not None


def test_recover_run_waiters_recovers_stale_preparing_waiter(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.prepare_run_waiter(
        task_id,
        worker_id="worker-1",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
        reason="hibernating: sleep 1",
        now=1000.0,
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: False,
        start_token_for_pid=lambda _pid: None,
        current_machine=TEST_HOST,
        arm_grace_seconds=10.0,
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 0, "recovered": 1}
    assert queue.get_active_run_waiter(task_id) is None
    wakes = queue.list_run_waiter_wakes(task_id)
    assert len(wakes) == 1
    assert wakes[0].task_id == task_id
