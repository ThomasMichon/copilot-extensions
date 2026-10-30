"""Bounded, observational task recovery plans and queue-owned CAS mutations."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .queue_common import _task_transition_spec
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_records import SpawnState, Status, TaskError
from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER
from .ordinary_native_bindings import is_native_retired, reservation_native_identity
from .wake_recovery_target import HISTORY_LIMIT, reconciliation_plan

RECOVERY_OPERATIONS = ("detach_agent", "reset_proposed", "reset_queued")
RESET_FIELDS = {
    "owner": None, "owner_session_id": None, "lease_expires_at": None,
    "claimed_at": None, "started_at": None, "last_liveness": None,
    "card": None, "card_draft": None, "awaiting_steer": 0, "resume_requested": 0,
}
TASK_FIELDS = (
    "id", "status", "generation", "updated_at", "owner", "owner_session_id",
    "target_machine", "target_worktree", "hold_reason", "attempts", "exclusive_key",
)


def _encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _history(conn: sqlite3.Connection, table: str, task_id: str) -> list[dict]:
    # All callers supply a fixed queue-owned table, never an HTTP table name.
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM {table} WHERE task_id=? ORDER BY rowid DESC LIMIT ?",  # noqa: S608
        (task_id, HISTORY_LIMIT + 1),
    )]


def _captured(queue, conn: sqlite3.Connection, task_id: str) -> dict:
    task = queue._fetch(conn, task_id)
    if task is None:
        raise TaskError("no such task")
    return {
        "task": {name: getattr(task, name) for name in TASK_FIELDS},
        "attachments": _history(conn, "task_attachments", task_id),
        "reservations": _history(conn, "spawn_reservations", task_id),
        "wake_outbox": _history(conn, "wake_outbox", task_id),
        "run_waiter_wakes": _history(conn, "run_waiter_wakes", task_id),
        "sends": _history(conn, "execution_send_custody", task_id),
        "waiters": _history(conn, "run_waiters", task_id),
        "acquisitions": _history(conn, "acquisition_receipts", task_id),
    }


def _revision(captured: dict) -> str:
    return hashlib.sha256(_encoded(captured).encode("utf-8")).hexdigest()


def _blockers(conn: sqlite3.Connection, captured: dict) -> list[str]:
    task = captured["task"]
    reasons = []
    if any(len(rows) > HISTORY_LIMIT for key, rows in captured.items() if key != "task"):
        reasons.append("retained history exceeds the bounded recovery view; inspect owning history")
    if task["hold_reason"] is not None:
        reasons.append("task is held; clear the independent hold explicitly")
    if any(row["state"] in {"preparing", "active"} for row in captured["waiters"]):
        reasons.append("retained run waiter remains active; settle it through its owner")
    try:
        require_execution_send_reconciliation(
            conn, task["id"], exclusive_key=task["exclusive_key"],
        )
    except TaskError as exc:
        reasons.append(str(exc))
    retired_sessions = set()
    for row in captured["reservations"]:
        if row["cooling_token"] is not None:
            reasons.append("reservation cooling remains unresolved")
        if row["state"] in SpawnState.ACTIVE:
            reasons.append("retained reservation still owns execution; conclude it through its owner")
        if (
            row["state"] in SpawnState.ACTIVE
            and row["exclusive_key"] is not None and not row["exclusive_released"]
        ):
            reasons.append("reservation retains exclusive execution custody")
        retired = is_native_retired(row)
        try:
            native = reservation_native_identity(conn, row)
        except (TaskError, TypeError, ValueError) as exc:
            reasons.append(f"retained native identity is unproven: {exc}")
            continue
        if (native or row["agent_info"] or row["session_handle"]) and not retired:
            reasons.append("retained native conversation has not been retired")
        if retired and row["state"] not in SpawnState.ACTIVE:
            if native is None:
                reasons.append("retired native identity is not qualified")
            else:
                retired_sessions.add(native)
    sessions = {
        row["session_id"] for row in captured["attachments"] if row["detached_at"] is None
    }
    if task["owner_session_id"]:
        sessions.add(task["owner_session_id"])
    if sessions - retired_sessions:
        reasons.append("native assignment has no qualified retirement; stopping and detaching are separate")
    for table in ("wake_outbox", "run_waiter_wakes"):
        for row in captured[table]:
            if table == "run_waiter_wakes" and row["sender"] == CLAIM_RELEASE_SENDER:
                continue
            if row["execution_send_state"] in {"pending", "unknown"}:
                reasons.append("wake send retains unresolved transport custody")
            elif row["status"] == "delivering" or (
                row["attempts"] > 0 and row["execution_send_state"] not in {"sent", "not_sent"}
            ):
                reasons.append("attempted legacy wake has no qualified transport outcome")
    return list(dict.fromkeys(reasons))


def require_task_recovery_safe(queue, conn: sqlite3.Connection, task_id: str) -> None:
    """Prevent legacy reset from bypassing the same retained-execution fences."""
    reasons = _blockers(conn, _captured(queue, conn, task_id))
    if reasons:
        raise TaskError("; ".join(reasons))


def _action(reasons: list[str], *, command: str) -> dict:
    return {
        "allowed": not reasons, "reasons": reasons, "command": command,
        "authority": "action owner rechecks authority and all effect-time prerequisites",
    }


class TaskRecoveryMixin:
    @staticmethod
    def _migrate_task_recovery(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_recovery_receipts ("
            "task_id TEXT NOT NULL, request_id TEXT NOT NULL, request_json TEXT NOT NULL,"
            "result_json TEXT NOT NULL, PRIMARY KEY(task_id, request_id))"
        )

    def task_diagnostics(self, task_id: str) -> dict:
        """One queue snapshot; no Bridge call, prompt, process action or repair."""
        with self._connect() as conn:
            conn.execute("BEGIN")
            captured = _captured(self, conn, task_id)
            task = captured["task"]
            blockers = _blockers(conn, captured)
            allowed, _ = _task_transition_spec("reset")
            reset_reasons = blockers + ([] if task["status"] in allowed else ["status cannot be reset"])
            detach_reasons = blockers + (
                [] if task["owner"] or task["owner_session_id"] or any(
                    row["detached_at"] is None for row in captured["attachments"]
                ) else ["task has no agent assignment"]
            )
            wakes = []
            for table in ("wake_outbox", "run_waiter_wakes"):
                for row in captured[table][:HISTORY_LIMIT]:
                    state = row["execution_send_state"]
                    try:
                        reconciliation_plan(captured, table, row["id"])
                        reconcile_reasons = []
                    except TaskError as exc:
                        reconcile_reasons = [str(exc)]
                    wakes.append({
                        "outbox": table, "id": row["id"], "status": row["status"],
                        "attempts": row["attempts"],
                        "generation": row["generation"] if table == "wake_outbox" else row["task_generation"],
                        "owner_session_id": row["owner_session_id"],
                        "outcome": state or ("unknown" if row["attempts"] else "not_attempted"),
                        "qualified_custody": row["execution_send"] is not None,
                        "reconcile_supported": not reconcile_reasons,
                        "reconcile_reasons": reconcile_reasons,
                    })
            return {
                "contract": "task_recovery/v1", "task": task, "revision": _revision(captured),
                "evidence": {
                    "source": "coordinator_snapshot", "liveness": "unknown",
                    "detail": "stored state is not a current Bridge/process liveness observation",
                    "history_limit": HISTORY_LIMIT,
                    "truncated": any(
                        len(rows) > HISTORY_LIMIT for key, rows in captured.items() if key != "task"
                    ),
                },
                "attachments": captured["attachments"][:HISTORY_LIMIT],
                "reservations": [{
                    name: row[name] for name in (
                        "key", "attempt", "state", "worktree", "conversation_retired",
                        "release_requested", "exclusive_key", "updated_at",
                    )
                } for row in captured["reservations"][:HISTORY_LIMIT]],
                "wakes": wakes,
                "waiters": [{
                    name: row[name] for name in ("id", "state", "generation", "updated_at")
                } for row in captured["waiters"][:HISTORY_LIMIT]],
                "sends": [{
                    name: row[name] for name in (
                        "send_id", "reservation_key", "state", "created_at", "updated_at",
                    )
                } for row in captured["sends"][:HISTORY_LIMIT]],
                "actions": {
                    "detach_agent": _action(detach_reasons, command="detach-agent"),
                    "reset_proposed": _action(reset_reasons, command="reset --to proposed"),
                    "reset_queued": _action(reset_reasons, command="reset --to queued"),
                    "detach_target": _action([], command="detach"),
                    "force_stop": _action(
                        [] if task["status"] == Status.STARTED else ["requires started"],
                        command="force-stop",
                    ),
                    "hold": _action([] if task["hold_reason"] is None else ["already held"], command="hold"),
                    "unhold": _action([] if task["hold_reason"] is not None else ["not held"], command="unhold"),
                    "release": _action(
                        [] if task["status"] == Status.SUSPENDED else ["requires suspended"],
                        command="release",
                    ),
                    "rearm": _action(["inspect the exact failed reservation with rearm before acting"], command="rearm"),
                    "reconcile_wake": _action(
                        [] if any(row["reconcile_supported"] for row in wakes) else (
                            list(dict.fromkeys(
                                reason for row in wakes for reason in row["reconcile_reasons"]
                            )) or ["no retained wake to reconcile"]
                        ), command="reconcile-wake",
                    ),
                },
            }

    def recover_task(
        self, task_id: str, *, operation: str, expected_revision: str,
        request_id: str, actor: str, reason: str, now: float | None = None,
    ) -> dict:
        """Apply one explicit recovery once, fencing the complete retained snapshot."""
        if operation not in RECOVERY_OPERATIONS:
            raise TaskError("unsupported task recovery operation")
        if any(not isinstance(value, str) or not value.strip() for value in (
            expected_revision, request_id, actor, reason,
        )):
            raise TaskError("recovery requires revision, request identity, actor and reason")
        request = _encoded({
            "operation": operation, "expected_revision": expected_revision,
            "actor": actor, "reason": reason,
        })
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM task_recovery_receipts WHERE task_id=? AND request_id=?",
                (task_id, request_id),
            ).fetchone()
            if prior is not None:
                if prior["request_json"] != request:
                    raise TaskError("recovery request identity belongs to a different request")
                return {**json.loads(prior["result_json"]), "replayed": True}
            captured = _captured(self, conn, task_id)
            if _revision(captured) != expected_revision:
                raise TaskError("task or retained execution changed; refresh diagnostics")
            reasons = _blockers(conn, captured)
            if reasons:
                raise TaskError("; ".join(reasons))
            task = self._fetch(conn, task_id)
            ts = self._now(now)
            if operation == "detach_agent":
                if not task.owner and not task.owner_session_id and not any(
                    row["detached_at"] is None for row in captured["attachments"]
                ):
                    raise TaskError("task has no agent assignment")
                destination = Status.QUEUED if task.status in Status.OWNED else task.status
                fields = {
                    "owner": None, "owner_session_id": None, "lease_expires_at": None,
                    "claimed_at": None, "last_liveness": None,
                }
            else:
                allowed, proposed = _task_transition_spec("reset")
                if task.status not in allowed:
                    raise TaskError("status cannot be reset")
                destination = proposed if operation == "reset_proposed" else Status.QUEUED
                fields = RESET_FIELDS
            sets = ["status=?", "updated_at=?", "generation=generation+1",
                    "activity=NULL", "activity_updated_at=?"]
            sets.extend(f"{name}=?" for name in fields)
            conn.execute(
                f"UPDATE tasks SET {','.join(sets)} WHERE id=?",  # noqa: S608
                (destination, ts, ts, *fields.values(), task_id),
            )
            conn.execute(
                "UPDATE task_attachments SET detached_at=?, detach_reason=?"
                " WHERE task_id=? AND detached_at IS NULL",
                (ts, f"{operation}: {reason}", task_id),
            )
            self._audit(
                conn, task_id, ts=ts, from_status=task.status, to_status=destination,
                worker=actor, note=f"{operation}: {reason}",
            )
            updated = self._fetch(conn, task_id)
            result = {
                "operation": operation, "request_id": request_id, "replayed": False,
                "task": {name: getattr(updated, name) for name in TASK_FIELDS},
            }
            conn.execute(
                "INSERT INTO task_recovery_receipts VALUES (?,?,?,?)",
                (task_id, request_id, request, _encoded(result)),
            )
            conn.commit()
        self._notify_owned_transition()
        return result
