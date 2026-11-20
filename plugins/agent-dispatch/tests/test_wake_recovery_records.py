import json
import sqlite3
from contextlib import closing
from pathlib import Path
import subprocess

import pytest

from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.bridge_command_delivery import NativePromptNotSent
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from agent_dispatch.wake_recovery_records import WakeRecoveryRecords
from tests.test_execution_wake_sends import _qualify
from tests.test_native_owner_wake import hosted as hosted, NATIVE, BRIDGE, OWNER


@pytest.mark.parametrize("ack", [True, False])
def test_observe_original_after_queue_loss(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ack: bool,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True, wake_requested=False)
    queue.submit_steer(task_id, fields={"decision": "accept"}, idempotency_key="reply",
                       wake_requested=True, wake_message="original")
    task = queue.get(task_id)
    assert task is not None
    _qualify(queue, task, monkeypatch)
    wake = queue.claim_due_wake()
    assert wake is not None
    expected = queue.steer_receipt(task_id, "reply")
    assert expected is not None
    sent = []
    records = WakeRecoveryRecords.create(tmp_path / "records")

    def deliver() -> bool:
        assert records.inspect(task_id, wake.id)["outcome"] == "unknown"
        sent.append(wake.id)
        return ack

    assert deliver_execution_wake(queue, wake, "application-a", deliver, records=records) == ack
    Path(queue.db_path).rename(tmp_path / "lost.db")
    restored = WakeRecoveryRecords(records.path)
    report = restored.inspect(task_id, wake.id)
    assert (report["task_id"], report["owner"], report["owner_session_id"], report["generation"]) == (
        task_id, OWNER, NATIVE, task.generation,
    )
    assert report["lease_expires_at"] == task.lease_expires_at
    assert report["steer_acceptances"] == [expected["acceptance"]]
    outcome = "sent" if ack else "unknown"
    assert report["outcome"] == outcome
    calls = []
    provider = {"version": 1, "session_id": BRIDGE, "native_session_id": NATIVE,
                "invocation_key": wake.id, "turn_index": 3, "queued": False, "admission": "accepted"}

    def observe(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "--receipt-only" in argv and "--no-wait" in argv and kwargs["input"] == "original"
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(provider))

    monkeypatch.setattr(subprocess, "run", observe)
    target = (BRIDGE, NATIVE, "host-a")
    provider["admission"] = "missing"
    with pytest.raises(TaskError, match="does not prove"):
        restored.observe_original(task_id, wake.id, expected_target=target)
    assert restored.inspect(task_id, wake.id)["outcome"] == outcome
    provider["admission"] = "accepted"
    restored.observe_original(task_id, wake.id, expected_target=target)
    assert restored.inspect(task_id, wake.id)["receipt"] == provider
    assert restored.inspect(task_id, wake.id)["outcome"] == "sent"
    assert len(calls) == 2 and sent == [wake.id] and not Path(queue.db_path).exists()


def test_recording_failure_and_typed_retry_keep_real_admission_eligible(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id = hosted
    queue.resume(task_id, OWNER, wake_requested=True, wake_message="original")
    task = queue.get(task_id)
    assert task is not None
    _qualify(queue, task, monkeypatch)
    wake = queue.claim_due_wake()
    assert wake is not None and wake.delivery_token is not None
    records = WakeRecoveryRecords.create(tmp_path / "records")
    with closing(sqlite3.connect(records.path)) as db:
        db.execute("CREATE TRIGGER refused BEFORE INSERT ON ordinary_wake_v1 BEGIN SELECT RAISE(FAIL,'full'); END")
    with pytest.raises(sqlite3.IntegrityError, match="full"):
        deliver_execution_wake(queue, wake, "application-a", lambda: pytest.fail("no send"), records=records)
    assert queue.list_wakes(task_id)[0].execution_send_state is None
    with closing(sqlite3.connect(records.path)) as db:
        db.execute("DROP TRIGGER refused")
    with queue._connect() as conn:
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT acceptance FROM task_steer "
                            "WHERE task_id=? AND wake_operation_id=?", (task_id, wake.id)).fetchall()
        assert any("idx_task_steer_wake" in row[3] for row in plan)
        for i in range(101):
            conn.execute("INSERT INTO task_events(task_id,ts,note,task_relationship) VALUES(?,?,?,?)",
                         (task_id, i, "historical relationship", "{}"))

    def refused() -> bool:
        raise NativePromptNotSent(BRIDGE, NATIVE, wake.id)

    assert not deliver_execution_wake(queue, wake, "application-a", refused, records=records)
    retry = queue.finish_wake(wake.id, wake.delivery_token, delivered=False, retry_base=0.01)
    fresh = queue.claim_due_wake(now=retry.not_before + 0.001)
    assert fresh is not None and fresh.delivery_token != wake.delivery_token
    assert deliver_execution_wake(queue, fresh, "application-a", lambda: True, records=records)
    with closing(sqlite3.connect(records.path)) as db:
        assert db.execute("SELECT COUNT(DISTINCT admission) FROM ordinary_wake_v1").fetchone() == (2,)
