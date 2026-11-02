"""Explicit actual-owner SQLite/JSONL lane; synthetic guest custody, no Docker."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "plugins" / "agent-dispatch" / "src"))
sys.path.insert(0, str(ROOT / "plugins" / "agent-bridge" / "src"))

import initial_case


@pytest.fixture
def owners(tmp_path: Path, monkeypatch: Any) -> Any:
    from agent_bridge.acquisition_journal import AcquisitionJournal
    from agent_bridge.acquisition_store import AcquisitionStore
    from agent_bridge.db import Database
    from agent_dispatch.queue import TaskQueue
    from process_telemetry.diagnostic_logs import DiagnosticLog
    from single_instance_lease import OperationLock

    dispatch_path = tmp_path / "dispatch" / "queue.db"
    dispatch_path.parent.mkdir(mode=0o700)
    queue = TaskQueue(dispatch_path)
    bridge_path = tmp_path / "bridge" / "bridge.db"
    writable = Database(bridge_path)
    AcquisitionStore(writable)
    writable.close()
    database = Database(bridge_path, read_only=True)
    journal = AcquisitionJournal(database)
    evidence = tmp_path / "diagnostics"
    evidence.mkdir(mode=0o700)
    monkeypatch.setattr(initial_case, "DIAGNOSTICS", evidence)
    log = DiagnosticLog(
        "reproducer", evidence, service="reproducer-lab", custody=OperationLock,
        create_parent=False, initial_maintenance=False,
    )
    # This lane exercises actual storage owners, not Linux 1000:1000/mount admission.
    monkeypatch.setattr(
        initial_case, "_journal_paths",
        lambda *_args: (initial_case.FileIdentity(1, 2),),
    )
    try:
        with OperationLock(tmp_path / "driver.lock") as holder:
            yield SimpleNamespace(
                queue=queue, journal=journal, log=log,
                custody=SimpleNamespace(assert_owned=holder.assert_owned),
            )
    finally:
        log.close()
        database.close()


def submit(owners: Any) -> Any:
    return initial_case.submit_initial_task(
        owners.custody, owners.queue, owners.journal, owners.log,
    )


def test_actual_queue_create_observes_the_synced_entry_first(owners: Any, monkeypatch: Any) -> None:
    from process_telemetry import append

    original = owners.queue.create_outcome
    entries: list[dict[str, object]] = []
    syncs: list[str] = []
    fsync = os.fsync

    def observe_sync(descriptor: int) -> None:
        info = os.fstat(descriptor)
        fsync(descriptor)
        if os.path.samestat(info, owners.log.directory.stat()):
            syncs.append("directory")
        elif owners.log.path.exists() and os.path.samestat(info, owners.log.path.stat()):
            syncs.append("file")

    monkeypatch.setattr(append.os, "fsync", observe_sync)

    def create(title: str, **kwargs: object) -> Any:
        assert syncs == ["file", "directory"]
        rows = owners.log.path.read_bytes().splitlines()
        assert len(rows) == 1
        entry = json.loads(rows[0].decode("utf-8"))
        assert entry["event"] == "initial_task_create_entry"
        assert entry["case"] == initial_case.CASE and "prompt" not in entry
        entries.append(entry)
        return original(title, **kwargs)

    monkeypatch.setattr(owners.queue, "create_outcome", create)
    outcome = submit(owners)
    assert outcome.disposition == "created" and len(entries) == 1
    task = owners.queue.get(outcome.task.id)
    assert task is not None and task.prompt.endswith("offline-case-complete.")
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(owners)
    assert len(owners.queue.list(limit=2)) == 1


@pytest.mark.parametrize("failure", ["file", "directory"])
def test_actual_sync_failure_never_creates_a_task(
    owners: Any, monkeypatch: Any, failure: str,
) -> None:
    import stat
    from process_telemetry import append

    fsync = os.fsync

    def fail(descriptor: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if is_directory == (failure == "directory"):
            raise OSError("synthetic sync failure")
        fsync(descriptor)

    monkeypatch.setattr(append.os, "fsync", fail)
    with pytest.raises(OSError, match="sync failure"):
        submit(owners)
    assert owners.queue.list(limit=1) == []


def test_actual_lost_create_reply_uses_existing_task_history(owners: Any, monkeypatch: Any) -> None:
    original = owners.queue.create_outcome
    calls = 0

    def lose(title: str, **kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        original(title, **kwargs)
        raise OSError("synthetic lost create reply")

    monkeypatch.setattr(owners.queue, "create_outcome", lose)
    with pytest.raises(OSError, match="lost create reply"):
        submit(owners)
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(owners)
    assert calls == 1 and len(owners.queue.list(limit=2)) == 1


@pytest.mark.parametrize("table,statement,values", [
    (
        "acquisition_markers",
        "INSERT INTO acquisition_markers VALUES (?)",
        ("orphan-reservation",),
    ),
    (
        "acquisition_receipts",
        "INSERT INTO acquisition_receipts VALUES (?,?,?,?,?,?,?,?)",
        ("orphan-reservation", "orphan-task", 1, "synthetic-worktree",
         "synthetic-session", "synthetic-native", "acp", "synthetic-delivery"),
    ),
    (
        "producer_create_requests",
        "INSERT INTO producer_create_requests VALUES (?,?,?,?,?,?,?,?)",
        (initial_case.LANE, "synthetic-source", 1, "synthetic-request",
         "0" * 64, "synthetic-producer", "orphan-task", 1.0),
    ),
    (
        "handoff_fallback_attempts",
        "INSERT INTO handoff_fallback_attempts VALUES (?,?,?,?,?)",
        ("orphan-task", 1.0, "pending", None, 1.0),
    ),
])
def test_actual_queue_orphan_history_refuses_without_task_or_reservation(
    owners: Any, table: str, statement: str, values: tuple[object, ...],
) -> None:
    with owners.queue._connect() as connection:
        connection.execute(statement, values)
        assert connection.execute("SELECT 1 FROM tasks LIMIT 1").fetchone() is None
        assert connection.execute("SELECT 1 FROM spawn_reservations LIMIT 1").fetchone() is None
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(owners)
    assert not owners.log.path.exists() and owners.queue.list(limit=1) == []
    with owners.queue._connect() as connection:
        assert connection.execute(f"SELECT 1 FROM {table}").fetchone() is not None


@pytest.mark.parametrize("table,values", [
    ("acquisition_launch_gates", (
        "orphan-worktree", "synthetic-delivery", "acp", None, None, "synthetic-driver",
    )),
    ("acquisition_bindings", (
        "synthetic-session", "synthetic-native", "orphan-task", 1,
        "orphan-worktree", "acp", "local", 1, 1.0,
    )),
])
def test_actual_store_orphan_gate_or_retired_binding_refuses(
    owners: Any, table: str, values: tuple[object, ...],
) -> None:
    with sqlite3.connect(owners.journal.db.db_path) as connection:
        connection.execute(
            f"INSERT INTO {table} VALUES ({','.join('?' for _ in values)})", values,
        )
        assert connection.execute("SELECT 1 FROM sessions LIMIT 1").fetchone() is None
        assert connection.execute("SELECT 1 FROM acquisition_attempts LIMIT 1").fetchone() is None
    with pytest.raises(ValueError, match="Bridge work"):
        submit(owners)
    assert not owners.log.path.exists() and owners.queue.list(limit=1) == []
    assert len(owners.journal.db.execute_read(f"SELECT 1 FROM {table}")) == 1


def test_actual_queue_task_keyed_schema_is_covered_independently(owners: Any) -> None:
    with owners.queue._connect() as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )]
        task_history = {
            table for table in tables
            if {"task_id", "reservation_key", "assignment_id"} & {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
        }
    assert task_history <= set(initial_case.DISPATCH_HISTORY + initial_case.OPTIONAL_DISPATCH_HISTORY)
    assert {"acquisition_markers", "acquisition_receipts", "producer_create_requests",
            "handoff_fallback_attempts"} <= task_history


def test_actual_bridge_operation_keyed_schema_is_covered_independently(owners: Any) -> None:
    database = owners.journal.db
    tables = [row[0] for row in database.execute_read(
        "SELECT name FROM sqlite_master WHERE type='table'",
    )]
    operation_history = {
        table for table in tables
        if {"session_id", "worktree_id", "delivery_id", "alias_session_id", "target_session_id"} & {
            row[1] for row in database.execute_read(f"PRAGMA table_info({table})")
        }
    }
    assert operation_history <= set(initial_case.BRIDGE_HISTORY)
    assert {"acquisition_launch_gates", "acquisition_bindings"} <= operation_history


def test_actual_cleanup_owner_orphan_receipt_refuses(owners: Any) -> None:
    queue = owners.queue
    task = queue.create(
        "Completed synthetic cleanup fixture", repo=initial_case.LANE,
        exclusive_key="synthetic-cleanup",
    )
    reservation, inserted = queue.reserve_spawn(task.id, now=10)
    assert inserted
    queue.record_spawn_worktree(
        reservation.key, "synthetic-worker", ownership="created",
        creating_host="synthetic-host", driver="supervisor",
        allocation_project="synthetic-project", worktree_path="synthetic-workspace",
    )
    queue.record_spawn(reservation.key, session_handle="synthetic-session")
    worker = "synthetic-host/synthetic-worker"
    queue.claim_one(worker, task_id=task.id)
    queue.start(task.id, worker, owner_session_id="synthetic-native")
    queue.complete(task.id, worker, result_ref="artifact:synthetic-result")
    queue.confirm(task.id, actor="operator")
    queue.settle_spawn(
        reservation.key, conclusion_state="held", release_requested=True,
        conclusion_detail=json.dumps({
            "action": "preserved", "reason": "synthetic-cleanup-held",
            "cleanup_kind": "attempt", "attempts": 1, "next_attempt_at": 0,
            "session_end": {"state": "complete", "session_id": "synthetic-native"},
            "worktree_cleanup": {"state": "held"},
        }),
    )
    generation = queue.get(task.id).generation
    preview = queue.recover_held_cleanup(
        reservation.key, expected_generation=generation, current_host="synthetic-host",
    )
    result = queue.recover_held_cleanup(
        reservation.key, expected_generation=generation, current_host="synthetic-host",
        apply=True, expected_preview=preview["preview_token"],
        request_id="synthetic-cleanup-request", reason="synthetic-owner-fixture",
    )
    assert result["scheduled"] is True
    # Simulate orphaned history only in this test's temporary, owner-prepared DB.
    with queue._connect() as connection:
        for table in reversed(initial_case.DISPATCH_HISTORY):
            connection.execute(f"DELETE FROM {table}")
        assert connection.execute("SELECT 1 FROM tasks").fetchone() is None
        assert connection.execute("SELECT 1 FROM spawn_reservations").fetchone() is None
        retained = dict(connection.execute("SELECT * FROM cleanup_recoveries").fetchone())
    test_actual_queue_task_keyed_schema_is_covered_independently(owners)
    with pytest.raises(ValueError, match="Dispatch work"):
        submit(owners)
    assert not owners.log.path.exists() and queue.list(limit=1) == []
    with queue._connect() as connection:
        assert dict(connection.execute("SELECT * FROM cleanup_recoveries").fetchone()) == retained
