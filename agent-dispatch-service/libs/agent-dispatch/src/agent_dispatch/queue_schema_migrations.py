"""Schema creation and migration body for the agent-dispatch SQLite queue.

Split out of :mod:`agent_dispatch.queue` (``TaskQueue._migrate``) purely for
module size: this is one long, inherently sequential procedure (every
``CREATE TABLE``/``ALTER TABLE`` the project has ever needed, each guarded
idempotently) rather than a cohesive set of independently-callable methods,
so it is extracted as a single plain function taking the already-open
connection (and a ``now_fn`` timestamp callable, standing in for
``TaskQueue._now``) rather than restructured into a mixin. ``TaskQueue._migrate``
calls this first, then its own two tail steps
(``_ensure_handoff_fallback_schema``, ``_migrate_producer_schema``) that
still need ``self``.
"""

from __future__ import annotations

import secrets
import sqlite3

from .identity import canonicalize_remote
from .queue_common import LEGACY_REPO, _COLUMNS
from .queue_records import SpawnState, Status
from .queue_value_migrations import apply_status_value_migrations


def run_queue_schema_migrations(conn: sqlite3.Connection, *, now_fn) -> None:
    """Create/migrate every table this queue owns against ``conn``.

    ``now_fn`` is ``TaskQueue._now`` (a timestamp callable), needed only by
    :func:`apply_status_value_migrations` below.
    """
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS queue_migrations ("
        "  name TEXT PRIMARY KEY,"
        "  applied_at REAL NOT NULL"
        ")"
    )
    # Status-value migrations (rename/retirement of a status constant)
    # live in queue_value_migrations.py -- extracted to keep this
    # module under its line-count cap (tools/check-module-size.py) as
    # new ones accrue.
    apply_status_value_migrations(conn, now_fn)
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
            Status.SUBMITTED,
            Status.SUBMITTED,
            Status.SUBMITTED,
            Status.SUBMITTED,
            Status.SUBMITTED,
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
        "  taken_at REAL,"
        "  idempotency_key TEXT"
        ")"
    )
    task_steer_columns = {r["name"] for r in conn.execute("PRAGMA table_info(task_steer)")}
    if "idempotency_key" not in task_steer_columns:
        try:
            conn.execute("ALTER TABLE task_steer ADD COLUMN idempotency_key TEXT")
        except sqlite3.OperationalError as exc:
            # Another concurrently-starting coordinator may have added
            # this exact column after our PRAGMA snapshot.
            if "duplicate column name" not in str(exc).lower():
                raise
    conn.execute(            "CREATE INDEX IF NOT EXISTS idx_task_steer_task ON task_steer(task_id)"
    )
    # A client-side retry of an ambiguous (already-committed-but-the-
    # response-timed-out) submission reuses the same key -- this
    # unique index is what makes ``submit_steer``'s dedup check a
    # race-safe DB-level guarantee rather than a check-then-insert
    # that a concurrent retry could still slip past.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_steer_idempotency "
        "ON task_steer(task_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    conn.execute(
    "CREATE TABLE IF NOT EXISTS run_waiters ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  task_id TEXT NOT NULL,"
    "  generation INTEGER NOT NULL,"
    "  task_generation INTEGER NOT NULL DEFAULT 0,"
    "  owner TEXT NOT NULL DEFAULT '',"
    "  owner_session_id TEXT,"
    "  pid INTEGER NOT NULL,"
    "  host TEXT,"
    "  start_token TEXT,"
    "  resume_worktree TEXT NOT NULL,"
    "  command_json TEXT NOT NULL DEFAULT '[]',"
    "  state TEXT NOT NULL,"
    "  retired_reason TEXT,"
    "  created_at REAL NOT NULL,"
    "  updated_at REAL NOT NULL"
    ")"
    )
    run_waiter_columns = {r["name"] for r in conn.execute("PRAGMA table_info(run_waiters)")}
    if "task_generation" not in run_waiter_columns:
        conn.execute(
            "ALTER TABLE run_waiters ADD COLUMN task_generation INTEGER NOT NULL DEFAULT 0"
        )
    if "owner" not in run_waiter_columns:
        conn.execute("ALTER TABLE run_waiters ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
    if "owner_session_id" not in run_waiter_columns:
        conn.execute("ALTER TABLE run_waiters ADD COLUMN owner_session_id TEXT")
    conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_run_waiters_task "
    "ON run_waiters(task_id, generation)"
    )
    conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_run_waiters_state "
    "ON run_waiters(state, created_at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_waiter_wakes ("
        "  id TEXT PRIMARY KEY,"
        "  task_id TEXT NOT NULL,"
        "  waiter_generation INTEGER NOT NULL,"
        "  task_generation INTEGER NOT NULL,"
        "  owner TEXT NOT NULL,"
        "  owner_session_id TEXT,"
        "  waiter_host TEXT,"
        "  resume_worktree TEXT NOT NULL,"
        "  sender TEXT NOT NULL,"
        "  message TEXT NOT NULL,"
        "  status TEXT NOT NULL DEFAULT 'pending',"
        "  attempts INTEGER NOT NULL DEFAULT 0,"
        "  not_before REAL NOT NULL DEFAULT 0,"
        "  created_at REAL NOT NULL,"
        "  updated_at REAL NOT NULL,"
        "  delivered_at REAL,"
        "  last_error TEXT,"
        "  delivery_token TEXT,"
        "  delivery_expires_at REAL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_waiter_wakes_due "
        "ON run_waiter_wakes(status, not_before, created_at)"
    )
    run_waiter_wake_columns = {
        r["name"] for r in conn.execute("PRAGMA table_info(run_waiter_wakes)")
    }
    if "waiter_host" not in run_waiter_wake_columns:
        conn.execute("ALTER TABLE run_waiter_wakes ADD COLUMN waiter_host TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_run_waiter_wakes_task "
        "ON run_waiter_wakes(task_id, waiter_generation)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS verification_requests ("
        "  id TEXT PRIMARY KEY,"
        "  task_id TEXT NOT NULL,"
        "  generation INTEGER NOT NULL,"
        "  trigger TEXT NOT NULL,"
        "  status TEXT NOT NULL DEFAULT 'pending',"
        "  attempts INTEGER NOT NULL DEFAULT 0,"
        "  not_before REAL NOT NULL DEFAULT 0,"
        "  created_at REAL NOT NULL,"
        "  updated_at REAL NOT NULL,"
        "  delivered_at REAL,"
        "  last_error TEXT,"
        "  delivery_token TEXT,"
        "  delivery_expires_at REAL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_verification_requests_due "
        "ON verification_requests(status, not_before, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_verification_requests_task "
        "ON verification_requests(task_id, generation)"
    )
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
        "  conversation_retired INTEGER NOT NULL DEFAULT 0,"
        "  release_requested INTEGER NOT NULL DEFAULT 0,"
        "  release_disposition TEXT,"
        "  exclusive_released INTEGER NOT NULL DEFAULT 0,"
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
    conversation_retired_newly_added = False
    if "conversation_retired" not in reservation_columns:
        try:
            conn.execute(
                "ALTER TABLE spawn_reservations ADD COLUMN "
                "conversation_retired INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        else:
            # Backfill deferred until after exclusive_key/
            # release_requested are guaranteed to exist (and
            # exclusive_key backfilled) further below -- a
            # sufficiently old database predates those columns too.
            conversation_retired_newly_added = True
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
    if "exclusive_released" not in reservation_columns:
        try:
            conn.execute(
                "ALTER TABLE spawn_reservations "
                "ADD COLUMN exclusive_released INTEGER NOT NULL DEFAULT 0"
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
    if conversation_retired_newly_added:
        # A pre-upgrade database can already contain a handle-less
        # retry after a retired session (attempt 1 retired a
        # handle; attempt 2 dropped it and failed before recording
        # a replacement) -- the column's own DEFAULT 0 would
        # otherwise silently assert "known, not retired" for those
        # legacy rows, and reserve_spawn's handle-less inheritance
        # would then enable worktree resume and resurrect the
        # retired conversation. Mark a handle-less retry
        # (session_handle IS NULL, attempt > 1) retired whenever an
        # EARLIER reservation in the same exclusive_key carried a
        # session_handle that was ever itself dropped as retired
        # (the identical release_requested + terminal-state
        # condition reserve_spawn's own retired-detection query
        # uses). Run only now that exclusive_key/release_requested
        # are guaranteed to exist and exclusive_key is backfilled.
        conn.execute(
            "UPDATE spawn_reservations SET conversation_retired = 1 "
            "WHERE session_handle IS NULL AND attempt > 1 "
            "AND exclusive_key IS NOT NULL AND EXISTS ("
            "  SELECT 1 FROM spawn_reservations AS earlier "
            "  WHERE earlier.exclusive_key = spawn_reservations.exclusive_key "
            "  AND earlier.session_handle IS NOT NULL "
            "  AND earlier.release_requested = 1 "
            "  AND earlier.state IN (?, ?, ?) "
            "  AND earlier.reserved_at < spawn_reservations.reserved_at"
            ")",
            (SpawnState.SETTLED, SpawnState.FAILED, SpawnState.REARMED),
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
            "AND state IN ('reserving','spawned','cold','releasing') "
            "AND exclusive_released = 0"
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
