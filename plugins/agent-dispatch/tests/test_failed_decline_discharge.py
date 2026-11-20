"""A late failed disposition does not invalidate a verified decline receipt."""

from types import SimpleNamespace

from agent_dispatch.queue import TaskQueue
from agent_dispatch.relationship_decline_history import _undischarged
from tests.test_pool_decline_closeout import governed as governed
from tests.test_relationship_discharge import replace_after_decline


def test_failed_decline_requires_retirement_before_replacement(governed: SimpleNamespace) -> None:
    queue: TaskQueue = governed.q
    key = governed.binding["reservation_key"]
    closed = queue.close_pool_decline(governed.task_id, **governed.binding)
    queue.request_spawn_release(key, disposition="failed")
    with queue._connect() as conn:
        pending = conn.execute("SELECT " + _undischarged(":id"), {"id": governed.task_id})
        assert pending.fetchone()[0]

    retired = queue.retire_spawn(key, exact_absence=True, conclusion_state="skipped")
    assert retired.state == "failed"
    replacement = replace_after_decline(
        TaskQueue(queue.db_path), governed.task_id, closed.receipt["not_before"]
    )
    assert replacement.key != key
    assert not replacement.task_relationship_required
