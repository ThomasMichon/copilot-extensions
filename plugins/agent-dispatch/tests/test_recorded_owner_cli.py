"""Verify exact public CLI identity on the recorded sender and the recovery reader."""
from pathlib import Path
import subprocess
from typing import Any

import pytest

from agent_dispatch.coordinator_execution_wakes import deliver_recorded_owner_wake
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import TaskError
from agent_dispatch.task_wake_recovery import reconcile_wake
from agent_dispatch.wake_recovery_records import WakeRecoveryRecords
from tests.test_execution_wake_sends import _qualify
from tests.test_native_owner_wake import hosted as hosted, NATIVE, BRIDGE, OWNER


def test_unknown_send_uses_only_exact_no_resume_receipt_after_queue_loss(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
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
    calls = []

    def unavailable(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs["input"]))
        return subprocess.CompletedProcess(argv, 70, "", "")

    monkeypatch.setattr(subprocess, "run", unavailable)
    records = WakeRecoveryRecords.create(tmp_path / "records")
    assert not deliver_recorded_owner_wake(queue, wake, "application-a", records)
    with pytest.raises(TaskError, match="remains unknown"):
        reconcile_wake(queue, task_id, outbox="wake_outbox", wake_id=wake.id,
                       expected_revision=queue.task_diagnostics(task_id)["revision"],
                       actor="operator", reason="inspect the original receipt")
    Path(queue.db_path).rename(tmp_path / "withheld")
    with pytest.raises(TaskError, match="remains unknown"):
        WakeRecoveryRecords(records.path).observe_original(task_id, wake.id, expected_target=(BRIDGE, NATIVE, "host-a"))
    assert len(calls) == 3
    assert "--receipt-only" not in calls[0][0]
    assert all("--receipt-only" in argv for argv, _ in calls[1:])
    for argv, prompt in calls:
        assert "--no-resume" in argv and prompt == "original"
        assert argv[argv.index("--expected-native-session-id") + 1] == NATIVE
        assert argv[argv.index("--idempotency-key") + 1] == wake.id
    assert records.inspect(task_id, wake.id)["outcome"] == "unknown"
