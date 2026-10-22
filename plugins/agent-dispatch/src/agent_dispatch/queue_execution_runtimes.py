"""Scope-qualified runtime accounting, not sender or transition authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass

from .execution_scope_binding import ExecutionInstallation
from .queue_execution_handoff import _scope_key
from .queue_execution_scopes import ExecutionScopeBinding
from .queue_records import TaskError


@dataclass(frozen=True)
class CoordinatorExecutionRuntime:
    """Server-observed process incarnation; never a native conversation ID."""

    incarnation: str
    pid: int
    process_start: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.incarnation, str)
            or not self.incarnation.strip()
            or type(self.pid) is not int
            or self.pid < 1
            or not isinstance(self.process_start, str)
            or not self.process_start.strip()
        ):
            raise TaskError("runtime enrollment requires a known process incarnation")


class QueueExecutionRuntimesMixin:
    @staticmethod
    def _migrate_execution_runtimes(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS execution_runtime_enrollments ("
            " scope_key TEXT NOT NULL, runtime_id TEXT NOT NULL,"
            " repo TEXT NOT NULL, source TEXT NOT NULL,"
            " producer_generation INTEGER NOT NULL,"
            " role TEXT NOT NULL, process TEXT NOT NULL, created_at REAL NOT NULL,"
            " PRIMARY KEY(scope_key, runtime_id))"
        )

    def enroll_coordinator_execution_runtime(
        self,
        binding: ExecutionScopeBinding,
        installation: ExecutionInstallation,
        runtime: CoordinatorExecutionRuntime,
        *,
        now: float | None = None,
    ) -> dict[str, object]:
        """Retain the authenticated route's own process under the admission lock.

        This internal primitive trusts the owning route's receipt and process
        observations. It does not authenticate arbitrary callers or assert that
        other runtimes, senders, legacy work or adopter effects are mediated.
        """
        if (
            not isinstance(binding, ExecutionScopeBinding)
            or not isinstance(installation, ExecutionInstallation)
            or not isinstance(runtime, CoordinatorExecutionRuntime)
        ):
            raise TaskError("runtime enrollment requires qualified coordinator observations")
        key = _scope_key(binding.scope)
        encoded = json.dumps(asdict(runtime), sort_keys=True, separators=(",", ":"))
        identity = json.dumps(
            [key, binding.producer_generation, "coordinator", encoded], separators=(",", ":")
        )
        runtime_id = "runtime:" + hashlib.sha256(identity.encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_execution_scope(conn, binding.scope)
            self._require_execution_producer(
                conn, binding.scope, binding.source, binding.producer_generation
            )
            registered = conn.execute(
                "SELECT * FROM execution_scope_bindings WHERE repo = ? AND source = ?",
                (binding.scope.repository, binding.source),
            ).fetchone()
            if registered is None or (
                registered["scope_key"] != key
                or registered["installation_baseline"] != installation.baseline
                or binding.scope.installation != installation.identity
            ):
                raise TaskError("runtime enrollment requires the retained installation binding")
            existing = conn.execute(
                "SELECT * FROM execution_runtime_enrollments"
                " WHERE scope_key = ? AND runtime_id = ?",
                (key, runtime_id),
            ).fetchone()
            if existing is None:
                closed = conn.execute(
                    "SELECT 1 FROM execution_admission_closures WHERE repo = ? AND source = ?",
                    (binding.scope.repository, binding.source),
                ).fetchone()
                if closed is not None:
                    raise TaskError("execution admission closed; no new runtime enrollment")
                conn.execute(
                    "INSERT INTO execution_runtime_enrollments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        runtime_id,
                        binding.scope.repository,
                        binding.source,
                        binding.producer_generation,
                        "coordinator",
                        encoded,
                        self._now(now),
                    ),
                )
            row = conn.execute(
                "SELECT * FROM execution_runtime_enrollments"
                " WHERE scope_key = ? AND runtime_id = ?",
                (key, runtime_id),
            ).fetchone()
            conn.commit()
        return {
            "scope": asdict(binding.scope),
            "source": row["source"],
            "producer_generation": row["producer_generation"],
            "runtime_id": row["runtime_id"],
            "role": row["role"],
            "process": json.loads(row["process"]),
            "created_at": row["created_at"],
            "replayed": existing is not None,
            "runtime_enrolled": True,
            "send_admission": False,
            "acknowledged": False,
        }
