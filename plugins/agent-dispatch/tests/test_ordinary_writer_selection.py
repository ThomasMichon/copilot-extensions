"""An active reservation, not the restarted coordinator, selects the admission policy."""

from pathlib import Path
from typing import Any

import pytest
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from tests.test_assignment_relationships import GroundOwner
from tests.test_pool_placement import pool, publish, task


class RecordedWriter(GroundOwner):
    def __init__(self) -> None:
        super().__init__()
        self.selections = 0

    def bind_request(self, request: dict[str, Any]) -> "RecordedWriter":
        self.selections += 1
        return self


@pytest.mark.parametrize("stage", ["prepare", "reconcile"])
@pytest.mark.parametrize(
    "before,after",
    [
        ("native", "factory:a"),
        ("factory:a", "native"),
        ("factory:a", "factory:b"),
        ("native", "unknown"),
        ("native", "native"),
        ("factory:a", "factory:a"),
    ],
)
def test_original_policy_checked_before_any_writer_contact(
    tmp_path: Path,
    before: str,
    after: str,
    stage: str,
) -> None:
    original, other = RecordedWriter(), RecordedWriter()
    db = tmp_path / "tasks.db"

    def open_queue(policy: str, writer: RecordedWriter) -> TaskQueue:
        if policy == "native":
            queue = TaskQueue(
                db, ordinary_relationship_writer=writer, relationship_authority="local"
            )
        else:
            queue = TaskQueue(db, relationship_writer=writer, relationship_authority="local")
            queue._ordinary_factory_policy = policy if policy != "unknown" else None
        writer.queue = queue
        return queue

    q = open_queue(before, original)
    t = task(q)
    key = q.reserve_spawn(
        t.id,
        reserved_by="allocator",
        driver="ordinary-bridge",
        pool_catalog_revision=publish(q, pool()),
    )[0].key

    def prepare(queue: TaskQueue) -> None:
        queue.record_spawn_worktree(
            key,
            "work",
            driver="ordinary-bridge",
            ownership="created",
            execution_machine="host-a",
            creating_host="host-a",
            allocation_project="example",
            worktree_path=str(tmp_path / "work"),
            creation_nonce="incarnation",
        )

    if stage == "reconcile":
        original.lose_reply = "reserve"
        with pytest.raises(TaskError, match="unresolved"):
            prepare(q)
    original.calls.clear()
    original.selections = 0
    reopened = open_queue(after, original if before == after else other)
    snapshot = reopened.get_reservation(key), reopened.task_relationship(t.id)

    def act() -> None:
        if stage == "prepare":
            prepare(reopened)
        else:
            reopened.reconcile_task_relationship(t.id)

    if before == after:
        act()
        assert original.selections and original.calls and not other.calls
        value = reopened.task_relationship(t.id)
        assert value and value["state"] == "bound"
    else:
        with pytest.raises(TaskError, match="admission policy"):
            act()
        assert (reopened.get_reservation(key), reopened.task_relationship(t.id)) == snapshot
        assert (original.selections, original.calls, other.selections, other.calls) == (
            0,
            [],
            0,
            [],
        )


def test_embedding_without_policy_cannot_reserve_governed_ordinary(tmp_path: Path) -> None:
    a = RecordedWriter()
    q = TaskQueue(tmp_path / "db", relationship_writer=a, relationship_authority="local")
    t = task(q)
    revision = publish(q, pool())
    with pytest.raises(TaskError, match="stable admission policy"):
        q.reserve_spawn(
            t.id, reserved_by="allocator", driver="ordinary-bridge", pool_catalog_revision=revision,
        )
    assert q.get(t.id) == t and not q.list_reservations(task_id=t.id)
    assert q.task_pool_assignment(t.id) is None and q.task_relationship(t.id) is None
    assert (a.selections, a.calls) == (0, [])
