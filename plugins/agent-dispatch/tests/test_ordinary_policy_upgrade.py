"""Upgrade at several points in a governed reservation's actual lifecycle."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

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
