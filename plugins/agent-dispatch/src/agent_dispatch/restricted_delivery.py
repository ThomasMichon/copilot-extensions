"""Control-owned, nonsecret startup receipts on the existing spawn reservation."""

from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import Depends, HTTPException

from .coordinator_auth import _make_control_auth
from .queue_records import SpawnReservation, SpawnState, Status, TaskError

DRIVER = "restricted-bridge"
_BASE = {"kind", "worker_id", "task_generation", "coordinator_epoch", "configuration", "charter"}
_OPTIONAL = {"binding_id", "agent", "session_id", "native_session_id", "send_attempted", "delivered"}


def validate_receipt(info: dict) -> None:
    if (
        not isinstance(info, dict) or not _BASE <= info.keys()
        or info.keys() - _BASE - _OPTIONAL or info["kind"] != DRIVER
        or not isinstance(info["worker_id"], str) or not info["worker_id"]
        or any(type(info[key]) is not int or not 0 <= info[key] < 2**63
               for key in ("task_generation", "coordinator_epoch"))
        or not isinstance(info["configuration"], dict)
        or set(info["configuration"]) != {"name", "instance_id", "coordinator_url"}
        or any(not isinstance(value, str) or not value for value in info["configuration"].values())
        or info["charter"] is not None
        and (not isinstance(info["charter"], str) or not info["charter"])
    ):
        raise TaskError("invalid restricted delivery receipt")
    for key in ("binding_id", "agent", "session_id", "native_session_id"):
        if key in info and (not isinstance(info[key], str) or not info[key]):
            raise TaskError("invalid restricted delivery identity")
    for key in ("send_attempted", "delivered"):
        if key in info and info[key] is not True:
            raise TaskError("restricted delivery markers must be positive")
    if (
        ("binding_id" in info) != ("agent" in info)
        or ("session_id" in info) != ("native_session_id" in info)
        or "session_id" in info and "binding_id" not in info
        or "send_attempted" in info and "session_id" not in info
        or "delivered" in info and "send_attempted" not in info
    ):
        raise TaskError("restricted delivery stages are out of order")


def record(queue, key: str, info: dict) -> SpawnReservation:
    validate_receipt(info)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        task = queue._fetch(conn, row["task_id"]) if row else None
        previous = json.loads(row["agent_info"]) if row and row["agent_info"] else {}
        terminal_ack = (
            task is not None and task.status in Status.CONCLUDED
            and info.get("delivered") is True and previous.get("send_attempted") is True
            and task.owner in {None, info["worker_id"]}
            and task.owner_session_id in {None, info.get("native_session_id")}
        )
        if (
            row is None or row["driver"] != DRIVER
            or row["state"] not in {SpawnState.RESERVING, SpawnState.SPAWNED}
            or row["release_requested"] or row["conversation_retired"]
            or task is None or task.generation != info["task_generation"]
            or not terminal_ack and (
                task.status not in Status.HELD or task.hold_reason
                or task.owner != info["worker_id"]
            )
            or info["worker_id"] != f"dispatch-{key}"
            or row["worktree"] or row["allocation_project"]
        ):
            raise TaskError("restricted delivery reservation or task incarnation changed")
        from .queue_spawn_cooling import require_no_cooling
        require_no_cooling(row)
        if any(info.get(field) != value for field, value in previous.items()):
            raise TaskError("restricted delivery identity cannot be replaced")
        if info.get("delivered"):
            if previous.get("send_attempted") is not True:
                raise TaskError("restricted delivery acknowledgement requires its retained send intent")
        elif info.get("send_attempted") and previous.get("send_attempted"):
            raise TaskError("restricted delivery send intent was already acquired")
        if "native_session_id" in info and task.owner_session_id not in {None, info["native_session_id"]}:
            raise TaskError("restricted delivery native owner changed")
        if "send_attempted" in info and not terminal_ack and task.owner_session_id != info["native_session_id"]:
            raise TaskError("restricted delivery requires the bound native owner")
        handle = f"local-body:{info['session_id']}" if "session_id" in info else None
        if row["session_handle"] and row["session_handle"] != handle:
            raise TaskError("restricted delivery bridge handle changed")
        conn.execute(
            "UPDATE spawn_reservations SET agent_info=?,session_handle=?,updated_at=?,"
            "state=CASE WHEN ? THEN ? ELSE state END WHERE key=?",
            (json.dumps(info, sort_keys=True), handle, queue._now(None),
             info.get("delivered") is True, SpawnState.SPAWNED, key),
        )
        result = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        conn.commit()
    return SpawnReservation._from_row(result)


def validate_start(row, task, native_id: str | None) -> None:
    if not row["agent_info"]:
        raise TaskError("restricted start requires its retained delivery receipt")
    info = json.loads(row["agent_info"])
    validate_receipt(info)
    if (
        row["acquisition_marked"] or row["delivery_id"]
        or row["reservation_state"] not in {"reserving", "spawned"}
        or row["release_requested"] or row["conversation_retired"]
        or info.get("send_attempted") is not True
        or native_id != info.get("native_session_id")
        or task.owner_session_id != native_id
        or task.owner != info["worker_id"] or task.generation != info["task_generation"]
    ):
        raise TaskError("restricted start owner or delivery incarnation changed")


def wake_identity(reservation: SpawnReservation, task) -> tuple[str, str]:
    """Keep restricted receipts distinct from ordinary worktree creation facts."""
    info = reservation.agent_info
    if not isinstance(info, dict):
        raise TaskError("restricted wake requires retained delivery facts")
    validate_receipt(info)
    if (
        info.get("delivered") is not True
        or info["worker_id"] != task.owner
        or info["worker_id"] != f"dispatch-{reservation.key}"
        or info["task_generation"] != task.generation
        or info.get("native_session_id") != task.owner_session_id
        or reservation.worktree is not None or reservation.allocation_project is not None
    ):
        raise TaskError("restricted wake owner or delivery incarnation changed")
    return info["session_id"], info["native_session_id"]


def register_routes(app, queue, bus, control_token: str | None, *, authenticated_ingress: bool) -> None:
    auth = _make_control_auth(
        control_token, lambda kind, detail: bus.publish({"type": kind, **detail}),
        operation="restricted_delivery", code_prefix="restricted_delivery",
        rejection_event="spawn.restricted_delivery_rejected",
        unavailable_message="restricted delivery requires configured control authority",
        forbidden_message="restricted delivery requires control authority",
    )

    @app.post("/spawn-reservations/{key}/restricted-delivery", dependencies=[Depends(auth)])
    def receipt(key: str, body: dict) -> dict:
        if not authenticated_ingress:
            raise HTTPException(503, detail="restricted delivery requires authenticated coordinator ingress")
        try:
            result = asdict(record(queue, key, body))
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        bus.publish({"type": "spawn.restricted_delivery", "reservation": result})
        return result
