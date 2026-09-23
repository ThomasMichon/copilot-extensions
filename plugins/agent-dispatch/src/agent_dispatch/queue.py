"""SQLite-backed leased task queue -- the agent-dispatch engine.

A single-writer, WAL-mode SQLite queue providing an **atomic leased claim** over
a set of *tasks*. This module is deliberately transport-free: it is a pure
library that the coordinator process wraps behind HTTP. Everything that must be
*correct under concurrency* lives here, patterned on a proven single-writer
leased-queue design.
Design notes
------------
* **Eight-state model** (see :class:`Status`):
  ``proposed -> queued -> claimed -> started -> completed`` plus dormant
  ``suspended`` and terminal ``abandoned`` / ``dead_letter``. ``proposed`` and
  ``suspended`` are never claimable; liveness recovery returns only actively
  held tasks to ``queued``.
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

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from .identity import canonical_reviewer_target, canonicalize_remote  # noqa: F401 -- compatibility re-export
from .payload import PayloadStore, is_blob_ref  # noqa: F401 -- compatibility re-export
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
    ResultTooLargeError,
    ResultValidationError,
    StructuredResult,
    Task,
    TaskAttachmentEntry,
    WakeOperation,
    _BUSY_TIMEOUT_MS,
    _COLUMNS,
    _CLAIM_REJECTION_EVENT_LIMIT,
    _MAX_AFFINITY,
    _PROGRESS_PR_MAX,
    _TASK_DB_COLUMNS,
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
)
from .queue_handoff_fallback import HandoffFallbackMixin
from .queue_lifecycle import QueueLifecycleMixin
from .queue_liveness import LivenessMixin
from .queue_notifications import QueueNotificationMixin
from .queue_producer_fences import (  # noqa: F401 -- re-exported for existing call sites/tests
    ProducerFenceError,
    ProducerFenceMixin,
    ProducerScopeState,
    ProducerScopeTransition,
    ProducerScopeValidationError,
)
from .queue_records import (  # noqa: F401 -- re-exported for existing call sites/tests
    ResourceReservation,
    ScheduleLease,
    ScheduleRecord,
    SpawnReservation,
    SpawnState,
    Status,
    TaskError,
)
from .queue_routing_assignments import RoutingAssignmentMixin
from .queue_schedule_registry import ScheduleRegistrationMixin
from .queue_spawn_reservations import (  # noqa: F401 -- re-exported for existing call sites/tests
    SpawnReservationMixin,
    spawn_key,
)
from .queue_storage import QueueStorageMixin
from .queue_steering import QueueSteeringMixin
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
    ProducerFenceMixin,
    QueueStorageMixin,
    QueueLifecycleMixin,
    LivenessMixin,
    HandoffFallbackMixin,
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
    #: flow reads before creating a task; see :meth:`sweep`.
    SWEEP_STATES = (
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
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
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY)")
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            for name, decl in _COLUMNS.items():
                if name == "id" or name in existing:
                    continue
                # name/decl are internal constants from _COLUMNS, never user input.
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
                except sqlite3.OperationalError as exc:
                    # Another concurrently-starting coordinator may have added
                    # this exact column after our PRAGMA snapshot.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            # Canonicalize legacy lane spellings before the repo-scoped unique
            # dedup index is installed. The previous global dedup index already
            # prevented active duplicate keys, so normalization cannot expose an
            # active same-lane collision.
            for row in conn.execute(
                "SELECT id, repo FROM tasks WHERE repo IS NOT NULL"
            ).fetchall():
                canonical = canonicalize_remote(row["repo"])
                if canonical and canonical != row["repo"]:
                    conn.execute(
                        "UPDATE tasks SET repo = ? WHERE id = ?",
                        (canonical, row["id"]),
                    )
            desired_dedup_index = (
                "CREATE UNIQUE INDEX idx_tasks_dedup ON tasks(repo, dedup_key) "
                "WHERE dedup_key IS NOT NULL AND status IN "
                "('proposed','queued','claimed','started','suspended')"
            )
            current_index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_tasks_dedup'"
            ).fetchone()
            current_sql = (
                " ".join(str(current_index["sql"] or "").split()) if current_index else ""
            )
            if current_sql != desired_dedup_index:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    conn.execute("DROP INDEX IF EXISTS idx_tasks_dedup")
                    conn.execute(desired_dedup_index)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_repo ON tasks(repo)")
            # Sentinel-backfill rows created before ``repo`` became required so a
            # legacy task never leaks into a real repo's default-scoped views.
            # Idempotent: after the first run there are no NULL-repo rows (create
            # requires a repo).
            conn.execute("UPDATE tasks SET repo = ? WHERE repo IS NULL", (LEGACY_REPO,))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_events ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  from_status TEXT,"
                "  to_status TEXT,"
                "  worker TEXT,"
                "  note TEXT"
                ")"
            )
            # Rows completed before ``completed_by`` existed retain their
            # original completing identity when the durable audit trail proves
            # exactly one owner.  A completion retry is a completed->completed
            # event, so only the original transition into the terminal state is
            # authoritative.  Ambiguous or unprovable legacy ownership stays
            # NULL and retry-fill fails closed.
            conn.execute(
                "UPDATE tasks SET completed_by = ("
                " SELECT MIN(worker) FROM task_events"
                " WHERE task_events.task_id = tasks.id"
                "   AND task_events.to_status = ?"
                "   AND task_events.from_status <> ?"
                "   AND task_events.worker IS NOT NULL"
                ") WHERE status = ? AND completed_by IS NULL"
                " AND 1 = ("
                " SELECT COUNT(DISTINCT worker) FROM task_events"
                " WHERE task_events.task_id = tasks.id"
                "   AND task_events.to_status = ?"
                "   AND task_events.from_status <> ?"
                "   AND task_events.worker IS NOT NULL"
                ")",
                (
                    Status.COMPLETED,
                    Status.COMPLETED,
                    Status.COMPLETED,
                    Status.COMPLETED,
                    Status.COMPLETED,
                ),
            )
            # Append-only progress log -- the *accumulated* counterpart of the
            # latest-only ``latest_progress`` beat (the *resumable-goal* feature).
            # Each ``record_progress`` appends one row here in addition to
            # overwriting ``latest_progress``, so a re-embodied worker resumes
            # from the recorded progress rather than restarting the goal.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_progress ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  phase TEXT,"
                "  summary TEXT,"
                "  detail TEXT,"
                "  worker TEXT"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_progress_task ON task_progress(task_id)"
            )
            # Durable attachment history (*durable-attachment-history*,
            # effort agent-dispatch-session-worktree-history): an append-only
            # record of every session that has ever attached to a task,
            # distinct from the single mutable `owner_session_id` on `tasks`.
            # A row's `detached_at IS NULL` means that session is the task's
            # CURRENT owner session; `bind_owner_session` opens a row and
            # `_transition`/`release_suspended`/a handoff-adopting `resume`
            # close it (never delete it) before opening the next one.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_attachments ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  session_id TEXT NOT NULL,"
                "  worktree_id TEXT,"
                "  machine TEXT,"
                "  attached_at REAL NOT NULL,"
                "  detached_at REAL,"
                "  detach_reason TEXT"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_attachments_task "
                "ON task_attachments(task_id, attached_at)"
            )
            # Append-only steer inbox -- the operator's answers to a task's card
            # (the human-in-the-loop counterpart of ``task_progress``). Each
            # ``submit_steer`` appends one row; ``take_steer`` marks the oldest
            # untaken row consumed and hands it to the resumed worker.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS task_steer ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  task_id TEXT NOT NULL,"
                "  ts REAL NOT NULL,"
                "  fields TEXT NOT NULL DEFAULT '{}',"
                "  sender TEXT,"
                "  taken INTEGER NOT NULL DEFAULT 0,"
                "  taken_at REAL"
                ")"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_task_steer_task ON task_steer(task_id)")
            # Durable wake outbox. A steer/resume transaction inserts the wake
            # row before commit; the coordinator loop claims and delivers it
            # later. The row id is also the downstream idempotency key, so a
            # restart retry cannot enqueue the same wake twice.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS wake_outbox ("
                "  id TEXT PRIMARY KEY,"
                "  task_id TEXT NOT NULL,"
                "  generation INTEGER NOT NULL,"
                "  wake_seq INTEGER NOT NULL,"
                "  owner TEXT NOT NULL,"
                "  owner_session_id TEXT,"
                "  message TEXT,"
                "  status TEXT NOT NULL DEFAULT 'pending',"
                "  attempts INTEGER NOT NULL DEFAULT 0,"
                "  not_before REAL NOT NULL DEFAULT 0,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  delivered_at REAL,"
                "  last_error TEXT,"
                "  delivery_token TEXT,"
                "  delivery_expires_at REAL,"
                "  UNIQUE(task_id, generation, wake_seq)"
                ")"
            )
            wake_columns = {r["name"] for r in conn.execute("PRAGMA table_info(wake_outbox)")}
            if "delivery_expires_at" not in wake_columns:
                conn.execute("ALTER TABLE wake_outbox ADD COLUMN delivery_expires_at REAL")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wake_outbox_due "
                "ON wake_outbox(status, not_before, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_wake_outbox_task ON wake_outbox(task_id, wake_seq)"
            )
            # Spawn reservations -- the atomic "exactly one embody spawn per
            # (task, attempt)" record that closes the gap between the queue's
            # transactional claim and the non-transactional CLI-side spawn.
            # Distinct from the execution *claim* (which the embodied worker
            # makes under its own worktree identity); this row is taken by the
            # *spawner* (a `create --spawn` CLI, or the supervisor loop) BEFORE
            # launching embody, so a crash/re-poll/lease-expiry never
            # double-spawns. See :meth:`reserve_spawn`.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS spawn_reservations ("
                "  key TEXT PRIMARY KEY,"
                "  task_id TEXT NOT NULL,"
                "  exclusive_key TEXT,"
                "  attempt INTEGER NOT NULL,"
                "  state TEXT NOT NULL,"
                "  reserved_by TEXT,"
                "  session_handle TEXT,"
                "  worktree TEXT,"
                "  inherited_worktree TEXT,"
                "  worktree_ownership TEXT,"
                "  creating_host TEXT,"
                "  driver TEXT,"
                "  release_requested INTEGER NOT NULL DEFAULT 0,"
                "  release_disposition TEXT,"
                "  detail TEXT,"
                "  conclusion_state TEXT,"
                "  conclusion_detail TEXT,"
                "  cleanup_claim_token TEXT,"
                "  cleanup_claim_expires_at REAL,"
                "  reserved_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS routing_assignments ("
                "  schema_version INTEGER NOT NULL,"
                "  assignment_id TEXT PRIMARY KEY,"
                "  task_id TEXT NOT NULL,"
                "  attempt INTEGER NOT NULL,"
                "  parent_assignment_id TEXT,"
                "  purpose TEXT NOT NULL,"
                "  selected_model TEXT NOT NULL,"
                "  eligibility_state TEXT NOT NULL,"
                "  selection_reason TEXT NOT NULL,"
                "  execution_surface TEXT NOT NULL,"
                "  containment_profile_ref TEXT,"
                "  trial_ref TEXT,"
                "  decision_ref TEXT NOT NULL,"
                "  coordinator_session_ref TEXT,"
                "  worker_session_ref TEXT,"
                "  state TEXT NOT NULL,"
                "  terminal_disposition TEXT,"
                "  reason_code TEXT,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  UNIQUE(task_id, attempt)"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_routing_assignments_task "
                "ON routing_assignments(task_id, attempt)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS routing_assignment_events ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  event_id TEXT NOT NULL UNIQUE,"
                "  assignment_id TEXT NOT NULL,"
                "  event_type TEXT NOT NULL,"
                "  occurred_at REAL NOT NULL,"
                "  from_state TEXT,"
                "  to_state TEXT NOT NULL,"
                "  actor_role TEXT NOT NULL,"
                "  reason_code TEXT,"
                "  worker_session_ref TEXT,"
                "  provider TEXT,"
                "  provider_billing_event_ref TEXT"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_routing_events_assignment "
                "ON routing_assignment_events(assignment_id, occurred_at)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_billing_ref "
                "ON routing_assignment_events(provider, provider_billing_event_ref) "
                "WHERE provider IS NOT NULL "
                "AND provider_billing_event_ref IS NOT NULL"
            )
            reservation_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(spawn_reservations)").fetchall()
            }
            for column in ("worktree_ownership", "creating_host", "driver"):
                if column not in reservation_columns:
                    try:
                        conn.execute(f"ALTER TABLE spawn_reservations ADD COLUMN {column} TEXT")
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise
            reservation_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(spawn_reservations)").fetchall()
            }
            if "conclusion_state" not in reservation_columns:
                try:
                    conn.execute("ALTER TABLE spawn_reservations ADD COLUMN conclusion_state TEXT")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "conclusion_detail" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations ADD COLUMN conclusion_detail TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "cleanup_claim_token" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations ADD COLUMN cleanup_claim_token TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "cleanup_claim_expires_at" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations ADD COLUMN cleanup_claim_expires_at REAL"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "inherited_worktree" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations ADD COLUMN inherited_worktree TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            conn.execute(
                "UPDATE spawn_reservations SET inherited_worktree = worktree "
                "WHERE inherited_worktree IS NULL "
                "AND worktree_ownership = 'reused' AND worktree IS NOT NULL"
            )
            if "exclusive_key" not in reservation_columns:
                try:
                    conn.execute("ALTER TABLE spawn_reservations ADD COLUMN exclusive_key TEXT")
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "release_requested" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations "
                        "ADD COLUMN release_requested INTEGER NOT NULL DEFAULT 0"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            if "release_disposition" not in reservation_columns:
                try:
                    conn.execute(
                        "ALTER TABLE spawn_reservations ADD COLUMN release_disposition TEXT"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
            conn.execute(
                "UPDATE spawn_reservations SET state = ?, "
                "release_disposition = COALESCE(release_disposition, ?), "
                "conclusion_state = COALESCE(conclusion_state, ?) "
                "WHERE release_requested = 1 AND state IN (?, ?, ?)",
                (
                    SpawnState.RELEASING,
                    "settled",
                    "pending",
                    SpawnState.RESERVING,
                    SpawnState.SPAWNED,
                    SpawnState.COLD,
                ),
            )
            conn.execute(
                "UPDATE spawn_reservations SET exclusive_key = ("
                " SELECT exclusive_key FROM tasks"
                " WHERE tasks.id = spawn_reservations.task_id"
                ") WHERE exclusive_key IS NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spawn_res_task ON spawn_reservations(task_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spawn_res_state ON spawn_reservations(state)"
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DROP INDEX IF EXISTS idx_spawn_res_exclusive_active")
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_spawn_res_exclusive_active "
                    "ON spawn_reservations(exclusive_key) "
                    "WHERE exclusive_key IS NOT NULL "
                    "AND state IN ('reserving','spawned','cold','releasing')"
                )
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
            # Recurring-schedule registry -- the persisted form of the timer
            # producer's spec entries, so recurring jobs are managed first-class
            # (register/list/inspect/remove/pause) instead of a hand-edited JSON
            # file. ``spec`` is the JSON schedule dict the producer consumes.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schedules ("
                "  id TEXT PRIMARY KEY,"
                "  spec TEXT NOT NULL,"
                "  paused INTEGER NOT NULL DEFAULT 0,"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
            # Schedule job-leases -- single-producer election per scope
            # (pin-not-failover; see :class:`ScheduleLease`). A row's mere
            # presence pins the scope to ``holder``; no wall-clock takeover.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schedule_leases ("
                "  scope TEXT PRIMARY KEY,"
                "  holder TEXT NOT NULL,"
                "  holder_session TEXT,"
                "  acquired_at REAL NOT NULL,"
                "  renewed_at REAL NOT NULL,"
                "  expires_at REAL"
                ")"
            )
            # Producer resource reservations -- atomic election before a
            # producer creates work for an external resource. Unbound rows are
            # short leases so a crash between election and task creation can
            # recover; binding a task removes the expiry until terminal
            # reconciliation releases the row.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS resource_reservations ("
                "  key TEXT PRIMARY KEY,"
                "  owner TEXT NOT NULL,"
                "  token TEXT NOT NULL,"
                "  task_id TEXT,"
                "  acquired_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL,"
                "  expires_at REAL"
                ")"
            )
            resource_reservation_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(resource_reservations)").fetchall()
            }
            if "token" not in resource_reservation_columns:
                conn.execute("ALTER TABLE resource_reservations ADD COLUMN token TEXT")
            for row in conn.execute(
                "SELECT key FROM resource_reservations WHERE token IS NULL OR token = ''"
            ).fetchall():
                conn.execute(
                    "UPDATE resource_reservations SET token = ? WHERE key = ?",
                    (secrets.token_urlsafe(24), row["key"]),
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_res_owner ON resource_reservations(owner)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_resource_res_task "
                "ON resource_reservations(task_id)"
            )
            # Supervisor registration registry -- the durable set of units the
            # host's singleton supervisor runs (a lane to spawn for, a schedule,
            # an emitter, an evaluator). ``supervise register`` writes a row here
            # and RETURNS its handle instead of becoming the foreground loop; the
            # singleton daemon reconciles these rows into subprocesses. ``spec``
            # is the JSON config the unit's runtime consumes; ``machine``/``env``
            # scope it to exactly one host's supervisor. See ``registrations.py``.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS registrations ("
                "  id TEXT PRIMARY KEY,"
                "  kind TEXT NOT NULL,"
                "  spec TEXT NOT NULL,"
                "  machine TEXT,"
                "  env TEXT NOT NULL DEFAULT 'default',"
                "  status TEXT NOT NULL DEFAULT 'active',"
                "  created_at REAL NOT NULL,"
                "  updated_at REAL NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registrations_scope ON registrations(machine, env)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_registrations_kind ON registrations(kind)"
            )
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

    # -- consumer / lease ----------------------------------------------------

    def claim_one(
        self,
        worker_id: str,
        capabilities: Iterable[str] = (),
        *,
        repo: str | None = None,
        machine: str | None = None,
        worktree: str | None = None,
        task_id: str | None = None,
        now: float | None = None,
        lease_seconds: int | None = None,
        evaluation: bool = False,
        _with_outcome: bool = False,
    ) -> Task | ClaimOutcome | None:
        """Atomically lease the best eligible ``queued`` task, or ``None``.

        Eligible = ``status='queued'``, ``not_before <= now``, no operator hold
        (``hold_reason IS NULL`` -- see :meth:`set_hold`; a released-to-queued
        held task is still never claimable), in the claimer's ``repo`` **lane**
        (when given -- a worker only claims its own repo's tasks), every token
        in the task's ``requires`` present in ``capabilities``, and — the
        **targeting gate** — the task's ``target_machine`` / ``target_worktree``
        are unset or match the claiming agent's ``machine`` / ``worktree``. So
        an agent only claims work in its lane that is unassigned *or* assigned
        to it. A claimer that leaves ``machine`` / ``worktree`` unset can
        therefore only take *untargeted* tasks. The winning row is flipped to
        ``claimed`` under a write lock, so concurrent callers never
        double-claim.

        If ``task_id`` is given, only that task is considered (a spawned worker
        deterministically claiming *its* task) — still subject to the same gates,
        including the ``repo`` lane. Tasks carrying a managed required label are
        additionally claimable only when their persisted fence, generation, and
        accepted-request ledger row agree.

        ``worker_id`` is stamped as the task ``owner``; in a multi-machine system it is the
        canonical ``machine/worktree`` composite (see :func:`worker_id_for`).
        """
        repo = self._canonical_repo(repo)
        ts = self._now(now)
        caps = set(self._canonical_selector_tokens(capabilities))
        # The worker's FULL advertised token set for selector matching: its
        # capabilities plus its identity tokens (``machine:``/``worktree:``/
        # ``repo:``). This is what ``requires`` (affinity) and ``excludes``
        # (anti-affinity) are matched against, so a selector can target or
        # exclude by machine/worktree/repo generically -- e.g. a task with
        # ``excludes=['machine:anomalous-potato']`` is invisible to that machine.
        full_caps = set(caps)
        if machine:
            full_caps.add(f"machine:{machine}")
        if worktree:
            full_caps.add(f"worktree:{worktree}")
        if repo:
            full_caps.add(f"repo:{repo}")
        if lease_seconds is not None:
            lease = lease_seconds
        elif evaluation:
            lease = self.eval_lease_seconds  # tight evaluation-window lease
        else:
            lease = self.lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if task_id is not None:
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
                    "WHERE id = ? AND status = ? AND not_before <= ? AND hold_reason IS NULL",
                    (task_id, Status.QUEUED, ts),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_TASK_BULK_SELECT}, producer_request_hash FROM tasks "
                    "WHERE status = ? AND not_before <= ? AND hold_reason IS NULL"
                    " ORDER BY created_at ASC",
                    (Status.QUEUED, ts),
                ).fetchall()
            chosen: sqlite3.Row | None = None
            best_affinity = -1
            producer_rejections: list[dict[str, object]] = []
            for row in rows:
                if repo is not None and row["repo"] != repo:
                    continue  # lane isolation: never claim another repo's work
                rejection = self._claim_fence_rejection(conn, row)
                if rejection is not None:
                    if "_fingerprint" not in rejection:
                        rejection["_fingerprint"] = hashlib.sha256(
                            json.dumps(
                                {
                                    "task_id": row["id"],
                                    "repo": row["repo"],
                                    "source": row["source"],
                                    "labels": row["labels"],
                                    "producer_fence": row["producer_fence"],
                                    "producer_request_hash": row["producer_request_hash"],
                                    "reason": rejection["reason"],
                                },
                                ensure_ascii=True,
                                separators=(",", ":"),
                                sort_keys=True,
                            ).encode("utf-8")
                        ).hexdigest()
                        rejection["fingerprint"] = str(rejection["_fingerprint"])[:16]
                    if len(
                        producer_rejections
                    ) < _CLAIM_REJECTION_EVENT_LIMIT and self._record_claim_rejection(
                        conn, rejection, ts=ts
                    ):
                        rejection.pop("_fingerprint")
                        producer_rejections.append(rejection)
                    continue
                requires = set(
                    self._canonical_selector_tokens(
                        json.loads(row["requires"] or "[]"), strict=False
                    )
                )
                if not requires.issubset(full_caps):
                    continue
                excludes = set(
                    self._canonical_selector_tokens(
                        json.loads(row["excludes"] or "[]"), strict=False
                    )
                )
                if excludes & full_caps:
                    continue  # anti-affinity: this worker is excluded (incl. a prior "not me")
                if not machine_matches(row["target_machine"], machine):
                    continue
                if row["target_worktree"] is not None and row["target_worktree"] != worktree:
                    continue
                score = self._affinity_score(json.loads(row["affinity"] or "{}"), worker_id, caps)
                if score > best_affinity:
                    best_affinity, chosen = score, row
                    if score == _MAX_AFFINITY:
                        break
            if chosen is None:
                conn.execute("COMMIT")
                outcome = ClaimOutcome(
                    task=None,
                    producer_rejections=producer_rejections,
                )
                return outcome if _with_outcome else None
            conn.execute(
                "UPDATE tasks SET status = ?, owner = ?, claimed_at = ?, updated_at = ?,"
                " lease_expires_at = ?, last_seen_at = ?, generation = generation + 1,"
                " owner_session_id = NULL, last_liveness = NULL,"
                " attempts = attempts + 1 WHERE id = ? AND status = ?",
                (Status.CLAIMED, worker_id, ts, ts, ts + lease, ts, chosen["id"], Status.QUEUED),
            )
            self._audit(
                conn,
                chosen["id"],
                ts=ts,
                from_status=Status.QUEUED,
                to_status=Status.CLAIMED,
                worker=worker_id,
                note="claim",
            )
            task = self._fetch(conn, chosen["id"])
            conn.execute("COMMIT")
        outcome = ClaimOutcome(
            task=task,
            producer_rejections=producer_rejections,
        )
        if task is not None:
            self._notify_owned_transition()
        return outcome if _with_outcome else task

    def claim_outcome(self, *args: object, **kwargs: object) -> ClaimOutcome:
        """Claim a task and return newly recorded producer rejection events."""
        kwargs["_with_outcome"] = True
        result = self.claim_one(*args, **kwargs)  # type: ignore[arg-type]
        assert isinstance(result, ClaimOutcome)
        return result

    def mine(
        self, machine: str, worktree: str, *, repo: str | None = None
    ) -> dict[str, list[Task]]:
        """Return an agent's inbox: tasks ``assigned`` to it and ``owned`` by it.

        Scoped to the ``repo`` lane when given (an agent's inbox is its own
        repo's work only).

        - ``assigned``: ``queued`` tasks targeted specifically at this agent —
          ``target_worktree == worktree``, or a machine-wide assignment
          (``target_machine == machine`` with no worktree pin). Untargeted open
          tasks are *not* listed here (they belong to no one in particular).
        - ``owned``: non-terminal tasks this agent has claimed/started/suspended
          (``owner == machine/worktree``).
        """
        repo = self._canonical_repo(repo)
        owner = worker_id_for(machine, worktree)
        repo_clause = " AND repo = ?" if repo is not None else ""
        repo_param: tuple = (repo,) if repo is not None else ()
        with self._connect() as conn:
            assigned_rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks WHERE status = ? AND ("  # noqa: S608 (repo_clause is a constant; all values parameterized)
                "  target_worktree = ?"
                "  OR (target_machine = ? COLLATE NOCASE AND target_worktree IS NULL)"
                ")" + repo_clause + " ORDER BY created_at ASC",
                (Status.QUEUED, worktree, machine, *repo_param),
            ).fetchall()
            owned_rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                "WHERE owner = ? AND status IN (?, ?, ?)"
                + repo_clause
                + " ORDER BY created_at ASC",
                (
                    owner,
                    Status.CLAIMED,
                    Status.STARTED,
                    Status.SUSPENDED,
                    *repo_param,
                ),
            ).fetchall()
        return {
            "assigned": [Task._from_row(r) for r in assigned_rows],
            "owned": [Task._from_row(r) for r in owned_rows],
        }

    @staticmethod
    def _affinity_score(affinity: dict[str, str], worker_id: str, caps: set[str]) -> int:
        """Rank a queued task for a worker: exact agent match > capability hint > any."""
        if not affinity:
            return 0
        pref_agent = affinity.get("agent")
        if pref_agent in (worker_id, "same") and pref_agent is not None:
            return _MAX_AFFINITY
        pref_cap = affinity.get("capability")
        if pref_cap is not None and pref_cap in caps:
            return 1
        return 0

    # -- read helpers --------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            return self._fetch(conn, task_id)

    def has_pending_wakes(self) -> bool:
        """Return whether a pending wake exists, including a delayed retry."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM wake_outbox WHERE status = 'pending' LIMIT 1"
            ).fetchone()
        return row is not None

    def list(
        self,
        *,
        repo: str | None = None,
        status: str | Sequence[str] | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        evaluator_ref: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        exclusive_key: str | None = None,
        limit: int = 200,
    ) -> list[Task]:
        """List tasks, optionally filtered. Newest first.

        ``repo`` scopes to a single lane (the caller's repo by default, at the
        CLI). ``status`` accepts a single status *or* a sequence of statuses (an
        ``IN (...)`` filter), so a producer can browse several states in one
        call. :meth:`sweep` uses this to pull the whole non-abandoned corpus.
        """
        repo = self._canonical_repo(repo)
        clauses: list[str] = []
        params: list[object] = []
        if repo is not None:
            clauses.append("repo = ?")
            params.append(repo)
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                clauses.append(f"status IN ({placeholders})")
                params.extend(statuses)
        if target_machine is not None:
            clauses.append("target_machine = ? COLLATE NOCASE")
            params.append(target_machine)
        if target_repo is not None:
            clauses.append("target_repo = ?")
            params.append(target_repo)
        if evaluator_ref is not None:
            if evaluator_ref:
                clauses.append("evaluator_ref = ?")
                params.append(evaluator_ref)
            else:
                clauses.append("evaluator_ref IS NULL")
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if origin_ref is not None:
            clauses.append("origin_ref = ?")
            params.append(origin_ref)
        if exclusive_key is not None:
            clauses.append("exclusive_key = ?")
            params.append(exclusive_key)
        if label is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each("
                "CASE WHEN json_valid(tasks.labels) THEN tasks.labels ELSE '[]' END"
                ") "
                "WHERE json_each.value = ?)"
            )
            params.append(label)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            # `where` is built from literal clause strings; values are bound.
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def find(self, text: str, *, repo: str | None = None, limit: int = 50) -> list[Task]:
        """Substring search over title/prompt -- one primitive in the
        agent-driven dedup flow (a quick targeted probe). Scoped to the ``repo``
        lane when given. For a full pre-create review, prefer :meth:`sweep`.
        """
        repo = self._canonical_repo(repo)
        like = f"%{text}%"
        repo_clause = " AND repo = ?" if repo is not None else ""
        repo_param: tuple = (repo,) if repo is not None else ()
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_BULK_SELECT} FROM tasks "
                "WHERE (title LIKE ? OR prompt LIKE ?)"
                + repo_clause
                + " ORDER BY created_at DESC LIMIT ?",
                (like, like, *repo_param, limit),
            ).fetchall()
        return [Task._from_row(r) for r in rows]

    def sweep(self, *, repo: str | None = None, limit: int = 500) -> list[Task]:
        """Return the dedup corpus: every non-abandoned task, newest first.

        Scoped to the ``repo`` lane when given (the CLI always passes the
        caller's repo -- a producer dedups against *its own* lane, since another
        repo's tasks are invisible to it). Backs the agent-driven
        *sweep + explore + verify* flow a producer runs before creating a task:
        it enumerates every ``proposed``/``queued``/``claimed``/``started``/
        ``suspended``/``completed`` task so the producer can read the
        descriptions and judge whether the work already exists -- no semantic
        index required.
        Correctness rests on each task carrying a self-contained title + prompt.
        (A future VEI adapter is a pluggable *optimization* over this same
        corpus, never a prerequisite.)
        """
        return self.list(repo=repo, status=self.SWEEP_STATES, limit=limit)

    def events(self, task_id: str) -> list[dict[str, object]]:
        """Return the append-only audit trail for a task, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, from_status, to_status, worker, note FROM task_events "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def progress_log(self, task_id: str) -> list[dict[str, object]]:
        """Return the accumulated append-only progress log for a task.

        Rows are chronological (oldest first) -- the durable, resumable record of
        every progress beat (the *resumable-goal* feature). Distinct from the
        latest-only ``latest_progress`` beat on the task row: a re-embodied worker
        reads this to continue toward the goal from recorded progress rather than
        restarting it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, phase, summary, detail, worker FROM task_progress "
                "WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- spawn reservation lookups ------------------------------------------

    def get_reservation(self, key: str) -> SpawnReservation | None:
        """Return one reservation by key, or ``None``."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
        return SpawnReservation._from_row(row) if row else None

    def latest_reservation(self, task_id: str) -> SpawnReservation | None:
        """Return the highest-attempt reservation for a task, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return SpawnReservation._from_row(row) if row else None

    def list_reservations(
        self,
        *,
        task_id: str | None = None,
        state: str | Sequence[str] | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        limit: int = 200,
    ) -> list[SpawnReservation]:
        """List spawn reservations, newest first, optionally filtered by task or
        state (a single state or a set of states)."""
        repo = self._canonical_repo(repo)
        clauses: list[str] = []
        params: list[object] = []
        if task_id is not None:
            clauses.append("r.task_id = ?")
            params.append(task_id)
        if state is not None:
            states = [state] if isinstance(state, str) else list(state)
            clauses.append(f"r.state IN ({','.join('?' * len(states))})")
            params.extend(states)
        join_tasks = repo is not None or label is not None or resume_requested is not None
        if repo is not None:
            clauses.append("t.repo = ?")
            params.append(repo)
        if label is not None:
            clauses.append("EXISTS (SELECT 1 FROM json_each(t.labels) WHERE value = ?)")
            params.append(label)
        if conclusion_state is not None:
            clauses.append("r.conclusion_state = ?")
            params.append(conclusion_state)
        if resume_requested is not None:
            clauses.append("t.resume_requested = ?")
            params.append(1 if resume_requested else 0)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                # ``where`` is built only from constant column names + bound '?'
                # placeholders; every value goes through ``params`` (never
                # interpolated), so this is not an injection vector.
                "SELECT r.* FROM spawn_reservations r "
                f"{'JOIN tasks t ON t.id = r.task_id ' if join_tasks else ''}"
                f"{where} ORDER BY r.reserved_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [SpawnReservation._from_row(r) for r in rows]
