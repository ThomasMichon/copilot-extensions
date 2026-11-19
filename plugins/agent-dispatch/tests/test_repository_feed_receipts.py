"""Actual receipt expiry survives reopen and fences the original native arm."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from agent_dispatch.queue_records import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_repository_feed_registration import prepared, reconcile

pytestmark = pytest.mark.contract("agent_dispatch.repository_feed.receipts")


@pytest.mark.parametrize("offset,active", [(-.25, True), (0, False), (.25, False)])
def test_actual_receipt_expiry_not_longer_native_deadline_controls_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offset: float, active: bool,
) -> None:
    monkeypatch.setattr("time.time", lambda: 100.5)
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    result = reconcile(registrar, task_id, spec)
    queue = TaskQueue(tmp_path / "tasks.db")
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    expiry = waiter["replay_registration_expires_at"]
    assert expiry == 400 < spec["feed"]["deadline_at"]
    assert waiter["replay_registration_id"] == result["registration_id"]
    def arm() -> dict[str, Any] | None:
        return queue.arm_run_waiter(
            task_id, generation=waiter["generation"], kind="delegated",
            delegation={**spec, "registration_id": result["registration_id"]},
            now=expiry + offset,
        )

    if active:
        armed = arm()
        assert armed is not None and armed["state"] == "active"
        assert armed["replay_registration_expires_at"] == expiry
    else:
        with pytest.raises(TaskError, match="receipt expired"):
            arm()
        assert queue.find_delegated_preparation(task_id, spec["preparation_id"]) == waiter
    assert client.calls.count("subscribe") == 1
    assert not queue.list_run_waiter_wakes(task_id)


def test_actual_receipt_expiry_is_rechecked_before_arm_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.5]
    monkeypatch.setattr("time.time", lambda: clock[0])
    queue, task_id, spec, _, registrar = prepared(tmp_path)
    result = reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    original_audit = queue._audit

    def expiry_crossed(*args: Any, **kwargs: Any) -> None:
        original_audit(*args, **kwargs)
        clock[0] = waiter["replay_registration_expires_at"]

    monkeypatch.setattr(queue, "_audit", expiry_crossed)
    with pytest.raises(TaskError, match="receipt expired"):
        queue.arm_run_waiter(
            task_id, generation=waiter["generation"], kind="delegated",
            delegation={**spec, "registration_id": result["registration_id"]},
        )
    reopened = TaskQueue(tmp_path / "tasks.db")
    assert reopened.find_delegated_preparation(task_id, spec["preparation_id"]) == waiter
    assert not reopened.list_run_waiter_wakes(task_id)


def test_original_receipt_expiry_cannot_be_changed(tmp_path: Path) -> None:
    queue, task_id, spec, _, registrar = prepared(tmp_path)
    result = reconcile(registrar, task_id, spec)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    with pytest.raises(TaskError, match="expiry changed"):
        queue.replay_waiter_progress(
            waiter, registration_id=result["registration_id"],
            registration_expires_at=waiter["replay_registration_expires_at"] - 1,
        )
    assert queue.find_delegated_preparation(task_id, spec["preparation_id"]) == waiter


def test_historical_unknown_expiry_is_not_filled_from_original_deadline(tmp_path: Path) -> None:
    queue, task_id, spec, _, _ = prepared(tmp_path)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    queue.replay_waiter_progress(waiter, attempt=True)
    queue.replay_waiter_progress(waiter, registration_id="historical-receipt")
    with queue._connect() as conn:
        conn.execute("ALTER TABLE run_waiters DROP COLUMN replay_registration_expires_at")
    reopened = TaskQueue(tmp_path / "tasks.db")
    historical = reopened.find_delegated_preparation(task_id, spec["preparation_id"])
    assert historical is not None and historical["replay_registration_expires_at"] is None
    with pytest.raises(TaskError, match="receipt expiry unavailable"):
        reopened.arm_run_waiter(
            task_id, generation=historical["generation"], kind="delegated",
            delegation={**spec, "registration_id": "historical-receipt"},
        )
    assert reopened.find_delegated_preparation(task_id, spec["preparation_id"]) == historical
    assert not reopened.list_run_waiter_wakes(task_id)


def test_lost_committed_reply_recovers_actual_expiry_not_a_new_request(tmp_path: Path) -> None:
    queue, task_id, spec, client, registrar = prepared(tmp_path)
    client.lost_reply = True
    with pytest.raises(TimeoutError):
        reconcile(registrar, task_id, spec)
    uncertain = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert uncertain is not None and uncertain["replay_registration_expires_at"] is None
    registrar.queue = TaskQueue(tmp_path / "tasks.db")
    result = reconcile(registrar, task_id, spec)
    recovered = registrar.queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert recovered is not None and recovered["replay_registration_expires_at"] is not None
    assert recovered["replay_registration_id"] == result["registration_id"]
    assert recovered["state"] == "preparing" and not recovered["replay_settled"]
    assert client.calls.count("subscribe") == 1 and client.calls[-1] == "inspect-request"
    assert not queue.list_run_waiter_wakes(task_id)


@pytest.mark.parametrize("expiry", [True, -1, 0, 2**53])
def test_invalid_receipt_expiry_does_not_change_native_progress(
    tmp_path: Path, expiry: object,
) -> None:
    queue, task_id, spec, _, _ = prepared(tmp_path)
    waiter = queue.find_delegated_preparation(task_id, spec["preparation_id"])
    assert waiter is not None
    with pytest.raises(TaskError, match="invalid original"):
        queue.replay_waiter_progress(
            waiter, registration_id="receipt", registration_expires_at=cast(Any, expiry),
        )
    assert queue.find_delegated_preparation(task_id, spec["preparation_id"]) == waiter
