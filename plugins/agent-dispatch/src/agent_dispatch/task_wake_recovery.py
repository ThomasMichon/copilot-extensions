"""Original-wake observation and explicitly selected provider settlement."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .bridge_command_delivery import native_prompt_receipt, steering_prompt
from .bridge_native_settlement import native_prompt_settlement, valid_settlement
from .queue_records import TaskError
from .queue_task_recovery import _captured, _revision, reconciliation_revision
from .wake_recovery_target import reconciliation_plan, retained_wake


def reconcile_wake(
    queue: Any, task_id: str, *, outbox: str, wake_id: str, expected_revision: str,
    actor: str, reason: str, observe: Callable[..., dict[str, Any]] | None = None,
    settle_native_send: bool = False,
) -> dict[str, Any]:
    """Reconcile the unchanged original send; settlement requires explicit selection."""
    if type(settle_native_send) is not bool:
        raise TaskError("native settlement selection must be boolean")
    if any(not isinstance(value, str) or not value.strip() for value in (
        wake_id, expected_revision, actor, reason,
    )):
        raise TaskError("wake reconciliation requires revision, identity, actor and reason")
    with queue._connect() as conn:
        conn.execute("BEGIN")
        captured = _captured(queue, conn, task_id)
        revision_for = _revision
        if revision_for(captured) != expected_revision:
            revision_for = reconciliation_revision
        if revision_for(captured) != expected_revision:
            raise TaskError("task or retained execution changed; refresh diagnostics")
        wake, target = reconciliation_plan(
            captured, outbox, wake_id, settlement=settle_native_send,
        )
    # Neither a receipt miss nor an unavailable provider can call a send path.
    prompt = steering_prompt(task_id, wake["message"])
    provider = native_prompt_settlement if settle_native_send else native_prompt_receipt
    receipt = (observe or provider)(
        target[0], target[1], prompt,
        idempotency_key=wake_id, machine=target[2],
        **({"no_resume": True} if wake["no_resume"] else {}),
    )
    if settle_native_send:
        valid = valid_settlement(
            receipt, target[0], target[1], prompt, idempotency_key=wake_id,
            no_resume=wake["no_resume"],
        )
    else:
        valid = isinstance(receipt, dict) and not (
            type(receipt.get("version")) is not int or receipt["version"] != 1
            or receipt.get("session_id") != target[0]
            or receipt.get("native_session_id") != target[1]
            or receipt.get("invocation_key") != wake_id
            or receipt.get("admission") != "accepted" or receipt.get("queued") is not False
            or type(receipt.get("turn_index")) is not int or receipt["turn_index"] < 0
        )
    if not valid:
        raise TaskError("transport observation does not prove original wake acceptance")
    outcome = "not_sent" if receipt["admission"] == "not_admitted" else "sent"
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_snapshot = _captured(queue, conn, task_id)
        if revision_for(current_snapshot) != expected_revision:
            raise TaskError("task or original send changed during transport observation")
        current = retained_wake(current_snapshot, outbox, wake_id, settlement=settle_native_send)
        if (
            current["execution_send_state"] in {"sent", "not_sent"}
            and current["execution_send_state"] != outcome
        ):
            raise TaskError("transport settlement conflicts with the retained original outcome")
        replayed = current["execution_send_state"] == outcome
        ts = queue._now(None)
        if outcome == "not_sent":
            # Terminal at publication, before either outbox's lease recovery can reclaim it.
            conn.execute(
                f"UPDATE {outbox} SET status='failed' WHERE id=?", (wake_id,),  # noqa: S608
            )
            if outbox == "wake_outbox":
                conn.execute(
                    "UPDATE tasks SET wake_status='failed' WHERE id=? AND wake_operation_id=?",
                    (task_id, wake_id),
                )
        if (
            outcome == "sent" and outbox == "wake_outbox"
            and current.get("pool_recovery_revision") is not None
        ):
            conn.execute(
                "UPDATE wake_outbox SET delivered_at=COALESCE(delivered_at, ?) WHERE id=?",
                (ts, wake_id),
            )
        if not replayed:
            detail = json.dumps({
                "source": (
                    "bridge_native_settlement/v1"
                    if settle_native_send else "bridge_native_receipt/v1"
                ),
                "receipt": receipt,
            }, sort_keys=True, separators=(",", ":"))
            conn.execute(
                f"UPDATE {outbox} SET execution_send_state=?, execution_send_detail=?"  # noqa: S608
                " WHERE id=? AND execution_send=?",
                (outcome, detail, wake_id, wake["execution_send"]),
            )
            task = queue._fetch(conn, task_id)
            queue._audit(
                conn, task_id, ts=ts, from_status=task.status,
                to_status=task.status, worker=actor,
                note=f"wake {outcome} reconciled: {wake_id}: {reason}",
            )
        conn.commit()
    return {"task_id": task_id, "outbox": outbox, "wake_id": wake_id,
            "outcome": outcome, "replayed": replayed, "receipt": receipt}
