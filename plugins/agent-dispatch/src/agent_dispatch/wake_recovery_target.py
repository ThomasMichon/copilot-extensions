"""Queue-only reconciliation eligibility, shared by diagnostics and settlement."""

import json

from .ordinary_agent_facts import identity
from .queue_records import TaskError

OUTBOXES = ("wake_outbox", "run_waiter_wakes")
HISTORY_LIMIT = 100


def retained_wake(captured: dict, outbox: str, wake_id: str) -> dict:
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


def reconciliation_plan(
    captured: dict, outbox: str, wake_id: str,
) -> tuple[dict, tuple[str, str, str | None]]:
    """Resolve the original ordinary adapter without transport or lifecycle effects."""
    from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle

    if any(len(rows) > HISTORY_LIMIT for key, rows in captured.items() if key != "task"):
        raise TaskError("retained history exceeds the bounded reconciliation view")
    wake = retained_wake(captured, outbox, wake_id)
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
        raise TaskError("reconciliation requires one supported original ordinary native Bridge target")
    return wake, targets.pop()
