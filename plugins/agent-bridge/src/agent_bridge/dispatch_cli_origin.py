"""First-save CLI origin from the original acquisition, never mutable placement."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .dispatch_origin import DispatchSessionOrigin

CAPABILITY = "dispatch_cli_session_origin"


def ensure_schema(conn: sqlite3.Connection) -> None:
    if "dispatch_origin_json" not in {
        row[1] for row in conn.execute("PRAGMA table_info(live_sessions)")
    }:
        conn.execute("ALTER TABLE live_sessions ADD COLUMN dispatch_origin_json TEXT")


def validate_schema(conn: sqlite3.Connection) -> None:
    column = next(
        (row for row in conn.execute("PRAGMA table_info(live_sessions)")
         if row[1] == "dispatch_origin_json"), None,
    )
    if column is None or column[2].upper() != "TEXT" or column[3] != 0:
        raise ValueError("CLI session origin requires supported writable preparation")


def first_save(
    conn: sqlite3.Connection, *, worktree_id: str | None, driven_by: str | None,
) -> str | None:
    """Run inside registration's write transaction, before its initial insert."""
    from .acquisition_contract import AcquisitionRequest, cli_driver
    from .acquisition_journal import _digest

    if not worktree_id or not driven_by or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='acquisition_launch_gates'",
    ).fetchone():
        return None
    gate = conn.execute(
        "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?",
        (worktree_id,),
    ).fetchone()
    if (
        gate is None or gate["mode"] != "cli" or gate["driver"] != driven_by
        or gate["native_session_id"] is not None or gate["session_id"] is not None
    ):
        return None
    row = conn.execute(
        "SELECT a.request_digest,a.state,e.stage,e.data_json "
        "FROM acquisition_attempts a JOIN acquisition_effects e USING(delivery_id) "
        "WHERE a.delivery_id=? AND a.worktree_id=?",
        (gate["delivery_id"], worktree_id),
    ).fetchone()
    if row is None:
        return None
    data = json.loads(row["data_json"])
    original = data.get("_request") or {}
    if original.get("dispatch_origin") is None:
        return None
    request = AcquisitionRequest.model_validate(original)
    if (
        row["state"] != "pending" or row["stage"] != "launching"
        or data.get("acquired") != "created" or data.get("creation_authorized") is not True
        or data.get("session_id") is not None or data.get("native_session_id") is not None
        or request.mode != "cli" or request.worktree_id != worktree_id
        or request.delivery_id != gate["delivery_id"] or cli_driver(request) != driven_by
        or _digest(request) != row["request_digest"]
    ):
        raise ValueError("CLI session origin does not match the original creation")
    assert request.dispatch_origin is not None
    return request.dispatch_origin.model_dump_json()


def inspect(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Opaque or legacy storage is not upgraded into guessed attribution."""
    try:
        raw = row.get("dispatch_origin_json") if row else None
        return DispatchSessionOrigin.model_validate_json(raw).model_dump(mode="json") if raw else None
    except (ValueError, TypeError):
        return None
