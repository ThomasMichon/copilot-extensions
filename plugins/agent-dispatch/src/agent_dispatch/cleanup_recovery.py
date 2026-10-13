"""Exact, preview-first recovery of housekeeping, not task execution."""
from __future__ import annotations

import hashlib
import json
import math

from .queue_records import SpawnState, Status, TaskError


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _has_history(conn):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cleanup_recoveries'",
    ).fetchone() is not None


def _allocation_guard(conn, row):
    blocker = conn.execute(
        "SELECT key FROM spawn_reservations WHERE key != ? AND "
        "((task_id=? AND attempt >= ?) OR "
        "((worktree=? OR inherited_worktree=?) AND "
        "(reserved_at >= ? OR state IN ('reserving','spawned','cold','releasing')))) LIMIT 1",
        (row["key"], row["task_id"], row["attempt"], row["worktree"],
         row["worktree"], row["reserved_at"]),
    ).fetchone()
    if blocker:
        raise TaskError(f"cleanup allocation fence changed: {blocker['key']}")
    if row["exclusive_key"]:
        active = conn.execute(
            "SELECT key FROM spawn_reservations WHERE key != ? AND exclusive_key=? "
            "AND state IN ('reserving','spawned','cold','releasing') "
            "AND exclusive_released=0 LIMIT 1", (row["key"], row["exclusive_key"]),
        ).fetchone()
        if active:
            raise TaskError("cleanup exclusive allocation remains active")


def validate_recovery(queue, conn, row):
    """Apply the recovery generation fence again under the cleanup lease."""
    if not _has_history(conn):
        return
    history = conn.execute(
        "SELECT generation, identity FROM cleanup_recoveries WHERE reservation_key=? "
        "ORDER BY applied_at DESC, rowid DESC LIMIT 1", (row["key"],),
    ).fetchone()
    if history is None:
        return
    task = queue._fetch(conn, row["task_id"])
    if task is None or task.generation != history["generation"] or task.status not in Status.TERMINAL:
        raise TaskError("cleanup recovery task generation changed")
    if _json(_identity(row)) != history["identity"]:
        raise TaskError("cleanup recovery provenance changed")
    _allocation_guard(conn, row)


def _identity(row):
    return {name: row[name] for name in (
        "key", "task_id", "attempt", "reserved_at", "worktree",
        "worktree_ownership", "creating_host", "driver", "state", "session_handle",
    )}


def _reset_payload(raw):
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise TaskError("cleanup detail is unknown; recovery requires a valid envelope") from exc
    if not isinstance(payload, dict):
        raise TaskError("cleanup detail is unknown; recovery requires a supported cleanup kind")
    if "cleanup_kind" in payload:
        if payload["cleanup_kind"] not in {"terminal", "attempt"}:
            raise TaskError("unsupported cleanup kind")
    elif (payload.get("action") not in {"failed", "preserved", "skipped", "primed", "already-primed"}
          or not isinstance(payload.get("reason"), str) or not payload["reason"]
          or type(payload.get("attempts")) is not int):
        raise TaskError("cleanup detail is unknown; recovery requires recorded retry progress")
    updated = {**payload, "action": "pending", "reason": "operator-cleanup-retry",
               "attempts": 0, "next_attempt_at": 0}
    components = []
    for name in ("session_end", "worktree_cleanup"):
        component = payload.get(name)
        if component is None:
            continue
        if not isinstance(component, dict) or component.get("state") not in {"held", "pending", "complete"}:
            raise TaskError(f"unknown cleanup component: {name}")
        components.append(component)
        if component["state"] != "complete":
            updated[name] = {**component, "state": "pending", "attempts": 0, "next_attempt_at": 0}
    if components and all(component["state"] == "complete" for component in components):
        raise TaskError("cleanup envelope has no incomplete component")
    return updated


def recover(queue, key, *, expected_generation, current_host, apply=False,
            expected_preview=None, reason=None, request_id=None, now=None):
    """Schedule one leased retry through the existing supervisor authority."""
    if (type(expected_generation) is not int or expected_generation < 0
            or not isinstance(current_host, str) or not current_host.strip()):
        raise TaskError("cleanup recovery requires exact generation and current host")
    reason = (reason or "").strip()
    if apply and (not reason or not request_id or not expected_preview):
        raise TaskError("cleanup recovery apply requires preview, request id and audit reason")
    request = _json({"key": key, "generation": expected_generation, "host": current_host.casefold(),
                     "preview": expected_preview, "reason": reason})
    ts = queue._now(now)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if apply and _has_history(conn):
            prior = conn.execute(
                "SELECT request, response FROM cleanup_recoveries WHERE request_id=?", (request_id,),
            ).fetchone()
            if prior:
                if prior["request"] != request:
                    raise TaskError("cleanup recovery request id reused with different parameters")
                return json.loads(prior["response"])
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
        if row is None:
            raise TaskError(f"no such reservation: {key}")
        task = queue._fetch(conn, row["task_id"])
        if task is None or task.status not in Status.TERMINAL or task.generation != expected_generation:
            raise TaskError("cleanup recovery requires unchanged terminal task generation")
        if (row["state"] not in {SpawnState.FAILED, SpawnState.SETTLED}
                or row["conclusion_state"] != "held"
                or not row["worktree"] or row["worktree_ownership"] != "created"
                or not row["driver"] or not row["creating_host"]
                or row["creating_host"].casefold() != current_host.casefold()):
            raise TaskError("cleanup recovery requires held, retired, current-host created provenance")
        if row["cleanup_claim_token"]:
            try:
                expires_at = float(row["cleanup_claim_expires_at"])
            except (ValueError, TypeError) as exc:
                raise TaskError("cleanup retry lease expiry is unknown") from exc
            if not math.isfinite(expires_at):
                raise TaskError("cleanup retry lease expiry is unknown")
            if expires_at > ts:
                raise TaskError("cleanup retry lease is still active")
        _allocation_guard(conn, row)
        payload = _reset_payload(row["conclusion_detail"])
        preview_token = _digest({"reservation": dict(row), "generation": expected_generation})
        result = {"reservation_key": key, "task_id": task.id, "generation": expected_generation,
                  "eligible": True, "preview_token": preview_token, "scheduled": False,
                  "execution": "existing-supervisor-lease-and-terminal-guards"}
        if not apply:
            return result
        if expected_preview != preview_token:
            raise TaskError("cleanup recovery preview changed; preview again")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cleanup_recoveries (request_id TEXT PRIMARY KEY, "
            "reservation_key TEXT NOT NULL, generation INTEGER NOT NULL, applied_at REAL NOT NULL, "
            "identity TEXT NOT NULL, prior_detail TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL)",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cleanup_recoveries_reservation "
            "ON cleanup_recoveries(reservation_key, applied_at)",
        )
        result = {**result, "scheduled": True, "request_id": request_id}
        conn.execute(
            "INSERT INTO cleanup_recoveries VALUES (?,?,?,?,?,?,?,?)",
            (request_id, key, expected_generation, ts, _json(_identity(row)),
             row["conclusion_detail"], request, _json(result)),
        )
        conn.execute(
            "UPDATE spawn_reservations SET conclusion_state='pending', conclusion_detail=?, "
            "updated_at=?, cleanup_claim_token=NULL, cleanup_claim_expires_at=NULL WHERE key=?",
            (_json(payload), ts, key),
        )
        queue._audit(
            conn, task.id, ts=ts, from_status=task.status, to_status=task.status,
            worker="operator", note=f"held cleanup retry: reservation={key}; request={request_id}; reason={reason}",
        )
        conn.commit()
        return result


def history(queue, key):
    """Read retained failure envelopes even after cleanup subsequently completes."""
    with queue._connect() as conn:
        if conn.execute("SELECT 1 FROM spawn_reservations WHERE key=?", (key,)).fetchone() is None:
            raise TaskError(f"no such reservation: {key}")
        if not _has_history(conn):
            return []
        rows = conn.execute(
            "SELECT request_id, generation, applied_at, prior_detail, request FROM cleanup_recoveries "
            "WHERE reservation_key=? ORDER BY applied_at, rowid", (key,),
        ).fetchall()
        return [{**dict(row), "request": json.loads(row["request"])} for row in rows]
