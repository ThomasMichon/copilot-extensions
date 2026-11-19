from __future__ import annotations

from pathlib import Path

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch import queue_emitter_updates
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_emitter_updates import QueuedObservation, observe_queued, update_queued
from agent_dispatch.queue_records import TaskError


def test_queued_update_keeps_task_and_refuses_late_replay(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    def create(payload: str) -> Task:
        task = queue.create("Review example/project#7", payload_inline=payload, repo="example/project",
                            dedup_key="review:7", source="recipe", origin_ref="reviews")
        assert isinstance(task, Task)
        return task

    task = create("A")

    def basis() -> QueuedObservation:
        return observe_queued(queue, task.id, repo="example/project", key="review:7", source="recipe", origin="reviews")

    a = basis()
    assert update_queued(queue, a, "B")
    b = basis()
    assert not update_queued(queue, a, "B") and basis() == b
    assert update_queued(queue, b, "C")
    with pytest.raises(TaskError, match="changed"):
        update_queued(queue, a, "B")
    current = create("D")
    assert (current.id, current.status, current.payload_inline) == (task.id, "queued", "C")
    assert not queue.list_wakes(task.id)


def test_queue_claim_and_hold_win_over_an_earlier_observation(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("Review", repo="example", dedup_key="key", source="recipe", origin_ref="reviews",
                        payload_inline="A")
    assert isinstance(task, Task)
    with pytest.raises(TaskError, match="original task"):
        observe_queued(queue, task.id, repo="example", key="key", source="recipe", origin="someone-else")
    a = observe_queued(queue, task.id, repo="example", key="key", source="recipe", origin="reviews")
    queue.set_hold(task.id, reason="maintenance", actor="operator")
    with pytest.raises(TaskError, match="available queued"):
        update_queued(queue, a, "B")
    queue.clear_hold(task.id)
    queue.claim_one("fixture", repo="example", task_id=task.id)
    with pytest.raises(TaskError, match="available queued"):
        update_queued(queue, a, "B")
    current = queue.get(task.id)
    assert current is not None and current.payload_inline == "A"
    yielded = queue.yield_task(task.id, "fixture")
    assert yielded.status == "queued" and not queue.list_reservations(task_id=task.id)
    with pytest.raises(TaskError, match="previously assigned"):
        observe_queued(queue, task.id, repo="example", key="key", source="recipe", origin="reviews")


def test_cursor_seeks_to_same_status_event_among_unrelated_history(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", repo="example", dedup_key="key", source="recipe", origin_ref="reviews")
    assert isinstance(task, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        tail = conn.execute(
            "INSERT INTO task_events(task_id,ts,from_status,to_status) VALUES (?,1,'queued','queued')",
            (task.id,),
        ).lastrowid
        conn.executemany("INSERT INTO task_events(task_id,ts) VALUES (?,2)", ((f"other-{i}",) for i in range(4096)))
        conn.execute("ANALYZE task_events")
        plan = conn.execute("EXPLAIN QUERY PLAN " + queue_emitter_updates._CURSOR_QUERY, (task.id,)).fetchall()
    assert any("SEARCH task_events USING COVERING INDEX idx_task_events_task (task_id=?)" in r[3] for r in plan)
    basis = observe_queued(queue, task.id, repo="example", key="key", source="recipe", origin="reviews")
    assert basis.last_event == tail


@pytest.mark.parametrize("protection", ["fenced-task", "new-source-scope", "foreign-label"])
def test_managed_protection_refuses_without_changing_payload_or_cursor(tmp_path: Path, protection: str) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    lane, key, source, origin = "example/project", "review:7", "recipe", "reviews"
    if protection == "fenced-task":
        grant = queue.handoff_producer_scope(lane, source, producer_id=origin, expected_generation=0)
        task = queue.create("review", repo=lane, dedup_key=key, source=source, origin_ref=origin,
                            payload_inline="A", producer_scope={"repo": lane, "source": source},
                            producer_id=origin, producer_generation=1, producer_capability=grant.producer_capability,
                            producer_request_id="first")
    else:
        task = queue.create("review", repo=lane, dedup_key=key, source=source, origin_ref=origin, payload_inline="A")
    assert isinstance(task, Task)
    if protection == "fenced-task":
        # A supplied basis must not bypass admission even with the correct cursor.
        with queue._connect() as conn:
            basis = QueuedObservation(task.id, lane, key, source, origin, queue_emitter_updates._cursor(conn, task.id))
    else:
        basis = observe_queued(queue, task.id, repo=lane, key=key, source=source, origin=origin)
        if protection == "new-source-scope":
            queue.handoff_producer_scope(lane, source, producer_id=origin, expected_generation=0)
        else:
            queue.handoff_producer_scope("other/project", "other-source", producer_id="other",
                                         expected_generation=0, required_label="protected")
            # Normal creation rejects foreign protected labels. Model a legacy row.
            with queue._connect() as conn:
                conn.execute("UPDATE tasks SET labels=? WHERE id=?", ('["protected"]', task.id))
    before = queue.get(task.id), queue.events(task.id)
    with pytest.raises(TaskError, match="managed emitter observations"):
        observe_queued(queue, task.id, repo=lane, key=key, source=source, origin=origin)
    with pytest.raises(TaskError, match="managed emitter observations"):
        update_queued(queue, basis, "B")
    assert (queue.get(task.id), queue.events(task.id)) == before
