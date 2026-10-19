"""Persist bridge-native start authority in the task owner's database."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from .queue import TaskError


def ensure(conn: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS acquisition_markers (reservation_key TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS acquisition_receipts ("
        "reservation_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
        "task_generation INTEGER NOT NULL, worktree_id TEXT NOT NULL, "
        "session_id TEXT NOT NULL, native_session_id TEXT NOT NULL, "
        "mode TEXT NOT NULL, delivery_id TEXT NOT NULL UNIQUE)"
    )


def bridge_evidence(delivery_id: str) -> dict[str, Any]:
    from .procutil import agent_bridge_launch_prefix, no_window_kwargs
    prefix = agent_bridge_launch_prefix()
    if not prefix:
        raise TaskError("bridge acquisition authority unavailable")
    result = subprocess.run(
        [*prefix, "acquisition-inspect", delivery_id], check=False,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    if result.returncode:
        raise TaskError("bridge acquisition authority refused inspection")
    try:
        evidence = json.loads(result.stdout)
    except ValueError as exc:
        raise TaskError("bridge acquisition evidence is malformed") from exc
    if not isinstance(evidence, dict):
        raise TaskError("bridge acquisition evidence is missing")
    return evidence


def verify_recovery_bridge() -> None:
    """Refuse authorization unless both bridge payload and daemon prove v31."""
    from .procutil import agent_bridge_launch_prefix, no_window_kwargs
    prefix = agent_bridge_launch_prefix()
    if not prefix:
        raise TaskError("recovery requires a configured bridge acquisition capability")
    result = subprocess.run(
        [*prefix, "acquisition-readiness"], check=False, capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise TaskError("recovery bridge readiness is unproven") from exc
    if (
        result.returncode or not isinstance(response, dict)
        or response.get("supported") is not True
        or type(response.get("required_protocol")) is not int
        or response["required_protocol"] < 31
    ):
        raise TaskError("recovery requires bridge payload and daemon acquisition protocol 31")


def bind(
    queue: Any, receipt: dict[str, Any], *, evidence: dict[str, Any],
) -> dict[str, Any]:
    required = {"reservation_key", "task_id", "task_generation", "worktree_id",
                "session_id", "native_session_id", "mode", "delivery_id"}
    if set(receipt) != required or any(
        not isinstance(receipt[field], str) or not receipt[field]
        for field in required - {"task_generation"}
    ) or type(receipt["task_generation"]) is not int or receipt["mode"] not in {"cli", "acp"}:
        raise TaskError("invalid acquisition receipt")
    actual = evidence.get("recorded_result")
    if (
        evidence.get("delivery_id") != receipt["delivery_id"]
        or evidence.get("stage") not in {"launched", "delivering", "delivered"}
        or evidence.get("state") not in {"pending", "completed"}
        or not isinstance(actual, dict)
        or any(actual.get(field) != receipt[field] for field in required - {"reservation_key"})
    ):
        raise TaskError("receipt does not match durable bridge acquisition authority")
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure(conn)
        task = queue._fetch(conn, receipt["task_id"])
        reservation = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (receipt["reservation_key"],),
        ).fetchone()
        if (
            task is None or task.generation != receipt["task_generation"]
            or task.hold_reason is not None
            or reservation is None or reservation["task_id"] != task.id
            or reservation["worktree"] != receipt["worktree_id"]
            or reservation["state"] not in {"reserving", "spawned"}
            or reservation["release_requested"]
        ):
            raise TaskError("acquisition receipt admission fence changed")
        previous = conn.execute(
            "SELECT * FROM acquisition_receipts WHERE reservation_key=?",
            (receipt["reservation_key"],),
        ).fetchone()
        if previous and dict(previous) != receipt:
            raise TaskError("acquisition receipt is already bound to another native identity")
        if not previous:
            conn.execute(
                "INSERT INTO acquisition_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(receipt[field] for field in (
                    "reservation_key", "task_id", "task_generation", "worktree_id",
                    "session_id", "native_session_id", "mode", "delivery_id",
                )),
            )
        conn.commit()
    return receipt


def start_authority(queue: Any, task_id: str) -> dict[str, Any] | None:
    with queue._connect() as conn:
        ensure(conn)
        row = conn.execute(
            "SELECT a.delivery_id,a.task_generation,s.agent_info FROM spawn_reservations s LEFT JOIN acquisition_receipts a "
            "ON s.key=a.reservation_key WHERE s.task_id=? "
            "ORDER BY s.attempt DESC LIMIT 1", (task_id,),
        ).fetchone()
        task = queue._fetch(conn, task_id)
        if row and row["agent_info"]:
            return None
        if row and row["delivery_id"] and task and task.generation > row["task_generation"] + 1:
            return None
    return bridge_evidence(row["delivery_id"]) if row and row["delivery_id"] else None


def validate_start(
    conn: Any, task: Any, native_id: str | None, authority: dict[str, Any] | None,
) -> None:
    ensure(conn)
    rows = conn.execute(
        "SELECT a.*,s.agent_info,s.driver,s.state AS reservation_state,s.release_requested,s.worktree AS reserved_worktree,"
        "s.conversation_retired,"
        "s.session_handle AS reserved_session_handle,s.allocation_project AS reserved_allocation_project,"
        "EXISTS(SELECT 1 FROM acquisition_markers m WHERE m.reservation_key=s.key) AS acquisition_marked FROM spawn_reservations s "
        "LEFT JOIN acquisition_receipts a ON s.key=a.reservation_key WHERE s.task_id=? "
        "ORDER BY s.attempt DESC LIMIT 1", (task.id,),
    ).fetchall()
    if rows and rows[0]["driver"] == "restricted-bridge":
        restricted = rows[0]
        empty_deferral = restricted["reservation_state"] == "deferred" and not any(
            restricted[field] for field in (
                "agent_info", "delivery_id", "acquisition_marked", "reserved_worktree",
                "reserved_session_handle", "reserved_allocation_project",
                "conversation_retired", "release_requested",
            )
        )
        if not empty_deferral:
            from .restricted_delivery import validate_start as validate_restricted_start

            validate_restricted_start(restricted, task, native_id)
            return
    if rows and rows[0]["agent_info"]:
        from .ordinary_agent_facts import identity
        ordinary = rows[0]
        info = json.loads(ordinary["agent_info"])
        actual = identity(info)
        if (
            ordinary["reservation_state"] not in {"reserving", "spawned"}
            or ordinary["release_requested"] or ordinary["conversation_retired"]
            or native_id != actual.native
            or (ordinary["native_session_id"] and ordinary["native_session_id"] != actual.native)
            or actual.worktree != ordinary["reserved_worktree"]
            or not info["task_generation"] <= task.generation <= info["task_generation"] + 1
            or ("/" in (task.owner or "") and task.owner.split("/", 1)[1] != ordinary["reserved_worktree"])
        ):
            raise TaskError("start owner does not match the ordinary created-agent reservation")
        return
    if not rows or not rows[0]["delivery_id"]:
        if (task.owner or "").startswith("acquired-") or (
            rows and rows[0]["acquisition_marked"]
        ):
            raise TaskError("acquired worker start requires a bridge acquisition receipt")
        return
    receipt = rows[0]
    if receipt["delivery_id"] and task.generation > receipt["task_generation"] + 1:
        return
    if receipt["reservation_state"] not in {"reserving", "spawned"} or receipt["release_requested"]:
        raise TaskError("acquisition reservation was withdrawn before start")
    current = authority or {}
    if (
        current.get("delivery_id") != receipt["delivery_id"]
        or
        current.get("stage") not in {"delivering", "delivered"}
        or current.get("delivery_admitted") is not True
        or current.get("state") not in {"pending", "completed"}
        or not isinstance(current.get("recorded_result"), dict)
        or any(current["recorded_result"].get(field) != receipt[field] for field in (
            "task_id", "worktree_id", "session_id", "native_session_id", "mode", "delivery_id",
        ))
    ):
        raise TaskError("acquisition delivery has not established current start authority")
    if (
        native_id != receipt["native_session_id"]
        or task.generation < receipt["task_generation"]
        or ("/" in (task.owner or "") and task.owner.split("/", 1)[1] != receipt["worktree_id"])
    ):
        raise TaskError("start owner_session_id does not match the acquired native conversation")


def register_routes(app: Any, queue: Any) -> None:
    from fastapi import HTTPException

    @app.post("/acquisition-markers")
    def mark(request: dict[str, Any]) -> dict:
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure(conn)
            reservation = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (request.get("reservation_key"),)).fetchone()
            task = queue._fetch(conn, request.get("task_id"))
            if not reservation or not task or reservation["task_id"] != task.id or reservation["state"] != "reserving" or task.generation != request.get("task_generation"):
                raise HTTPException(409, detail="acquisition marker fence changed")
            conn.execute("INSERT OR IGNORE INTO acquisition_markers VALUES (?)", (reservation["key"],))
            conn.commit()
        return request

    @app.post("/acquisition-receipts")
    def persist_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
        try:
            evidence = bridge_evidence(str(receipt.get("delivery_id") or ""))
            return bind(queue, receipt, evidence=evidence)
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, detail="bridge receipt authority unavailable") from exc


def command(args: argparse.Namespace) -> int:
    from .loop_commands import _resolve_cli_module
    core = _resolve_cli_module()
    with core._client(args) as client:
        result = client._unwrap(client._http.post(
            "/acquisition-markers" if args.command == "acquisition-mark" else "/acquisition-receipts",
            json=json.loads(args.request_json),
        ))
    print(json.dumps(result))
    return 0


def register_cli(sub: Any) -> None:
    parser = sub.add_parser("acquisition-bind", help="Record bridge-native start authority")
    parser.add_argument("--request-json", required=True)
    parser.set_defaults(func=command)
    parser = sub.add_parser("acquisition-mark", help="Mark a reservation as bridge-acquisition bound")
    parser.add_argument("--request-json", required=True)
    parser.set_defaults(func=command)
