"""Exercise queue-owned preparation and terminal detachment, not a copied selector policy."""

import json
import sqlite3
from pathlib import Path

import pytest
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.completion_origins import (
    PreparationOrigin,
    capture_terminal_detachment,
    exact_detached_completion,
    preparation_origins,
)
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnReservation, TaskError
from tests.test_assignment_relationships import GroundOwner
from tests.test_pool_placement import current_task, place, pool, publish, record_native, task


def test_failed_proposal_keeps_its_exact_acquisition_after_same_worker_reclaims(
    tmp_path: Path,
) -> None:
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.db", relationship_writer=owner, relationship_authority="local"
    )
    owner.queue = queue
    t = task(queue)
    space = AssignmentWorkspace("host-a", "example", "tree", str(tmp_path / "tree"), "first")
    assert isinstance(
        queue.claim_one(space.worker_id, task_id=t.id, assignment_workspace=space), Task
    )
    queue.start(t.id, space.worker_id, owner_session_id="first-native")
    with pytest.raises(TaskError, match="readiness"):
        queue.complete(t.id, space.worker_id, result={"prior": True})
    old = queue.submission_preparation(t.id)
    assert old is not None and old["blocker"] and old.get("advancement") is None
    old_event = old["request"]["assignment_event_id"]
    queue.yield_task(t.id, space.worker_id)
    assert queue.reconcile_task_relationship(t.id)["state"] == "closed"
    assert isinstance(
        queue.claim_one(space.worker_id, task_id=t.id, assignment_workspace=space), Task
    )
    current = queue.task_relationship(t.id)
    assert current and current["request"]["event_id"] != old_event
    with queue._connect() as conn:
        conn.execute("BEGIN")
        [actual] = preparation_origins(conn, t.id)
        assert actual.assignment_event_id == old_event
        # An unattributable legacy proposal must be visible, not quietly filtered out.
        altered = {
            **old,
            "request": {**old["request"], "assignment_event_id": current["request"]["event_id"]},
        }
        corrupt = conn.execute(
            "INSERT INTO task_events(task_id,ts,from_status,to_status,worker,note,submission_preparation)"
            " VALUES (?,100,'started','started',?,'fixture',?)",
            (t.id, space.worker_id, json.dumps(altered)),
        ).lastrowid
        assert corrupt is not None
        assert preparation_origins(conn, t.id) == (actual, PreparationOrigin(corrupt, None))
        conn.rollback()
    assert queue.submission_preparation(t.id) == old


def _started_pool(tmp_path: Path) -> tuple[TaskQueue, Task, SpawnReservation]:
    queue = TaskQueue(tmp_path / "queue.db")
    publish(queue, pool())
    t = task(queue)
    reservation, _ = place(queue, t)
    record_native(queue, reservation, worktree="tree", native="execution-native")
    queue.record_spawn(
        reservation.key,
        session_handle="execution-native",
        startup_complete=True,
        startup_revision=1,
    )
    queue.start(
        t.id,
        "host-a/tree",
        expected_generation=current_task(queue, t.id).generation,
        owner_session_id="execution-native",
    )
    return queue, t, reservation


@pytest.mark.parametrize("history", ["verified", "legacy", "enriched"])
def test_only_verified_terminal_detachment_can_identify_exact_pool_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: str,
) -> None:
    from agent_dispatch import completion_origins

    queue, t, reservation = _started_pool(tmp_path)
    payload = {"answer": [1, 2]}
    if history == "enriched":
        queue.complete(t.id, "host-a/tree")
    done = queue.complete(t.id, "host-a/tree", result=payload, result_ref="object:one")
    encoded = queue._encode_result(payload)
    with queue._connect() as conn:
        conn.execute("BEGIN")
        assert not exact_detached_completion(
            conn, t.id, worker_id="host-a/tree", encoded_result=encoded, result_ref="object:one"
        )
    queue.request_spawn_release(reservation.key, disposition="settled")
    with pytest.raises(TaskError):
        queue.recover_task(
            t.id,
            operation="detach_agent",
            expected_revision=queue.task_diagnostics(t.id)["revision"],
            request_id="detach",
            actor="operator",
            reason="retired native",
        )
    queue.retire_spawn(reservation.key, exact_absence=True)
    if history == "legacy":
        monkeypatch.setattr(completion_origins, "capture_terminal_detachment", lambda *_: None)
    revision = queue.task_diagnostics(t.id)["revision"]
    result = queue.recover_task(
        t.id,
        operation="detach_agent",
        expected_revision=revision,
        request_id="detach",
        actor="operator",
        reason="retired native",
    )
    qualified = history == "verified"
    assert ("completion_origin_v1" in result) is qualified
    if qualified:
        origin = result["completion_origin_v1"]
        assert (origin["reservation_key"], origin["generation"], origin["native_id"]) == (
            reservation.key,
            done.generation,
            "execution-native",
        )
        assert "answer" not in json.dumps(origin)
    reopened = TaskQueue(queue.db_path)
    after = current_task(reopened, t.id)
    assert (
        after.status == "completed"
        and after.generation == done.generation + 1
        and after.owner_session_id is None
    )
    with reopened._connect() as conn:
        conn.execute("BEGIN")
        for worker, value, ref, expected in (
            ("host-a/tree", encoded, "object:one", qualified),
            ("other/tree", encoded, "object:one", False),
            ("host-a/tree", None, "object:one", False),
            ("host-a/tree", encoded, None, False),
        ):
            assert (
                exact_detached_completion(
                    conn, t.id, worker_id=worker, encoded_result=value, result_ref=ref
                )
                is expected
            )
        if qualified:
            # Even the same worker asking for the same (now substituted) stored
            # bytes is not replaying the outcome the detach owner sealed.
            conn.execute("UPDATE tasks SET result_ref='different' WHERE id=?", (t.id,))
            assert not exact_detached_completion(
                conn,
                t.id,
                worker_id="host-a/tree",
                encoded_result=encoded,
                result_ref="different",
            )
            conn.rollback()
    assert reopened.recover_task(
        t.id,
        operation="detach_agent",
        expected_revision=revision,
        request_id="detach",
        actor="operator",
        reason="retired native",
    ) == {**result, "replayed": True}
    assert current_task(reopened, t.id) == after


def test_terminal_origin_work_stays_task_scoped_on_upgraded_queue(tmp_path: Path) -> None:
    queue, t, reservation = _started_pool(tmp_path)
    done = queue.complete(t.id, "host-a/tree", result={"done": True})
    unrelated = task(queue)
    queue.request_spawn_release(reservation.key, disposition="settled")
    queue.retire_spawn(reservation.key, exact_absence=True)
    with queue._connect() as conn:
        conn.execute("DROP INDEX idx_task_events_task")
    reopened = TaskQueue(queue.db_path)
    with reopened._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        def measured() -> int:
            instructions = 0

            def tick() -> int:
                nonlocal instructions
                instructions += 1
                return 0

            conn.set_progress_handler(tick, 1)
            try:
                origin = capture_terminal_detachment(conn, t.id)
            finally:
                conn.set_progress_handler(None, 0)
            assert origin and origin["reservation_key"] == reservation.key
            return instructions

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        baseline = measured()
        conn.set_trace_callback(None)
        lookup = next(query for query in statements if "JOIN task_events e" in query)
        # Match the completion candidate *and* disqualifying later-event shape,
        # but on another task. Neither audit lookup should visit this history.
        conn.executemany(
            "INSERT INTO task_events(task_id,ts,from_status,to_status,worker,note)"
            " VALUES (?,?,'started','submitted','host-a/tree','complete')",
            ((unrelated.id, done.completed_at) for _ in range(3000)),
        )
        enlarged = measured()
        assert enlarged <= baseline + 100, (baseline, enlarged)
        plans = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + lookup)]
        for alias in ("e", "later"):
            searches = [step for step in plans if f"SEARCH {alias} " in step]
            assert any("(task_id=?" in step for step in searches), plans
        conn.rollback()


def test_conflicting_acquisition_native_leaves_replay_unqualified(tmp_path: Path) -> None:
    queue, t, reservation = _started_pool(tmp_path)
    queue.complete(t.id, "host-a/tree", result_ref="object:one")
    queue.request_spawn_release(reservation.key, disposition="settled")
    queue.retire_spawn(reservation.key, exact_absence=True)
    generation = current_task(queue, t.id).generation

    def conflicting_receipt(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO acquisition_receipts(reservation_key,task_id,task_generation,worktree_id,session_id,"
            "native_session_id,mode,delivery_id) VALUES (?,?,?,'tree','bridge','different-native','cli','delivery')",
            (reservation.key, t.id, generation),
        )

    with queue._connect() as conn:
        conn.execute("BEGIN")
        assert capture_terminal_detachment(conn, t.id) is not None
        conflicting_receipt(conn)
        changes = conn.total_changes
        assert capture_terminal_detachment(conn, t.id) is None
        assert conn.total_changes == changes
        conn.rollback()
    queue.recover_task(
        t.id,
        operation="detach_agent",
        expected_revision=queue.task_diagnostics(t.id)["revision"],
        request_id="detach",
        actor="operator",
        reason="retired native",
    )
    with queue._connect() as conn:
        conn.execute("BEGIN")

        def matches() -> bool:
            return exact_detached_completion(
                conn, t.id, worker_id="host-a/tree", encoded_result=None, result_ref="object:one"
            )

        assert matches()
        conflicting_receipt(conn)
        changes = conn.total_changes
        assert not matches()
        assert conn.total_changes == changes
        conn.rollback()
        conn.execute("BEGIN")
        assert matches()

        def deny_receipt_read(action: int, table: str | None, *_: object) -> int:
            if action == sqlite3.SQLITE_READ and table == "acquisition_receipts":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(deny_receipt_read)
        with pytest.raises(sqlite3.DatabaseError):
            matches()
