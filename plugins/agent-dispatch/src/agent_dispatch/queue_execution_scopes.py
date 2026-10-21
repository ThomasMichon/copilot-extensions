"""Qualified scope bindings in the existing queue; no runtime enrollment."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .execution_effect_adapter import ExecutionScope
from .execution_scope_binding import ExecutionInstallation
from .queue_execution_custody import require_custody_scope
from .queue_execution_handoff import _scope_key
from .queue_records import TaskError


@dataclass(frozen=True)
class ExecutionScopeBinding:
    scope: ExecutionScope
    source: str
    producer_generation: int
    replayed: bool


class QueueExecutionScopesMixin:
    @staticmethod
    def _migrate_execution_scopes(conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS execution_scope_bindings ("
            " scope_key TEXT NOT NULL UNIQUE,"
            " repo TEXT NOT NULL, source TEXT NOT NULL,"
            " installation_baseline TEXT NOT NULL, created_at REAL NOT NULL,"
            " PRIMARY KEY(repo, source))"
        )

    def bind_execution_scope(
        self,
        installation: ExecutionInstallation,
        *,
        repo: str,
        source: str,
        expected_producer_generation: int,
        now: float | None = None,
    ) -> ExecutionScopeBinding:
        """Resolve stored producer identity, never a supervisor label or caller scope.

        The owning control route authenticates and validates installation receipts.
        This primitive neither creates a producer nor grants execution authority.
        """
        if not isinstance(installation, ExecutionInstallation):
            raise TaskError("execution scope requires a validated installation")
        repo, source = self._validate_producer_scope(repo, source)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            coordinator = conn.execute(
                "SELECT identity FROM workspace_queue_identity WHERE singleton = 1"
            ).fetchone()
            if coordinator is None:
                raise TaskError("execution scope requires the retained queue identity")
            registration = json.dumps([repo, source], separators=(",", ":"))
            scope = ExecutionScope(
                installation.identity,
                coordinator["identity"],
                repo,
                "producer:" + hashlib.sha256(registration.encode()).hexdigest(),
            )
            self._require_execution_producer(conn, scope, source, expected_producer_generation)
            key = _scope_key(scope)
            require_custody_scope(conn, key, repo, source)
            existing = conn.execute(
                "SELECT * FROM execution_scope_bindings WHERE repo = ? AND source = ?",
                (repo, source),
            ).fetchone()
            if existing is not None:
                if existing["installation_baseline"] != installation.baseline:
                    raise TaskError("execution scope installation provenance changed")
            else:
                conn.execute(
                    "INSERT INTO execution_scope_bindings"
                    " (scope_key, repo, source, installation_baseline, created_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (key, repo, source, installation.baseline, self._now(now)),
                )
            conn.commit()
        return ExecutionScopeBinding(
            scope, source, expected_producer_generation, existing is not None
        )
