"""Upgrade at several points in a governed reservation's actual lifecycle."""

import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_dispatch import submission_readiness_observation
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_local_ordinary_admission import local as local
from tests.test_ordinary_pool_startup import api as api, launch, ordinary as ordinary, q as q


def legacy_snapshot(queue: TaskQueue, path: Path, *, pre_column: bool) -> None:
    with queue._connect() as source, sqlite3.connect(path) as old:
        source.backup(old)
        # Reconstruct the two historical shapes on the synthetic copy, never on the owner.
        for name, in old.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' AND sql LIKE '%ordinary_admission_policy%'"
        ).fetchall():
            old.execute(f'DROP TRIGGER "{name}"')
        old.execute("UPDATE spawn_reservations SET ordinary_admission_policy=NULL")
        if pre_column:
            old.execute("ALTER TABLE spawn_reservations DROP COLUMN ordinary_admission_policy")


@pytest.mark.parametrize("pre_column", [False, True])
@pytest.mark.parametrize("ordinary", ["disposed"], indirect=True)
def test_upgrade_waits_for_original_claim_and_exact_retirement(
    local: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pre_column: bool,
) -> None:
    queue = local.q
    launch(local)
    key = local.placed["reservation"]["key"]
    reservation = queue.get_reservation(key)
    queue.record_spawn(key, session_handle=reservation.session_handle, worktree=reservation.worktree,
                       startup_complete=True, startup_revision=1)
    task = reservation.task_id
    worker = "host-a/worktree-a"
    queue.start(task, worker, owner_session_id="native-a", expected_generation=local.task["generation"])
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    queue.complete(task, worker, result={"ok": True})
    queue.confirm(task)

    def refused(stage: str) -> None:
        path = tmp_path / stage
        legacy_snapshot(queue, path, pre_column=pre_column)
        with sqlite3.connect(path) as old:
            before = list(old.iterdump())
            with pytest.raises(TaskError, match="settle legacy ordinary reservation"):
                TaskQueue(path)
            assert list(old.iterdump()) == before

    refused("claim-bound")
    if pre_column:
        assert queue.reconcile_task_relationship(task)["state"] == "closed"
        refused("closed-but-not-retired")
    queue.request_spawn_release(key, disposition="settled")
    queue.retire_spawn(key, exact_absence=True, conclusion_state="complete")
    if not pre_column:
        refused("retired-but-bound")
        assert queue.reconcile_task_relationship(task)["state"] == "closed"
    path = tmp_path / "settled"
    legacy_snapshot(queue, path, pre_column=pre_column)
    upgraded = TaskQueue(path)
    assert upgraded.task_relationship(task) == queue.task_relationship(task)
    with upgraded._connect() as conn:
        assert conn.execute("SELECT ordinary_admission_policy FROM spawn_reservations WHERE key=?", (key,)).fetchone()[0] is None
    # Older reservers insert the attempt, then opt it into capture without a policy.
    with pytest.raises(sqlite3.IntegrityError, match="stable admission policy"), upgraded._connect() as conn:
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO spawn_reservations(key,task_id,attempt,state,driver,reserved_at,updated_at)"
            " VALUES ('legacy-retry',?,2,'reserving','ordinary-bridge',1,1)", (task,),
        )
        conn.execute("UPDATE spawn_reservations SET task_relationship_required=1 WHERE key='legacy-retry'")
    assert upgraded.get_reservation("legacy-retry") is None


def test_upgrade_reads_each_wake_table_once(q: TaskQueue, tmp_path: Path) -> None:
    from agent_dispatch.assignment_local_ordinary import LocalOrdinaryRelationshipWriter
    from agent_dispatch.ordinary_policy_upgrade import install_policy_boundary
    from tests.test_pool_placement import pool, publish, task

    revision = publish(q, pool())
    q._ordinary_relationship_writer = LocalOrdinaryRelationshipWriter(q)
    for i in range(8):
        old = task(q, dedup_key=f"old-{i}")
        held, _ = q.reserve_spawn(
            old.id, reserved_by="allocator", driver="ordinary-bridge", pool_catalog_revision=revision
        )
        assert held.task_relationship_required
        q.request_spawn_release(held.key, finished_startup_revision=1)
        q.retire_spawn(held.key, exact_absence=True, conclusion_state="skipped")
        q.abandon(old.id, permitted=True, reason="source closed")

    path = tmp_path / "legacy-history.db"
    legacy_snapshot(q, path, pre_column=True)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        queries: list[str] = []
        conn.set_trace_callback(queries.append)
        install_policy_boundary(conn)
    for table in ("wake_outbox", "run_waiter_wakes"):
        scans = [sql for sql in queries if f"FROM {table} " in sql and "execution_send_state" in sql]
        assert len(scans) == 1


@pytest.mark.parametrize("stage", ["project-bound", "project-bound-twice", "prepared"])
@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_settled_factory_retry_upgrade(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str,
) -> None:
    from agent_dispatch import ordinary_bridge
    from tests.test_pool_startup_policy import exhaust, renew

    q = ordinary.q
    task = ordinary.task["id"]
    first = q.get_reservation(ordinary.placed["reservation"]["key"])
    if stage.startswith("project-bound"):
        q.record_spawn_allocation(first.key, "example")
    else:
        run = ordinary_bridge.subprocess.run
        with monkeypatch.context() as once:
            def refuse_create(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                if argv[2] == "create":
                    return subprocess.CompletedProcess(argv, 1, "", "native not created")
                return run(argv, **kwargs)

            once.setattr(ordinary_bridge.subprocess, "run", refuse_create)
            with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
                launch(ordinary)
    queued = exhaust(q, q.get(task), first)
    q.request_spawn_release(first.key, finished_startup_revision=1, now=701)
    q.retire_spawn(first.key, exact_absence=True, conclusion_state="skipped", now=702)
    monkeypatch.setattr(q, "_now", lambda now=None: queued.not_before if now is None else now)
    renew(q, queued.not_before)
    retry, _ = q.reserve_spawn(task, reserved_by="allocator", driver="ordinary-bridge",
                               pool_catalog_revision=q.pool_catalog_snapshot().revision)
    ordinary.placed["reservation"] = asdict(retry)
    ordinary.task.update(asdict(q.get(task)), spawn_reservation_key=retry.key)
    if stage == "project-bound-twice":
        from tests._project_retry_controls import retry_after_project
        retry = retry_after_project(ordinary, monkeypatch)
        assert retry.attempt == 3
    launch(ordinary)
    current = q.get_reservation(retry.key)
    q.record_spawn(retry.key, session_handle=current.session_handle, worktree=current.worktree,
                   startup_complete=True, startup_revision=1)
    worker = "host-a/worktree-a"
    q.start(task, worker, owner_session_id="native-a", expected_generation=ordinary.task["generation"])
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    q.complete(task, worker, result={"ok": True})
    q.confirm(task)
    q.request_spawn_release(retry.key, disposition="settled")
    q.retire_spawn(retry.key, exact_absence=True, conclusion_state="complete")
    closed = q.reconcile_task_relationship(task)
    assert closed["state"] == "closed"
    assert closed["request"]["reservation_key"] == (first.key if stage == "prepared" else retry.key)

    for fault in ("foreign-project", "missing-native", "pending-retirement", "foreign-handle",
                  "foreign-stamp", "open-claim", "unknown-send", None):
        path = tmp_path / str(fault)
        legacy_snapshot(q, path, pre_column=True)
        if fault is not None:
            with sqlite3.connect(path) as old:
                if fault == "foreign-project":
                    old.execute("UPDATE spawn_reservations SET allocation_project='foreign' WHERE key=?", (first.key,))
                elif fault == "pending-retirement":
                    old.execute("UPDATE spawn_reservations SET native_release_pending=1 WHERE key=?", (retry.key,))
                elif fault == "foreign-handle":
                    old.execute("UPDATE spawn_reservations SET session_handle='local-body:other' WHERE key=?", (retry.key,))
                elif fault == "foreign-stamp":
                    old.execute("UPDATE spawn_reservations SET agent_info=json_set(agent_info,"
                                "'$.creation.worktree.dispatch_attempt.reservation_key','other') WHERE key=?", (retry.key,))
                elif fault == "open-claim":
                    old.execute("UPDATE task_events SET task_relationship=json_set(task_relationship,'$.state','bound')"
                                " WHERE task_id=? AND task_relationship IS NOT NULL", (task,))
                elif fault == "unknown-send":
                    old.execute("INSERT INTO execution_send_custody(scope_key,send_id,repo,source,producer_generation,"
                                "task_id,reservation_key,task_generation,kind,identity,state,detail,created_at,updated_at)"
                                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                ("scope", "send", "example", "source", 1, task, retry.key,
                                 ordinary.task["generation"], "startup", "{}", "unknown", "{}", 1, 1))
                else:
                    old.execute("UPDATE spawn_reservations SET agent_info=NULL WHERE key=?", (retry.key,))
                old.commit()
                before = list(old.iterdump())
                with pytest.raises(TaskError, match="settle legacy ordinary reservation"):
                    TaskQueue(path)
                assert list(old.iterdump()) == before
        else:
            upgraded = TaskQueue(path)
            assert upgraded.task_relationship(task) == closed
            with upgraded._connect() as conn:
                assert [r[0] for r in conn.execute(
                    "SELECT ordinary_admission_policy FROM spawn_reservations WHERE task_id=?", (task,)
                )] == [None] * retry.attempt
