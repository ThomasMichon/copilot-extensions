from pathlib import Path

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task


def origins(queue: TaskQueue) -> list[tuple[str, ...]]:
    with queue._connect() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT a.task_id,w.reservation_key,w.allocation_project,w.worktree_path "
            "FROM task_attachments a JOIN attachment_workspaces w ON w.attachment_id=a.id ORDER BY a.id"
        )]


def test_same_tree_id_keeps_its_original_allocation_across_detach_and_restart(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "queue.db")
    expected = []
    for project in ("alpha", "beta"):
        t = q.create(project, repo="same-producing-repository")
        assert isinstance(t, Task)
        r, _ = q.reserve_spawn(t.id, driver="cli")
        path, native = str(tmp_path / project / "driver"), f"native-{project}"
        q.record_spawn_worktree(r.key, "driver", driver="cli", execution_machine="host",
                                allocation_project=project, worktree_path=path)
        q.record_spawn(r.key, session_handle=native, startup_complete=True, startup_revision=1)
        q.claim_one("host/driver", task_id=t.id)
        q.start(t.id, "host/driver", owner_session_id=native)
        q.yield_task(t.id, "host/driver")
        assert q.attachment_history(t.id)[0].detached_at is not None
        expected.append((t.id, r.key, project, path))
    assert origins(TaskQueue(q.db_path)) == expected


@pytest.mark.parametrize("mismatch", ["no-reservation", "host", "native", "path"])
def test_unqualified_attachment_is_not_reconstructed_from_task_lane(tmp_path: Path, mismatch: str) -> None:
    q = TaskQueue(tmp_path / "queue.db")
    t = q.create("work", repo="not-a-workspace-project")
    assert isinstance(t, Task)
    if mismatch != "no-reservation":
        r, _ = q.reserve_spawn(t.id, driver="cli")
        q.record_spawn_worktree(r.key, "driver", driver="cli", execution_machine="host",
                                allocation_project="alpha", worktree_path=None if mismatch == "path" else "/a/driver")
        q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    worker = "foreign/driver" if mismatch == "host" else "host/driver"
    q.claim_one(worker, task_id=t.id)
    q.start(t.id, worker, owner_session_id="other" if mismatch == "native" else "native")
    assert len(q.attachment_history(t.id)) == 1
    assert origins(TaskQueue(q.db_path)) == []
