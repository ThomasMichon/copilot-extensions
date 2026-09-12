"""Persistent per-(repo, PR) observation store for the GitHub provider adapter.

Phase 10 item 3's remaining slice: :mod:`agent_dispatch.pr_revision_evaluator`
needs a *previous* :class:`~agent_dispatch.github_provider_adapter.
PRObservation` to detect staleness, and
:mod:`agent_dispatch.pr_polling_policy` needs a *last observed at* timestamp
to decide whether a fallback poll is due -- both need something to hold
state between calls. This module is that store.

Deliberately its **own small SQLite file**, not a new table folded into
``queue.py``. Every other Phase 9/10 machine's live-runtime table lives in
``queue.py``'s single database, but that module is already the plugin's
largest by a wide margin (grandfathered in `tools/module-size-baseline.json`
at its current size) -- growing it further to host an unrelated concern
(PR-target observation caching has no owner/generation/claim semantics in
common with task/spawn-reservation/routing-assignment rows) is exactly the
unbounded-growth failure the module-size guard now exists to catch. A
self-contained store, one small module, is the componentized alternative:
``queue.py`` does not need to know this concern exists at all.

Concurrency is intentionally minimal: unlike ``queue.py``'s task/spawn rows
(claimed by many competing workers), a PR observation is written by exactly
one process at a time in every deployment this module is designed for today
(a single coordinator's polling/webhook loop) -- there is no multi-writer
race to fence with a CAS/generation primitive the way
:mod:`agent_dispatch.machine_coupling` does for the other machines. A plain
upsert under SQLite's own transaction is sufficient; add real fencing only
if a future slice introduces a genuinely concurrent writer.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .github_provider_adapter import PRObservation
from .provider_state_machine import ApprovalStatus, HoldReason, Mergeability, Revision

_BUSY_TIMEOUT_MS = 5000


class PRObservationStore:
    """A small, self-contained SQLite-backed store of the latest known
    :class:`PRObservation` per ``(repo, number)``, plus the wall-clock time
    it was last refreshed (from any source -- webhook or poll)."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS pr_observations ("
                "  repo TEXT NOT NULL,"
                "  number INTEGER NOT NULL,"
                "  approval_status TEXT NOT NULL,"
                "  mergeability TEXT NOT NULL,"
                "  holds TEXT NOT NULL,"
                "  diff_hash TEXT NOT NULL,"
                "  base_sha TEXT NOT NULL,"
                "  last_observed_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  PRIMARY KEY (repo, number)"
                ")"
            )

    @staticmethod
    def _to_row(observation: PRObservation) -> dict[str, str]:
        return {
            "approval_status": observation.approval_status.value,
            "mergeability": observation.mergeability.value,
            "holds": json.dumps(sorted(h.value for h in observation.holds)),
            "diff_hash": observation.revision.diff_hash,
            "base_sha": observation.revision.base_sha,
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PRObservation:
        return PRObservation(
            number=int(row["number"]),
            approval_status=ApprovalStatus(row["approval_status"]),
            mergeability=Mergeability(row["mergeability"]),
            holds=frozenset(HoldReason(h) for h in json.loads(row["holds"])),
            revision=Revision(diff_hash=row["diff_hash"], base_sha=row["base_sha"]),
        )

    def get(self, repo: str, number: int) -> PRObservation | None:
        """The latest stored observation for ``(repo, number)``, or ``None``
        if none has ever been recorded."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pr_observations WHERE repo = ? AND number = ?",
                (repo, number),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def last_observed_at(self, repo: str, number: int) -> float | None:
        """When ``(repo, number)`` was last refreshed, or ``None`` if never."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_observed_at FROM pr_observations WHERE repo = ? AND number = ?",
                (repo, number),
            ).fetchone()
        return float(row["last_observed_at"]) if row is not None else None

    def put(
        self,
        repo: str,
        number: int,
        observation: PRObservation,
        *,
        observed_at: float,
    ) -> None:
        """Upsert the latest observation for ``(repo, number)``."""
        fields = self._to_row(observation)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pr_observations "
                "(repo, number, approval_status, mergeability, holds, "
                " diff_hash, base_sha, last_observed_at, updated_at) "
                "VALUES (:repo, :number, :approval_status, :mergeability, "
                " :holds, :diff_hash, :base_sha, :observed_at, :observed_at) "
                "ON CONFLICT(repo, number) DO UPDATE SET "
                " approval_status = excluded.approval_status,"
                " mergeability = excluded.mergeability,"
                " holds = excluded.holds,"
                " diff_hash = excluded.diff_hash,"
                " base_sha = excluded.base_sha,"
                " last_observed_at = excluded.last_observed_at,"
                " updated_at = excluded.updated_at",
                {
                    "repo": repo,
                    "number": number,
                    "observed_at": observed_at,
                    **fields,
                },
            )


def record_observation(
    store: PRObservationStore,
    repo: str,
    number: int,
    current: PRObservation,
    *,
    now: float | None = None,
) -> PRObservation:
    """Evaluate ``current`` against ``store``'s prior observation for
    ``(repo, number)`` (correcting for staleness via
    :func:`agent_dispatch.pr_revision_evaluator.evaluate_observation`),
    persist the result, and return it.

    This is the glue slice tying the observer
    (:mod:`agent_dispatch.github_provider_adapter`), the evaluator
    (:mod:`agent_dispatch.pr_revision_evaluator`), and this store together --
    the caller still owns *when* to call it (a polling loop or webhook
    handler, per :mod:`agent_dispatch.pr_polling_policy`'s cadence).
    """
    from .pr_revision_evaluator import evaluate_observation

    previous = store.get(repo, number)
    evaluated = evaluate_observation(previous, current)
    store.put(repo, number, evaluated, observed_at=now if now is not None else time.time())
    return evaluated
