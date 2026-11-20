"""Queue-only reconciliation eligibility, shared by diagnostics and settlement."""

from __future__ import annotations

import json
from typing import Any

from .ordinary_agent_facts import identity
from .queue_records import TaskError

OUTBOXES = ("wake_outbox", "run_waiter_wakes")
HISTORY_LIMIT = 100


def retained_wake(
    captured: dict[str, Any], outbox: str, wake_id: str, *, settlement: bool = False,
) -> dict[str, Any]:
    if outbox not in OUTBOXES:
        raise TaskError("unknown wake outbox")
    rows = [row for row in captured[outbox] if row["id"] == wake_id]
    if len(rows) != 1:
        raise TaskError("wake is unavailable in this task's bounded retained history")
    row: dict[str, Any] = rows[0]
    try:
        send = json.loads(row["execution_send"])
    except (TypeError, ValueError) as exc:
        raise TaskError(
            "wake has no qualified original send identity; legacy outcome remains unknown"
        ) from exc
    if not isinstance(send, dict) or (
        send.get("outbox") != outbox or send.get("wake_id") != wake_id
        or type(send.get("no_resume", False)) is not bool
        or send.get("owner_session_id") != row["owner_session_id"]
        or type(send.get("task_generation")) is not int
        or send.get("task_generation") != (
            row["generation"] if outbox == "wake_outbox" else row["task_generation"]
        )
        or not isinstance(send.get("delivery_token"), str) or not send["delivery_token"]
        or (row["delivery_token"] is not None and row["delivery_token"] != send["delivery_token"])
        or (row["execution_scope_key"] is None and not (
            outbox == "wake_outbox"
            and isinstance(send.get("reservation_key"), str) and send["reservation_key"]
            and (
                (send.get("protocol") == "pool_recovery_wake/v1"
                and type(row.get("pool_recovery_revision")) is int
                and send.get("recovery_revision") == row["pool_recovery_revision"])
                or (send.get("protocol") == "task_relevance_closeout/v1"
                and row.get("pool_recovery_revision") is None
                and type(send.get("closeout_steer_id")) is int
                and send["closeout_steer_id"] > 0)
            )
        ))
    ):
        raise TaskError("wake original send identity is incomplete or conflicting")
    states = (
        {"pending", "unknown", "sent", "not_sent"}
        if settlement else {"pending", "unknown", "sent"}
    )
    if row["execution_send_state"] not in states:
        raise TaskError("wake does not retain a reconcilable send outcome")
    if row["execution_send_state"] == "not_sent":
        try:
            detail = json.loads(row["execution_send_detail"])
        except (TypeError, ValueError) as exc:
            raise TaskError("not-sent wake lacks retained fenced-settlement evidence") from exc
        if not isinstance(detail, dict) or (
            detail.get("source") != "bridge_native_settlement/v1"
            or not isinstance(detail.get("receipt"), dict)
            or detail["receipt"].get("admission") != "not_admitted"
        ):
            raise TaskError("not-sent wake lacks retained fenced-settlement evidence")
    return {**row, "no_resume": send.get("no_resume", False)}


def reconciliation_plan(
    captured: dict[str, Any], outbox: str, wake_id: str, *, settlement: bool = False,
) -> tuple[dict[str, Any], tuple[str, str, str | None]]:
    """Resolve the original ordinary adapter without transport or lifecycle effects."""
    from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle

    if any(len(rows) > HISTORY_LIMIT for key, rows in captured.items() if key != "task"):
        raise TaskError("retained history exceeds the bounded reconciliation view")
    wake = retained_wake(captured, outbox, wake_id, settlement=settlement)
    if settlement and wake["execution_send_state"] == "pending":
        raise TaskError(
            "native settlement refuses an in-flight send; await its original transport outcome"
        )
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
        raise TaskError(
            "reconciliation requires one supported original ordinary native Bridge target"
        )
    target = targets.pop()
    if wake["execution_send_state"] == "not_sent":
        from .bridge_command_delivery import steering_prompt
        from .bridge_native_settlement import valid_settlement
        detail = json.loads(wake["execution_send_detail"])
        prompt = steering_prompt(captured["task"]["id"], wake["message"])
        if not valid_settlement(
            detail["receipt"], target[0], target[1], prompt, idempotency_key=wake_id,
            no_resume=wake["no_resume"],
        ):
            raise TaskError(
                "not-sent wake retained settlement conflicts with its original request"
            )
    return wake, target
