"""Internal send accounting; not runtime enrollment or a handoff capability."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Literal

from .execution_effect_adapter import ExecutionScope, _token
from .queue_execution_handoff import _scope_key, require_execution_admission
from .queue_records import TaskError

SendKind = Literal["launch", "resume", "deliver", "redrive"]
SendOutcome = Literal["sent", "not_sent", "unknown"]


@dataclass(frozen=True)
class ExecutionSendIdentity:
    scope: ExecutionScope
    send_id: str
    runtime_id: str
    reservation_key: str
    task_generation: int
    kind: SendKind
    generation: int = 1

    def __post_init__(self) -> None:
        _scope_key(self.scope)
        for name in ("send_id", "runtime_id", "reservation_key"):
            _token(getattr(self, name), name)
        if type(self.task_generation) is not int or self.task_generation < 0:
            raise TaskError("send requires the exact nonnegative task generation")
        if type(self.generation) is not int or self.generation != 1:
            raise TaskError("only initial automatic execution custody is implemented")
        if self.kind not in {"launch", "resume", "deliver", "redrive"}:
            raise TaskError("unknown execution send kind")


@dataclass(frozen=True)
class ExecutionSend:
    identity: ExecutionSendIdentity
    state: str
    detail: str
    created_at: float
    updated_at: float
    replayed: bool


def require_custody_scope(conn: sqlite3.Connection, key: str, repo: str, source: str) -> None:
    rows = conn.execute(
        "SELECT scope_key, repo, source"
        " FROM execution_send_custody"
        " WHERE scope_key = ? OR (repo = ? AND source = ?)"
        " UNION SELECT scope_key, repo, source"
        " FROM execution_admission_closures"
        " WHERE scope_key = ? OR (repo = ? AND source = ?)",
        (key, repo, source, key, repo, source),
    ).fetchall()
    if any((row["scope_key"], row["repo"], row["source"]) != (key, repo, source) for row in rows):
        raise TaskError("execution custody conflicts with the qualified producer scope")


def _result(row: sqlite3.Row, identity: ExecutionSendIdentity, replayed: bool) -> ExecutionSend:
    return ExecutionSend(
        identity,
        row["state"],
        row["detail"],
        row["created_at"],
        row["updated_at"],
        replayed,
    )


class QueueExecutionCustodyMixin:
    @staticmethod
    def _migrate_execution_custody(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS execution_send_custody ("
            " scope_key TEXT NOT NULL, send_id TEXT NOT NULL,"
            " repo TEXT NOT NULL, source TEXT NOT NULL,"
            " producer_generation INTEGER NOT NULL, task_id TEXT NOT NULL,"
            " reservation_key TEXT NOT NULL, task_generation INTEGER NOT NULL,"
            " kind TEXT NOT NULL, identity TEXT NOT NULL,"
            " state TEXT NOT NULL, detail TEXT NOT NULL,"
            " created_at REAL NOT NULL, updated_at REAL NOT NULL,"
            " PRIMARY KEY(scope_key, send_id),"
            " UNIQUE(reservation_key, task_generation, kind))"
        )

    def admit_execution_send(
        self,
        identity: ExecutionSendIdentity,
        *,
        source: str,
        expected_producer_generation: int,
        now: float | None = None,
    ) -> ExecutionSend:
        """Retain one pre-send operation under the queue's admission write lock.

        Only a newly admitted result may precede a send. Replaying even a pending
        operation is observation, never permission to resend after response loss.
        The future authenticated runtime adapter must enforce that distinction.
        """
        if not isinstance(identity, ExecutionSendIdentity):
            raise TaskError("execution send requires a qualified identity")
        key = _scope_key(identity.scope)
        encoded = json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))
        repo, source = self._validate_producer_scope(identity.scope.repository, source)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_execution_scope(conn, identity.scope)
            row = conn.execute(
                "SELECT * FROM execution_send_custody WHERE scope_key = ? AND send_id = ?",
                (key, identity.send_id),
            ).fetchone()
            if row is not None:
                if (
                    row["identity"] != encoded
                    or row["source"] != source
                    or type(expected_producer_generation) is not int
                    or row["producer_generation"] != expected_producer_generation
                ):
                    raise TaskError("execution send conflicts with the retained identity")
                return _result(row, identity, True)
            self._require_execution_producer(
                conn, identity.scope, source, expected_producer_generation
            )
            require_custody_scope(conn, key, repo, source)
            reservation = conn.execute(
                "SELECT r.*, t.repo, t.source, t.generation AS task_generation"
                " FROM spawn_reservations r JOIN tasks t ON t.id = r.task_id"
                " WHERE r.key = ?",
                (identity.reservation_key,),
            ).fetchone()
            if reservation is None or (
                reservation["repo"] != repo
                or reservation["source"] != source
                or reservation["task_generation"] != identity.task_generation
                or reservation["state"] not in {"reserving", "spawned"}
                or reservation["release_requested"]
                or reservation["conclusion_state"] == "pending"
            ):
                raise TaskError("execution send requires the current retained reservation")
            require_execution_admission(conn, reservation["task_id"])
            prior = conn.execute(
                "SELECT send_id FROM execution_send_custody"
                " WHERE reservation_key = ? AND task_generation = ? AND kind = ?",
                (identity.reservation_key, identity.task_generation, identity.kind),
            ).fetchone()
            if prior is not None:
                raise TaskError(
                    "execution send already has custody; observe its existing identity"
                )
            ts = self._now(now)
            conn.execute(
                "INSERT INTO execution_send_custody VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?)",
                (
                    key,
                    identity.send_id,
                    repo,
                    source,
                    expected_producer_generation,
                    reservation["task_id"],
                    identity.reservation_key,
                    identity.task_generation,
                    identity.kind,
                    encoded,
                    ts,
                    ts,
                ),
            )
            row = conn.execute(
                "SELECT * FROM execution_send_custody WHERE scope_key = ? AND send_id = ?",
                (key, identity.send_id),
            ).fetchone()
            conn.commit()
        return _result(row, identity, False)

    def settle_execution_send(
        self,
        identity: ExecutionSendIdentity,
        *,
        outcome: SendOutcome,
        detail: str,
        now: float | None = None,
    ) -> ExecutionSend:
        """Record a known transport outcome, not task exit or domain settlement.

        Unknown outcomes stay unresolved. A trusted caller must reconcile them
        against the actual transport authority, not infer absence from timeout.
        """
        if not isinstance(identity, ExecutionSendIdentity):
            raise TaskError("execution send requires a qualified identity")
        if outcome not in {"sent", "not_sent", "unknown"}:
            raise TaskError("unknown execution send outcome")
        if not isinstance(detail, str) or not detail.strip():
            raise TaskError("execution send outcome requires its reconciliation detail")
        key = _scope_key(identity.scope)
        encoded = json.dumps(asdict(identity), sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_execution_scope(conn, identity.scope)
            row = conn.execute(
                "SELECT * FROM execution_send_custody WHERE scope_key = ? AND send_id = ?",
                (key, identity.send_id),
            ).fetchone()
            if row is None or row["identity"] != encoded:
                raise TaskError("execution send has no matching retained custody")
            if row["state"] == outcome and row["detail"] == detail:
                return _result(row, identity, True)
            if row["state"] in {"sent", "not_sent"}:
                raise TaskError("execution send outcome conflicts with its settled result")
            conn.execute(
                "UPDATE execution_send_custody SET state = ?, detail = ?, updated_at = ?"
                " WHERE scope_key = ? AND send_id = ?",
                (outcome, detail, self._now(now), key, identity.send_id),
            )
            row = conn.execute(
                "SELECT * FROM execution_send_custody WHERE scope_key = ? AND send_id = ?",
                (key, identity.send_id),
            ).fetchone()
            conn.commit()
        return _result(row, identity, False)
