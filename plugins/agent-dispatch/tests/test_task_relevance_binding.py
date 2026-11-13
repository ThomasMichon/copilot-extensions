"""Closeout binds the accepted assignment, not a later workspace-history match."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import Task, TaskQueue
from agent_dispatch.queue_relevance import RelevanceScope
from tests import test_assignment_relationships as relationships
from tests.test_assignment_relationships import claim, start_managed
from tests.test_task_relevance import report, request

managed_assignment = relationships.assignment
Assignment = tuple[TaskQueue, relationships.GroundOwner, AssignmentWorkspace, Task]


@pytest.fixture
def assignment(managed_assignment: Assignment) -> Assignment:
    queue, owner, workspace, task = managed_assignment
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET source='emitter', origin_ref='review-source' WHERE id=?", (task.id,),
        )
    return queue, owner, workspace, queue.get(task.id)


def _scope(queue: TaskQueue, task_id: str) -> RelevanceScope:
    task = queue.get(task_id)
    return RelevanceScope(task.repo, task.source, task.origin_ref)


def _binding(queue: TaskQueue, task_id: str) -> dict | None:
    return queue.relevance_snapshot(task_id)["closeout"]["resource_binding"]


@pytest.mark.parametrize("pooled", [False, True])
def test_acceptance_captures_exact_relationship_and_native_reservation(
    assignment: Assignment, pooled: bool,
) -> None:
    queue, _, workspace, task = assignment
    if pooled:
        start_managed(queue, workspace, task)
    else:
        claim(queue, workspace, task)
        queue.start(task.id, workspace.worker_id, owner_session_id="native")
    relationship = queue.task_relationship(task.id)["request"]
    before_calls = list(queue._relationship_writer.calls)
    report(queue, task.id, scope=_scope(queue, task.id))
    binding = _binding(queue, task.id)
    reservation = queue.get_reservation(relationship["reservation_key"]) if pooled else None
    assert binding == {
        "state": "captured",
        "queue_identity": relationship["recovery"]["queue_identity"],
        "assignment_event_id": relationship["event_id"],
        "relationship_request_id": relationship["request_id"],
        "workspace": relationship["workspace"],
        "reservation_key": relationship["reservation_key"],
        "startup_revision": reservation.startup_revision if reservation else None,
    }
    assert queue._relationship_writer.calls == before_calls
    closeout = queue.relevance_receipt(task.id, "closed-source")["closeout"]
    assert closeout["resource_binding"] == binding
    assert (closeout["owner"], closeout["owner_session_id"], closeout["generation"]) == (
        workspace.worker_id, "native", queue.get(task.id).generation,
    )


def test_binding_survives_unassignment_restart_and_duplicate_reports(assignment: Assignment) -> None:
    queue, owner, workspace, task = assignment
    start_managed(queue, workspace, task)
    original_request = request(queue, task.id, scope=_scope(queue, task.id))
    accepted = queue.reconcile_task_relevance(task.id, **original_request)
    original = _binding(queue, task.id)
    queue.yield_task(task.id, workspace.worker_id)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET assignment_released=1 WHERE task_id=?", (task.id,),
        )
    restarted = TaskQueue(
        queue.db_path, relationship_writer=owner, relationship_authority="local",
    )
    assert restarted.reconcile_task_relevance(task.id, **original_request) == accepted
    report(restarted, task.id, scope=_scope(restarted, task.id), request_id="later-source-report")
    assert _binding(restarted, task.id) == original
    assert restarted.get(task.id).owner is None


def test_resumed_owner_keeps_original_relationship_and_current_generation(
    assignment: Assignment,
) -> None:
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    queue.start(task.id, workspace.worker_id, owner_session_id="first-native")
    original = queue.task_relationship(task.id)["request"]
    queue.suspend(task.id, workspace.worker_id, reason="awaiting source")
    resumed = queue.resume(task.id, workspace.worker_id, adopt_owner_session_id="resumed-native")
    assert resumed.generation > original["generation"]
    report(queue, task.id, scope=_scope(queue, task.id))
    binding = _binding(queue, task.id)
    assert binding["state"] == "captured"
    assert binding["assignment_event_id"] == original["event_id"]
    closeout = queue.relevance_receipt(task.id, "closed-source")["closeout"]
    assert closeout["generation"] == resumed.generation
    assert closeout["owner_session_id"] == "resumed-native"


@pytest.mark.parametrize("change", ["native", "reservation", "relationship", "generation"])
def test_inconsistent_binding_stays_visible_without_blocking_closeout_delivery(
    assignment: Assignment, change: str,
) -> None:
    queue, _, workspace, task = assignment
    start_managed(queue, workspace, task)
    relationship = queue.task_relationship(task.id)["request"]
    with queue._connect() as conn:
        if change == "native":
            conn.execute(
                "UPDATE spawn_reservations SET session_handle='other-native' WHERE key=?",
                (relationship["reservation_key"],),
            )
        elif change == "reservation":
            conn.execute(
                "UPDATE task_pool_assignments SET reservation_key='missing-reservation' WHERE task_id=?",
                (task.id,),
            )
        elif change == "generation":
            conn.execute(
                "UPDATE task_pool_assignments SET task_generation=task_generation+1 WHERE task_id=?",
                (task.id,),
            )
        else:
            row = conn.execute(
                "SELECT task_relationship FROM task_events WHERE id=?", (relationship["event_id"],),
            ).fetchone()
            value = {**json.loads(row["task_relationship"]), "state": "pending"}
            conn.execute(
                "UPDATE task_events SET task_relationship=? WHERE id=?",
                (json.dumps(value), relationship["event_id"]),
            )
    accepted = report(queue, task.id, scope=_scope(queue, task.id))
    assert accepted["decision"] == "closeout_pending"
    assert len(queue.list_wakes(task.id)) == 1
    assert _binding(queue, task.id)["state"] == "unconfirmed"
    assert _binding(queue, task.id)["reason"]
    assert "workspace" not in _binding(queue, task.id)


def test_legacy_or_preacceptance_unassigned_owner_is_not_guessed(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("work", repo="example", source="emitter", origin_ref="review-source")
    queue.claim_one("host/worker", task_id=task.id)
    queue.start(task.id, "host/worker", owner_session_id="native")
    queue.yield_task(task.id, "host/worker")
    report(queue, task.id, scope=_scope(queue, task.id))
    assert _binding(queue, task.id) == {"state": "unconfirmed", "reason": "owner_unavailable"}
    assert queue.list_wakes(task.id) == []


def test_old_closeout_rows_remain_explicitly_uncaptured_after_migration(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE task_relevance_closeouts ("
            "task_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, generation INTEGER NOT NULL,"
            "owner TEXT, owner_session_id TEXT, steer_id INTEGER NOT NULL, created_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO task_relevance_closeouts VALUES ('legacy', 'original', 1,"
            " 'host/worker', 'native', 1, 100)"
        )
    queue = TaskQueue(path)
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM task_relevance_closeouts WHERE task_id='legacy'").fetchone()
    assert row["resource_binding"] is None


_MIGRATE_CHILD = """
import pathlib, sqlite3, sys, time
from agent_dispatch.queue_relevance import QueueRelevanceMixin

root = pathlib.Path(sys.argv[2])

class Connection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, parameters)
        if sql == 'PRAGMA table_info(task_relevance_closeouts)':
            rows = cursor.fetchall()
            if not any(row[1] == 'resource_binding' for row in rows):
                (root / ('ready-' + sys.argv[3])).write_text('ready')
                deadline = time.monotonic() + 10
                while not all((root / ('ready-' + name)).exists() for name in ('0', '1')):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('other migration did not observe the legacy schema')
                    time.sleep(0.01)
            return rows
        return cursor

with sqlite3.connect(sys.argv[1], isolation_level=None, timeout=15, factory=Connection) as conn:
    QueueRelevanceMixin._migrate_task_relevance(conn)
print('migrated')
"""


def test_independent_initializers_can_both_migrate_the_same_legacy_column(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    with sqlite3.connect(path) as conn:
        for table in ("task_events", "acquisition_receipts", "execution_send_custody"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER, task_id TEXT)")
        conn.execute(
            "CREATE TABLE task_relevance_closeouts ("
            "task_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, generation INTEGER NOT NULL,"
            "owner TEXT, owner_session_id TEXT, steer_id INTEGER NOT NULL, created_at REAL NOT NULL)"
        )

    def migrate(identity: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", _MIGRATE_CHILD, str(path), str(tmp_path), str(identity)],
            text=True, capture_output=True, timeout=30, **no_window_kwargs(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(migrate, identity) for identity in (0, 1)]
        results = [future.result(timeout=40) for future in futures]
    assert all(result.returncode == 0 for result in results), [
        (result.returncode, result.stdout, result.stderr) for result in results
    ]
    assert all(result.stdout.strip() == "migrated" for result in results)
    with sqlite3.connect(path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(task_relevance_closeouts)")]
    assert columns.count("resource_binding") == 1
