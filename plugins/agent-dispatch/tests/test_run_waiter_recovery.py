from __future__ import annotations

from agent_dispatch.run_waiter_recovery import recover_run_waiters
from tests._helpers import RepoDefaultingQueue as TaskQueue


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
        host="lambda-core",
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda pid: pid == 101,
        start_token_for_pid=lambda pid: "token-101" if pid == 101 else None,
        wake_worktree=lambda *_a: False,
        release_claim=lambda *_a: None,
        current_machine="lambda-core",
    )

    assert counts == {"checked": 1, "live": 1, "unknown": 0, "recovered": 0}
    assert queue.get_active_run_waiter(task_id) is not None


def test_recover_run_waiters_wakes_dead_waiter_and_releases_claim(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host="lambda-core",
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )
    wakes = []
    claims = []

    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: False,
        start_token_for_pid=lambda _pid: None,
        wake_worktree=lambda worktree, message: wakes.append((worktree, message)) or True,
        release_claim=lambda task_id, worktree: claims.append((task_id, worktree)) or {"released": True},
        current_machine="lambda-core",
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 0, "recovered": 1}
    assert queue.get_active_run_waiter(task_id) is None
    assert wakes and wakes[0][0] == "m/wt-1"
    assert claims == [(task_id, "m/wt-1")]


def test_superseded_waiter_drops_late_completion(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    task_id = _suspended_task(queue)
    queue.register_run_waiter(
        task_id,
        pid=101,
        host="lambda-core",
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )
    queue.supersede_run_waiter(task_id, reason="event note wake")

    assert (
        queue.retire_run_waiter(
            task_id,
            pid=101,
            host="lambda-core",
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
        host="lambda-core",
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
    )

    counts = recover_run_waiters(
        queue,
        process_exists=lambda _pid: True,
        start_token_for_pid=lambda _pid: None,
        wake_worktree=lambda *_a: False,
        release_claim=lambda *_a: None,
        current_machine="lambda-core",
    )

    assert counts == {"checked": 1, "live": 0, "unknown": 1, "recovered": 0}
    assert queue.get_active_run_waiter(task_id) is not None
