"""Run waiter record projection and exact-generation SQL selection."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def select_waiter_row(
    conn: sqlite3.Connection, task_id: str, *, states: tuple[str, ...],
    generation: int | None = None, pid: int | None = None,
    host: str | None = None, start_token: str | None = None,
) -> sqlite3.Row | None:
    placeholders = ",".join("?" for _ in states)
    clauses = ["task_id = ?", f"state IN ({placeholders})"]
    params: list[object] = [task_id, *states]
    for field, value in (("generation", generation), ("pid", pid),
                         ("host", host), ("start_token", start_token)):
        if value is not None:
            clauses.append(f"{field} = ?")
            params.append(int(value) if field in ("generation", "pid") else value)
    query = (
        "SELECT * FROM run_waiters WHERE "
        + " AND ".join(clauses)
        + " ORDER BY generation DESC LIMIT 1"
    )
    return conn.execute(query, params).fetchone()


def run_waiter_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "kind": row["kind"],
        "delegation": json.loads(row["delegation_json"] or "null"),
        "event_id": row["event_id"],
        "replay_registration_id": row["replay_registration_id"],
        "replay_attempted": bool(row["replay_attempted"]),
        "replay_settled": bool(row["replay_settled"]),
        "id": row["id"],
        "task_id": row["task_id"],
        "generation": row["generation"],
        "task_generation": row["task_generation"],
        "owner": row["owner"],
        "owner_session_id": row["owner_session_id"],
        "pid": row["pid"],
        "host": row["host"],
        "start_token": row["start_token"],
        "resume_worktree": row["resume_worktree"],
        "command": json.loads(row["command_json"] or "[]"),
        "state": row["state"],
        "retired_reason": row["retired_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def run_waiter_summary(waiter: dict) -> dict:
    summary = {field: waiter[field] for field in (
        "command", "state", "created_at", "updated_at",
    )}
    if waiter.get("kind") == "delegated":
        identity = waiter["delegation"]
        summary.update(kind="delegated", generation=waiter["generation"],
                       repo=identity["repo"], number=identity["number"],
                       observation_deadline=waiter["created_at"] + identity["timeout"])
        if "replay" in identity:
            summary.update(
                observation_deadline=identity["replay"]["deadline_at"],
                delivery="service-replay", replay_settled=waiter["replay_settled"],
            )
    return summary
