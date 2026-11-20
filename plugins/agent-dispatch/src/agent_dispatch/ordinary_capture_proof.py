"""Join creation, first workspace receipt and execution using the retained audit event."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from typing import Any

from .queue_records import SpawnReservation, TaskError


def original_evidence(conn: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    event = conn.execute(
        "SELECT task_relationship FROM task_events WHERE task_id=? AND id=?",
        (request["task_id"], request["event_id"]),
    ).fetchone()
    value = json.loads(event[0]) if event and event[0] else None
    if not value or value["request"] != request:
        raise TaskError("ordinary admission lost its original relationship event")
    binding = {**request, **value.get("continuation", {})}

    def row(key: str) -> sqlite3.Row:
        result: sqlite3.Row | None = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=? AND task_id=?",
            (key, request["task_id"]),
        ).fetchone()
        if (
            result is None
            or result["driver"] != "ordinary-bridge"
            or not result["task_relationship_required"]
        ):
            raise TaskError("ordinary admission lost its original governed reservation")
        return result

    def retired(prior: sqlite3.Row) -> bool:
        return bool(
            prior["release_requested"]
            and not prior["native_release_pending"]
            and prior["state"] in {"settled", "rearmed", "failed"}
        )

    prepared = row(request["reservation_key"])
    root = row(prepared["allocation_reservation_key"])
    current = row(binding["reservation_key"])
    if (
        not root["creation_key"]
        or root["allocation_reservation_key"] != root["key"]
        or prepared["worktree_ownership"] != "created"
        or prepared["inherited_worktree"] is not None
        or any(
            item["creation_key"] != root["creation_key"]
            or item["reserved_by"] != root["reserved_by"]
            or item["allocation_revision"] != root["allocation_revision"]
            for item in (current, prepared)
        )
        or current["allocation_reservation_key"] != root["key"]
        or any(
            item["allocation_cohort"] != root["allocation_cohort"] for item in (current, prepared)
        )
        or any(
            item["task_relationship_event_id"] != request["event_id"]
            for item in (current, prepared)
        )
        or current["task_relationship_generation"] != binding["generation"]
        or prepared["task_relationship_generation"] != request["generation"]
        or not prepared["worktree_writer_requirement"]
        or current["worktree_writer_requirement"] != prepared["worktree_writer_requirement"]
        or any(
            current[k] != prepared[k]
            for k in (
                "worktree",
                "worktree_path",
                "allocation_project",
                "execution_machine",
                "creation_nonce",
            )
        )
    ):
        raise TaskError("ordinary admission cannot adopt a carried, foreign or unknown workspace")
    if root["key"] != prepared["key"] and (
        not retired(root)
        or any(
            root[k] is not None
            for k in (
                "worktree",
                "worktree_path",
                "agent_info",
                "session_handle",
                "task_relationship_event_id",
                "creation_nonce",
                "worktree_writer_requirement",
            )
        )
    ):
        raise TaskError("ordinary retry lacks a qualified empty original reservation")
    if current["key"] != prepared["key"] and (
        current["worktree_ownership"] != "reused"
        or current["inherited_worktree"] != prepared["worktree"]
        or not retired(prepared)
        or prepared["agent_info"] is not None
        or prepared["session_handle"] is not None
        or not conn.execute(
            "SELECT 1 FROM task_pool_assignments WHERE task_id=? AND reservation_key=? AND task_generation=?",
            (request["task_id"], current["key"], binding["generation"]),
        ).fetchone()
    ):
        raise TaskError("ordinary retry has no verified continuation of its original workspace")
    # The Worktrees stamp names the allocation root, not necessarily the first
    # prepared receipt. Only the execution row can attest its Bridge native.
    result = asdict(SpawnReservation._from_row(prepared))
    execution = SpawnReservation._from_row(current)
    result.update(
        key=root["key"],
        attempt=root["attempt"],
        agent_info=execution.agent_info,
        session_handle=execution.session_handle,
    )
    return result
