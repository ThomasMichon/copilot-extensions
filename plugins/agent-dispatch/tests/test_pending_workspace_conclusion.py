"""Workspace conclusion custody remains visible across settlement and retry claims."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

from agent_dispatch.current_driver_lookup import DriverMatch
from agent_dispatch.pending_workspace_conclusion import pending_workspace_conclusion
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task


def test_plain_writer_pending_retry_until_owner_records_completion(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("Finished", repo="example")
    assert isinstance(task, Task)
    with sqlite3.connect(queue.db_path) as writer, queue._connect() as reader:
        writer.execute("UPDATE tasks SET status='completed' WHERE id=?", (task.id,))
        writer.execute(
            "INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,updated_at,"
            "worktree,creating_host,session_handle) VALUES ('retry',?,1,'settled',1,1,'tree',"
            "'MÜNCHEN','local-body:bridge-id')", (task.id,),
        )
        writer.commit()

        def result(host: str = "münchen") -> DriverMatch:
            return pending_workspace_conclusion(reader, machine=host, worktree="tree")

        reader.execute("BEGIN")
        assert result() is DriverMatch.CLEAR
        queue.record_spawn_conclusion("retry", conclusion_state="pending", conclusion_detail="{}")
        assert result() is DriverMatch.CLEAR  # earlier snapshot
        reader.rollback()
        reader.execute("BEGIN")
        assert result() is DriverMatch.MATCH and result("foreign") is DriverMatch.CLEAR
        _, claimed, token = queue.claim_spawn_conclusion_retry("retry")
        assert claimed and token
        queue.validate_spawn_conclusion_claim("retry", claim_token=token)
        reader.rollback()
        reader.execute("BEGIN")
        assert result() is DriverMatch.MATCH  # claiming the retry did not settle it
        queue.record_spawn_conclusion("retry", conclusion_state="complete", conclusion_detail="{}",
                                      claim_token=token)
        reader.rollback()
        reader.execute("BEGIN")
        assert result() is DriverMatch.CLEAR
        reader.rollback()
        writer.execute("UPDATE spawn_reservations SET creating_host=NULL,"
                       "conclusion_state='pending'")
        writer.commit()
        reader.execute("BEGIN")
        assert result() is DriverMatch.UNKNOWN


def test_pending_conclusion_probe_skips_history_and_other_workspaces(
    tmp_path: Path, record_property: Callable[[str, object], None],
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    with queue._connect() as conn:
        costs: list[int] = []
        for start, end in ((0, 20), (20, 2000)):
            conn.execute("BEGIN")
            for tree, conclusion in (("captured", "complete"), ("other", "pending")):
                conn.executemany(
                    "INSERT INTO spawn_reservations(key,task_id,attempt,state,reserved_at,"
                    "updated_at,worktree,conclusion_state) VALUES (?,?,1,'settled',1,1,?,?)",
                    [(f"{tree}-{i}", f"{tree}-{i}", tree, conclusion) for i in range(start, end)],
                )
            conn.commit()
            conn.execute("BEGIN")
            steps = 0

            def count() -> int:
                nonlocal steps
                steps += 1
                return 0

            conn.set_progress_handler(count, 1)
            try:
                assert pending_workspace_conclusion(
                    conn, machine="host", worktree="captured",
                ) is DriverMatch.CLEAR
            finally:
                conn.set_progress_handler(None, 0)
            costs.append(steps)
            conn.rollback()
        record_property("vm_steps_20_then_2000", costs)
        assert costs[1] <= costs[0] * 2, costs
