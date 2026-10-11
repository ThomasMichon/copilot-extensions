"""Persist bridge-native start authority in the task owner's database."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from .queue import TaskError


def ensure(conn: Any) -> None:
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


def validate_start(conn: Any, task: Any, native_id: str | None) -> None:
    ensure(conn)
    rows = conn.execute(
        "SELECT a.* FROM acquisition_receipts a JOIN spawn_reservations s "
        "ON s.key=a.reservation_key WHERE a.task_id=? "
        "AND s.state IN ('reserving','spawned') AND s.release_requested=0 "
        "ORDER BY s.attempt DESC", (task.id,),
    ).fetchall()
    if not rows:
        if (task.owner or "").startswith("acquired-"):
            raise TaskError("acquired worker start requires a bridge acquisition receipt")
        return
    receipt = rows[0]
    current = bridge_evidence(receipt["delivery_id"])
    if (
        current.get("stage") not in {"delivering", "delivered"}
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
            "/acquisition-receipts", json=json.loads(args.request_json),
        ))
    print(json.dumps(result))
    return 0


def register_cli(sub: Any) -> None:
    parser = sub.add_parser("acquisition-bind", help="Record bridge-native start authority")
    parser.add_argument("--request-json", required=True)
    parser.set_defaults(func=command)
