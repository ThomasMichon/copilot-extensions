"""Independent evidence for one ordinary native wake; never a second outbox."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any

from .bridge_command_delivery import native_prompt_receipt, steering_prompt
from .queue import TaskQueue
from .queue_common import Task
from .queue_records import TaskError
from .wake_recovery_target import reconciliation_plan


class WakeRecoveryRecords:
    """Caller owns the private directory and its retention. Recovery is observational."""

    def __init__(self, path: Path) -> None:
        self.path = path.absolute()

    @classmethod
    def create(cls, directory: Path) -> WakeRecoveryRecords:
        directory.mkdir(mode=0o700)
        obj = cls(directory / "wake.records")
        with closing(sqlite3.connect(obj.path)) as db:
            db.execute("CREATE TABLE ordinary_wake_v1(task TEXT, wake TEXT, admission TEXT NOT NULL, "
                       "evidence TEXT NOT NULL, outcome TEXT NOT NULL, receipt TEXT, PRIMARY KEY(task,wake,admission))")
        return obj

    def _open(self, mode: str) -> sqlite3.Connection:
        db = sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True)
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def retain(self, conn: sqlite3.Connection, task: Task, wake_id: str, admission: str, *, no_resume: bool = False) -> None:
        """Failure rolls back queue admission before transport. No unrelated history scan."""
        task_id = task.id
        row = conn.execute("SELECT * FROM wake_outbox WHERE task_id=? AND id=?", (task_id, wake_id)).fetchone()
        reservation = conn.execute("SELECT * FROM spawn_reservations WHERE task_id=? "
                                   "ORDER BY attempt DESC LIMIT 1", (task_id,)).fetchone()
        if row is None or reservation is None:
            raise TaskError("original ordinary wake or reservation unavailable")
        captured = {"task": {"id": task_id}, "wake_outbox": [dict(row)], "reservations": [dict(reservation)]}
        wake, target = reconciliation_plan(captured, "wake_outbox", wake_id)
        if (admission != wake["execution_send"] or wake["execution_send_state"] != "pending"
                or task.owner != wake["owner"] or task.owner_session_id != wake["owner_session_id"]
                or task.generation != wake["generation"]):
            raise TaskError("cannot retain mismatched original wake custody")
        steer = conn.execute(
            "SELECT acceptance FROM task_steer WHERE task_id=? AND wake_operation_id=?",
            (task_id, wake_id),
        ).fetchall()
        evidence = {
            "task_id": task_id, "status": task.status, "owner": task.owner,
            "owner_session_id": task.owner_session_id, "generation": task.generation,
            "lease_expires_at": task.lease_expires_at, "wake_id": wake_id,
            "admission": admission, "target": target,
            "prompt": steering_prompt(task_id, wake["message"]),
            "steer_acceptances": [json.loads(row[0]) for row in steer],
            **({"no_resume": True} if no_resume else {}),
        }
        with closing(self._open("rw")) as db:
            encoded = json.dumps(evidence, allow_nan=False, sort_keys=True)
            db.execute("INSERT OR IGNORE INTO ordinary_wake_v1 VALUES(?,?,?,?,'unknown',NULL)",
                       (task_id, wake_id, admission, encoded))
            found = db.execute("SELECT evidence,outcome FROM ordinary_wake_v1 WHERE task=? AND wake=? AND admission=?",
                               (task_id, wake_id, admission)).fetchone()
            if found != (encoded, "unknown"):
                raise TaskError("existing original attempt differs")
            db.commit()

    def retain_settlement(self, queue: TaskQueue, task_id: str, wake_id: str, admission: str) -> None:
        with queue._connect() as conn:
            row = conn.execute("SELECT execution_send,execution_send_state FROM wake_outbox "
                               "WHERE task_id=? AND id=?", (task_id, wake_id)).fetchone()
            if row is None or tuple(row) != (admission, "sent"):
                raise TaskError("queue has not settled the original wake as sent")
        with closing(self._open("rw")) as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("UPDATE ordinary_wake_v1 SET outcome='sent' WHERE task=? AND wake=? AND admission=?",
                          (task_id, wake_id, admission)).rowcount != 1:
                raise TaskError("wrong wake admission")
            db.commit()

    @staticmethod
    def _row(db: sqlite3.Connection, task_id: str, wake_id: str) -> tuple[dict[str, Any], str, Any]:
        row = db.execute("SELECT evidence,outcome,receipt FROM ordinary_wake_v1 WHERE task=? AND wake=? "
                         "ORDER BY rowid DESC LIMIT 1",
                         (task_id, wake_id)).fetchone()
        if row is None:
            raise TaskError("no retained ordinary wake")
        data = json.loads(row[0])
        if data["task_id"] != task_id or data["wake_id"] != wake_id or row[1] not in ("unknown", "sent"):
            raise TaskError("invalid retained ordinary wake")
        return data, row[1], json.loads(row[2]) if row[2] else None

    def inspect(self, task_id: str, wake_id: str) -> dict[str, Any]:
        """No durable delivery tokens, prompt text or runnable lease in the diagnostic."""
        with closing(self._open("ro")) as db:
            evidence, outcome, receipt = self._row(db, task_id, wake_id)
        return {**{k: v for k, v in evidence.items() if k not in ("admission", "prompt")},
                "outcome": outcome, "receipt": receipt}

    def observe_original(self, task_id: str, wake_id: str, *, expected_target: tuple[str, str, str | None]) -> None:
        """Query only the caller-addressed original Bridge receipt. A miss remains unknown."""
        with closing(self._open("ro")) as db:
            evidence, _, _ = self._row(db, task_id, wake_id)
        if list(expected_target) != evidence["target"]:
            raise TaskError("original wake target differs")
        bridge, native, machine = expected_target
        receipt = native_prompt_receipt(
            bridge, native, evidence["prompt"], idempotency_key=wake_id, machine=machine,
            **({"no_resume": True} if evidence.get("no_resume") is True else {}),
        )
        with closing(self._open("rw")) as db:
            db.execute("BEGIN IMMEDIATE")
            current, _, prior = self._row(db, task_id, wake_id)
            if current != evidence or (prior is not None and prior != receipt):
                raise TaskError("original wake evidence changed")
            db.execute("UPDATE ordinary_wake_v1 SET outcome='sent',receipt=? WHERE task=? AND wake=? AND admission=?",
                       (json.dumps(receipt), task_id, wake_id, evidence["admission"]))
            db.commit()
