"""Reconcile a retained wake using read-only evidence from its original transport."""

from __future__ import annotations

import json
from collections.abc import Callable

from .bridge_command_delivery import native_prompt_receipt, steering_prompt
from .queue_records import TaskError
from .queue_task_recovery import _captured, _revision
from .wake_recovery_target import reconciliation_plan, retained_wake


def reconcile_wake(
    queue, task_id: str, *, outbox: str, wake_id: str, expected_revision: str,
    actor: str, reason: str, observe: Callable[..., dict] | None = None,
) -> dict:
    """Observe outside the queue lock, then settle only the unchanged original send."""
    if any(not isinstance(value, str) or not value.strip() for value in (
        wake_id, expected_revision, actor, reason,
    )):
        raise TaskError("wake reconciliation requires revision, identity, actor and reason")
    with queue._connect() as conn:
        conn.execute("BEGIN")
        captured = _captured(queue, conn, task_id)
        if _revision(captured) != expected_revision:
            raise TaskError("task or retained execution changed; refresh diagnostics")
        wake, target = reconciliation_plan(captured, outbox, wake_id)
    # Neither a receipt miss nor an unavailable provider can call a send path.
    receipt = (observe or native_prompt_receipt)(
        target[0], target[1], steering_prompt(task_id, wake["message"]),
        idempotency_key=wake_id, machine=target[2],
    )
    if not isinstance(receipt, dict) or (
        type(receipt.get("version")) is not int or receipt["version"] != 1
        or receipt.get("session_id") != target[0]
        or receipt.get("native_session_id") != target[1]
        or receipt.get("invocation_key") != wake_id
        or receipt.get("admission") != "accepted" or receipt.get("queued") is not False
        or type(receipt.get("turn_index")) is not int or receipt["turn_index"] < 0
    ):
        raise TaskError("transport observation does not prove original wake acceptance")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_snapshot = _captured(queue, conn, task_id)
        if _revision(current_snapshot) != expected_revision:
            raise TaskError("task or original send changed during transport observation")
        current = retained_wake(current_snapshot, outbox, wake_id)
        replayed = current["execution_send_state"] == "sent"
        if not replayed:
            detail = json.dumps({
                "source": "bridge_native_receipt/v1", "receipt": receipt,
            }, sort_keys=True, separators=(",", ":"))
            conn.execute(
                f"UPDATE {outbox} SET execution_send_state='sent', execution_send_detail=?"
                " WHERE id=? AND execution_send=?",  # noqa: S608
                (detail, wake_id, wake["execution_send"]),
            )
            task = queue._fetch(conn, task_id)
            queue._audit(
                conn, task_id, ts=queue._now(None), from_status=task.status,
                to_status=task.status, worker=actor,
                note=f"wake acceptance reconciled: {wake_id}: {reason}",
            )
        conn.commit()
    return {"task_id": task_id, "outbox": outbox, "wake_id": wake_id,
            "outcome": "sent", "replayed": replayed, "receipt": receipt}
