"""A later qualified delivery cannot attest to an earlier unqualified attempt."""

import json
from pathlib import Path

import pytest

from agent_dispatch.bridge_command_delivery import NativePromptNotSent
from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.provisional_driver_work import require_no_outstanding_driver_work
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_wakes import TABLES, Wake, wake_table
from agent_dispatch.queue_records import TaskError
from tests import test_execution_wake_sends as controls


@pytest.mark.parametrize("waiter", [False, True], ids=["owner", "waiter"])
@pytest.mark.parametrize("initial", ["unqualified", "known-not-sent", "first"])
@pytest.mark.parametrize("accepted", [False, True], ids=["not-sent", "sent"])
def test_qualified_result_covers_only_its_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, waiter: bool, initial: str, accepted: bool,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create("result", repo=controls.REPO, source=controls.SOURCE)
    assert isinstance(task, Task)
    queue.claim_one(controls.OWNER, task_id=task.id)
    task = queue.start(task.id, controls.OWNER, owner_session_id="session-a")
    pending: Wake
    if waiter:
        queue.suspend(task.id, controls.OWNER, reason="external")
        registration = queue.register_run_waiter(
            task.id, pid=123, host="host", start_token="fixture", resume_worktree=controls.OWNER,
            command=["fixture"],
        )
        queue.recover_dead_run_waiter(task.id, generation=registration["generation"],
                                      reason="finished", message="done", sender="fixture")
        [pending] = queue.list_run_waiter_wakes(task.id)
    else:
        queue.submit_steer(task.id, fields={"result": 1}, wake_requested=True)
        [pending] = queue.list_wakes(task.id)
    wake = controls._claim(queue, pending)
    assert wake and wake.owner_session_id

    def refused() -> bool:
        raise NativePromptNotSent("bridge-a", "session-a", pending.id)

    if initial != "unqualified":
        controls._qualify(queue, task, monkeypatch)
    if initial != "first":
        assert not deliver_execution_wake(
            queue, wake, controls.INCARNATION,
            refused if initial == "known-not-sent" else lambda: False,
        )
        retry = controls._finish(queue, wake, delivered=False)
        if initial == "unqualified":
            assert retry.execution_send_state is None
            controls._qualify(queue, task, monkeypatch)
        wake = controls._claim(queue, retry, now=retry.not_before + .01)
        assert wake and wake.attempts == 2
    result = deliver_execution_wake(
        queue, wake, controls.INCARNATION, (lambda: True) if accepted else refused,
    )
    assert result is accepted
    settled = controls._finish(queue, wake, delivered=result, max_attempts=1)
    assert settled.execution_send_state == ("sent" if accepted else "not_sent")
    restarted = TaskQueue(queue.db_path)
    with restarted._connect() as conn:
        conn.execute("BEGIN")
        original = restarted._fetch(conn, task.id)
        assert original is not None
        assert controls._row(restarted, wake)["first_qualified_attempt"] == (
            2 if initial == "unqualified" else 1
        )
        reservation = {"execution_machine": "host", "worktree": "worktree", "task_id": task.id}
        if initial == "unqualified":
            with pytest.raises(TaskError, match="unresolved driver wake"):
                require_no_outstanding_driver_work(
                    restarted, conn, original, reservation, "session-a",
                )
        else:
            require_no_outstanding_driver_work(restarted, conn, original, reservation, "session-a")


@pytest.mark.parametrize("attempts,valid", [(1, True), (2, True), (2, False)])
def test_v2_upgrade_never_infers_first_attempt_from_later_receipt(
    cohort: controls.Cohort, attempts: int, valid: bool,
) -> None:
    queue, pending, _ = cohort
    wake = controls._claim(queue, pending)
    assert wake and wake.delivery_token
    assert deliver_execution_wake(queue, wake, controls.INCARNATION, lambda: True)
    controls._finish(queue, wake, delivered=True)
    with queue._connect() as conn:
        for table in TABLES:
            for suffix in ("task", "target"):
                conn.execute(f"DROP INDEX idx_{table}_driver_{suffix}_v3")
                conn.execute(f"CREATE INDEX idx_{table}_driver_{suffix}_v2 ON {table}(task_id)"
                             " WHERE execution_send_state IN ('pending','unknown')")
            conn.execute(f"DROP TRIGGER {table}_first_qualified_attempt")
            conn.execute(f"ALTER TABLE {table} DROP COLUMN first_qualified_attempt")
        # A legacy retained row has only the last send identity, not its first ordinal.
        original = json.loads(controls._row(queue, wake)["execution_send"])
        if not valid:
            original["wake_id"] = "different"
        conn.execute(
            f"UPDATE {wake_table(wake)} SET attempts=?, execution_send=? WHERE id=?",  # noqa: S608 -- fixed outbox
            (attempts, json.dumps(original), wake.id),
        )
    restarted = TaskQueue(queue.db_path)
    assert controls._row(restarted, wake)["first_qualified_attempt"] == (
        1 if attempts == 1 and valid else None
    )
    with restarted._connect() as conn:
        conn.execute("BEGIN")
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        for table in TABLES:
            for suffix in ("task", "target"):
                assert f"idx_{table}_driver_{suffix}_v3" in indexes
                assert f"idx_{table}_driver_{suffix}_v2" not in indexes
        task = restarted._fetch(conn, wake.task_id)
        assert task is not None
        reservation = {"execution_machine": "host", "worktree": "worktree", "task_id": task.id}
        if attempts > 1:
            with pytest.raises(TaskError, match="unresolved driver wake"):
                require_no_outstanding_driver_work(restarted, conn, task, reservation, "session-a")
        else:
            require_no_outstanding_driver_work(restarted, conn, task, reservation, "session-a")


cohort = controls.cohort
