"""SQLite-backed leased task queue -- the agent-dispatch engine.

A single-writer, WAL-mode SQLite queue providing an **atomic leased claim** over
a set of *tasks*. This module is deliberately transport-free: it is a pure
library that the coordinator process wraps behind HTTP. Everything that must be
*correct under concurrency* lives here, patterned on a proven single-writer
leased-queue design.
Design notes
------------
* **Nine-state model** (see :class:`Status`):
  ``proposed -> queued -> claimed -> started -> submitted -> completed`` plus
  dormant ``suspended`` and terminal ``abandoned`` / ``dead_letter``.
  ``proposed`` and ``suspended`` are never claimable; liveness recovery returns
  only actively held tasks to ``queued``.
* **Capability-gated claim.** A task carries a hard ``requires`` set (capability
  tokens or an ``agent:<id>`` identity pin); a worker advertises a capability
  set at claim time. A task is claimable only when ``requires`` is a subset of
  the worker's capabilities. ``affinity`` is a soft preference that orders
  candidates but never excludes.
* **Cooperative claiming = redundancy.** ``claim_one`` takes a write lock
  (``BEGIN IMMEDIATE``) and re-checks ``status='queued'`` before committing, so
  N capable workers racing for one task yield exactly one winner. A dead worker's
  lease expires and any other capable worker reclaims it -- no leader election.
* **Additive migrations.** ``_migrate`` runs ``CREATE TABLE IF NOT EXISTS`` plus
  idempotent ``ALTER TABLE`` column adds, so an existing DB upgrades safely (a
  bare ``CREATE TABLE IF NOT EXISTS`` never upgrades an existing table).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from .identity import canonical_reviewer_target, canonicalize_remote  # noqa: F401 -- compatibility re-export
from .payload import PayloadStore, is_blob_ref  # noqa: F401 -- compatibility re-export
from .queue_claim_queries import QueueClaimQueriesMixin
from .queue_common import (  # noqa: F401 -- re-exported for existing call sites/tests
    DEFAULT_BLOB_THRESHOLD,
    DEFAULT_EVAL_LEASE_SECONDS,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_RESULT_MAX_BYTES,
    DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    LEGACY_REPO,
    PROGRESS_PHASE_MAX,
    PROGRESS_SUMMARY_MAX,
    AttachmentRecord,
    ClaimOutcome,
    CompletionOutcome,
    CreationOutcome,
    RunWaiterWakeOperation,
    ResultTooLargeError,
    ResultValidationError,
    StructuredResult,
    Task,
    TaskAttachmentEntry,
    VerificationRequest,
    WakeOperation,
    _BUSY_TIMEOUT_MS,
    _CLAIM_REJECTION_EVENT_LIMIT,
    _COLUMNS,
    _MAX_AFFINITY,
    _TASK_BULK_SELECT,
    _TASK_SELECT,
    _check_expected_status,
    _claimant_worktree_machine,
    _clip,
    _progress_snapshot,
    _task_transition_spec,
    encode_result,
    machine_matches,
    worker_id_for,
    _PROGRESS_PR_MAX,
    _TASK_DB_COLUMNS,
)
from .queue_completion_review import QueueCompletionReviewMixin
from .queue_excludes import QueueExcludeMixin
from .queue_handoff_fallback import HandoffFallbackMixin
from .queue_lifecycle import QueueLifecycleMixin
from .queue_liveness import LivenessMixin
from .queue_notifications import QueueNotificationMixin
from .queue_run_waiter_transition_cleanup import QueueRunWaiterTransitionCleanupMixin
from .queue_run_waiters import QueueRunWaitersMixin
from .queue_producer_fences import (  # noqa: F401 -- re-exported for existing call sites/tests
    ProducerFenceError,
    ProducerFenceMixin,
    ProducerScopeState,
    ProducerScopeTransition,
    ProducerScopeValidationError,
)
from .queue_records import (  # noqa: F401 -- re-exported for existing call sites/tests
    ExclusiveKeyBusyError,
    ResourceReservation,
    ScheduleLease,
    ScheduleRecord,
    SpawnReservation,
    SpawnState,
    Status,
    TaskError,
)
from .queue_agent_backed_repo import AgentBackedRepoMixin
from .queue_routing_assignments import RoutingAssignmentMixin
from .queue_schema_migrations import run_queue_schema_migrations
from .queue_schedule_registry import ScheduleRegistrationMixin
from .queue_spawn_rearm import SpawnRearmMixin
from .queue_spawn_conclusion import SpawnConclusionMixin
from .queue_spawn_reservations import (  # noqa: F401 -- re-exported for existing call sites/tests
    SpawnReservationMixin,
    spawn_key,
)
from .queue_storage import QueueStorageMixin
from .queue_steering import QueueSteeringMixin
from .queue_suspend import QueueSuspendMixin
from .queue_verification_requests import QueueVerificationRequestsMixin
from .registrations import (  # noqa: F401 -- re-exported for existing call sites/tests
    RegistrationError,
    RegistrationKind,
    RegistrationRecord,
    RegistrationStatus,
    derive_registration_id,
    validate_registration,
)
from .routing_provenance import (  # noqa: F401 -- re-exported for existing call sites/tests
    ACTOR_ROLES,
    ROUTING_SCHEMA_VERSION,
    TERMINAL_DISPOSITIONS,
    RoutingAssignment,
    RoutingProvenanceError,
    normalize_assignment,
)
from .routing_provenance import (  # noqa: F401 -- re-exported for existing call sites/tests
    token as routing_token,
)


class TaskQueue(
    ScheduleRegistrationMixin,
    RoutingAssignmentMixin,
    SpawnReservationMixin,
    SpawnConclusionMixin,
    SpawnRearmMixin,
    ProducerFenceMixin,
    QueueStorageMixin,
    AgentBackedRepoMixin,
    QueueClaimQueriesMixin,
    QueueLifecycleMixin,
    QueueExcludeMixin,
    QueueSuspendMixin,
    QueueVerificationRequestsMixin,
    QueueCompletionReviewMixin,
    LivenessMixin,
    HandoffFallbackMixin,
    QueueRunWaiterTransitionCleanupMixin,
    QueueRunWaitersMixin,
    QueueSteeringMixin,
    QueueNotificationMixin,
):
    """A leased, capability-gated task queue over a SQLite database file.

    Instances are cheap; each operation opens its own short-lived connection so
    the queue is safe to share across threads (each thread gets its own
    connection). WAL mode + ``BEGIN IMMEDIATE`` on the write path give atomic
    claims without a process-wide lock.
    """

    #: The states a dedup *sweep* spans -- every state except the terminal
    #: ``abandoned`` (an abandoned task is not a live duplicate of new work).
    #: This is the corpus the agent-driven "sweep + explore + verify" dedup
    #: flow reads before creating a task; see :meth:`sweep`. Includes
    #: ``completed`` alongside ``submitted`` (2026-09-25): a truly completed
    #: task is exactly as real a prior instance of the work as a merely
    #: submitted one -- the confirm step doesn't make it any less relevant to
    #: dedup.
    SWEEP_STATES = (
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
        Status.SUBMITTED,
        Status.COMPLETED,
    )

    def __init__(
        self,
        db_path: str | Path,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        eval_lease_seconds: int = DEFAULT_EVAL_LEASE_SECONDS,
        payload_dir: str | Path | None = None,
        blob_threshold: int = DEFAULT_BLOB_THRESHOLD,
        result_max_bytes: int = DEFAULT_RESULT_MAX_BYTES,
    ):
        self.db_path = str(db_path)
        self.lease_seconds = lease_seconds
        #: Tight lease for an evaluation-mode claim (see ``claim_one(evaluation=)``).
        self.eval_lease_seconds = eval_lease_seconds
        self.blob_threshold = blob_threshold
        self.result_max_bytes = result_max_bytes
        self._wake_notifier: Callable[[], None] | None = None
        self._owned_transition_notifier: Callable[[], None] | None = None
        self._run_waiter_prepare_notifier: Callable[[], None] | None = None
        # Blobs live in a ``payloads/`` directory beside the queue DB unless the
        # caller overrides it (e.g. a shared blob volume).
        if payload_dir is None:
            payload_dir = Path(self.db_path).parent / "payloads"
        self.payloads = PayloadStore(payload_dir)
        self._migrate()

    # -- connection / schema -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=_BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        deadline = time.monotonic() + (_BUSY_TIMEOUT_MS / 1000)
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    conn.close()
                    raise
                time.sleep(0.05)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            run_queue_schema_migrations(conn, now_fn=self._now)
            self._ensure_handoff_fallback_schema(conn)
            self._migrate_producer_schema(conn)

    @staticmethod
    def _migrate_producer_schema(conn: sqlite3.Connection) -> None:
        """Install the producer-fence schema under one migration write lock."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            # The first fence prototype keyed authority by source+label. It was
            # never released; preserve any local prototype tables for inspection
            # but do not let their label-dependent authority reopen a real source.
            scope_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(producer_scopes)")
            }
            history_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(producer_scope_generations)")
            }
            legacy_scope = bool(scope_columns) and "repo" not in scope_columns
            legacy_history = bool(history_columns) and "repo" not in history_columns
            if legacy_scope or legacy_history:
                # A renamed table keeps its old index name, which would block
                # creation of the canonical index on the replacement table.
                conn.execute("DROP INDEX IF EXISTS idx_producer_scope_history")
                if legacy_scope:
                    conn.execute("ALTER TABLE producer_scopes RENAME TO producer_scopes_label_v1")
                if legacy_history:
                    conn.execute(
                        "ALTER TABLE producer_scope_generations "
                        "RENAME TO producer_scope_generations_label_v1"
                    )
            # Coordinator-owned task-create generations. A scope is permanently
            # one canonical repo lane + one exact task source. An optional label
            # also protects that label from alternate or omitted source claims;
            # omission under the managed source is rejected. Every handoff
            # retires N and activates N+1 atomically.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_scopes ("
                "  repo TEXT NOT NULL,"
                "  source TEXT NOT NULL,"
                "  required_label TEXT,"
                "  current_generation INTEGER NOT NULL,"
                "  active_producer TEXT NOT NULL,"
                "  capability_hash TEXT NOT NULL,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  PRIMARY KEY(repo, source)"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_scope_generations ("
                "  repo TEXT NOT NULL,"
                "  source TEXT NOT NULL,"
                "  generation INTEGER NOT NULL,"
                "  producer_id TEXT NOT NULL,"
                "  capability_hash TEXT NOT NULL,"
                "  required_label TEXT,"
                "  state TEXT NOT NULL,"
                "  activated_at REAL NOT NULL,"
                "  retired_at REAL,"
                "  PRIMARY KEY(repo, source, generation)"
                ")"
            )
            desired_history_index = (
                "CREATE INDEX idx_producer_scope_history "
                "ON producer_scope_generations(repo, source, generation DESC)"
            )
            current_history_index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_producer_scope_history'"
            ).fetchone()
            current_history_sql = (
                " ".join(str(current_history_index["sql"] or "").split())
                if current_history_index
                else ""
            )
            if current_history_sql != desired_history_index:
                conn.execute("DROP INDEX IF EXISTS idx_producer_scope_history")
                conn.execute(desired_history_index)
            # Accepted managed create requests are durable independently from a
            # task's lifecycle and from ordinary dedup.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_create_requests ("
                "  repo TEXT NOT NULL,"
                "  source TEXT NOT NULL,"
                "  generation INTEGER NOT NULL,"
                "  request_id TEXT NOT NULL,"
                "  request_hash TEXT NOT NULL,"
                "  producer_id TEXT NOT NULL,"
                "  task_id TEXT NOT NULL,"
                "  accepted_at REAL NOT NULL,"
                "  PRIMARY KEY(repo, source, generation, request_id)"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_producer_requests_task "
                "ON producer_create_requests(task_id)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS producer_claim_rejections ("
                "  task_id TEXT NOT NULL,"
                "  fingerprint TEXT NOT NULL,"
                "  observed_at REAL NOT NULL,"
                "  PRIMARY KEY(task_id, fingerprint)"
                ")"
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -- helpers -------------------------------------------------------------
