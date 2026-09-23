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

import secrets
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
    ResultTooLargeError,
    ResultValidationError,
    StructuredResult,
    Task,
    TaskAttachmentEntry,
    WakeOperation,
    _BUSY_TIMEOUT_MS,
    _COLUMNS,
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
    QueueClaimQueriesMixin,
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
