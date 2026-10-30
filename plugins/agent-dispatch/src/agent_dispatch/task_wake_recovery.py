"""Reconcile a retained wake using read-only evidence from its original transport."""

from __future__ import annotations

import json
from collections.abc import Callable

from .bridge_command_delivery import native_prompt_receipt, steering_prompt
from .ordinary_agent_facts import identity
from .queue_records import TaskError
from .queue_task_recovery import _captured, _revision
from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle

OUTBOXES = ("wake_outbox", "run_waiter_wakes")


def _wake(captured: dict, outbox: str, wake_id: str) -> dict:
    if outbox not in OUTBOXES:
        raise TaskError("unknown wake outbox")
    rows = [row for row in captured[outbox] if row["id"] == wake_id]
    if len(rows) != 1:
        raise TaskError("wake is unavailable in this task's bounded retained history")
    row = rows[0]
    try:
        send = json.loads(row["execution_send"])
    except (TypeError, ValueError) as exc:
        raise TaskError("wake has no qualified original send identity; legacy outcome remains unknown") from exc
    if not isinstance(send, dict) or (
        send.get("outbox") != outbox or send.get("wake_id") != wake_id
        or send.get("owner_session_id") != row["owner_session_id"]
        or type(send.get("task_generation")) is not int
        or send.get("task_generation") != (
            row["generation"] if outbox == "wake_outbox" else row["task_generation"]
        )
        or not isinstance(send.get("delivery_token"), str) or not send["delivery_token"]
        or row["delivery_token"] is not None and row["delivery_token"] != send["delivery_token"]
        or row["execution_scope_key"] is None
    ):
        raise TaskError("wake original send identity is incomplete or conflicting")
    if row["execution_send_state"] not in {"pending", "unknown", "sent"}:
        raise TaskError("wake does not retain a reconcilable send outcome")
    return row


def _native_target(captured: dict, wake: dict) -> tuple[str, str, str | None]:
    targets = set()
    for row in captured["reservations"]:
        if row["driver"] != "ordinary-bridge":
            continue
        try:
            info = json.loads(row["agent_info"] or "{}")
            if not isinstance(info, dict):
                raise TaskError("retained native identity is not an object")
            facts = identity(info)
        except (TaskError, TypeError, ValueError):
            continue
        if facts.native != wake["owner_session_id"]:
            continue
        fleet = _parse_fleet_body_handle(row["session_handle"])
        local = _parse_local_body_handle(row["session_handle"])
        if (fleet is None and local is None) or facts.bridge != (fleet[1] if fleet else local):
            raise TaskError("original wake transport and retained Bridge identity conflict")
        if facts.worktree != row["worktree"] or facts.project != row["allocation_project"]:
            raise TaskError("original wake transport and retained workspace conflict")
        host = fleet[0] if fleet else row["creating_host"]
        if not isinstance(wake["owner"], str):
            raise TaskError("original wake owner is unavailable; send remains unknown")
        owner_host, separator, worktree = wake["owner"].partition("/")
        if separator:
            from .bridge_remote import normalize_host
            if facts.worktree != worktree or (
                host is not None and normalize_host(owner_host) != normalize_host(host)
            ):
                raise TaskError("original wake owner and retained execution host conflict")
            host = owner_host
        targets.add((facts.bridge, facts.native, host))
    if len(targets) != 1:
        raise TaskError("original native transport is unavailable or ambiguous; send remains unknown")
    return targets.pop()


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
        wake = _wake(captured, outbox, wake_id)
        target = _native_target(captured, wake)
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
        current = _wake(current_snapshot, outbox, wake_id)
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
