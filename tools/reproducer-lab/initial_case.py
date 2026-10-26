"""One explicit initial task submission; never replay or whole-lab admission."""

from __future__ import annotations

import asyncio
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from guest_operation import GuestOperation, STATE

if TYPE_CHECKING:
    from agent_bridge.acquisition_journal import AcquisitionJournal
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.queue_common import CreationOutcome
    from process_telemetry.diagnostic_logs import DiagnosticLog

DISPATCH = STATE / "dispatch" / "queue.db"
BRIDGE = STATE / "bridge" / "bridge.db"
DIAGNOSTICS = Path("/lab/evidence/diagnostics")
CASE = "offline-dispatch-bridge-v1"
LANE = "github.com/example/reproducer-lab"
MAX_OBSERVATION_SECONDS = 5.0
OPTIONAL_DISPATCH_HISTORY = ("cleanup_recoveries",)

# Include retained history, not only currently active or highest-attempt rows.
DISPATCH_HISTORY = (
    "tasks", "spawn_reservations", "task_events", "task_attachments",
    "wake_outbox", "run_waiter_wakes", "execution_send_custody",
    "task_progress", "task_steer", "run_waiters", "verification_requests",
    "routing_assignments", "routing_assignment_events", "resource_reservations",
    "schedule_leases", "acquisition_markers", "acquisition_receipts",
    "producer_create_requests", "producer_claim_rejections", "handoff_fallback_attempts",
    "execution_admission_closures",
)
BRIDGE_HISTORY = (
    "acquisition_attempts", "acquisition_effects", "sessions", "turns", "events",
    "live_sessions", "live_messages", "pending_prompts", "worktree_ownership",
    "cli_mode_reservations", "cold_outages", "workspace_embodiments",
    "provider_embodiments", "remote_acp_relationships", "bridge_inbox_consumers",
    "acquisition_launch_gates", "acquisition_bindings", "delivery_cursors",
    "delivery_cursor_invalidations", "live_session_aliases",
)


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int


def _private_path(path: Path, *, directory: bool, device: int) -> FileIdentity:
    info = path.lstat()
    expected_mode = 0o700 if directory else 0o600
    if (
        path.resolve(strict=True) != path
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        != (1000, 1000, expected_mode)
        or info.st_dev != device or not directory and info.st_nlink != 1
    ):
        raise ValueError("initial case requires existing private guest journal paths")
    return FileIdentity(info.st_dev, info.st_ino)


def _journal_paths(
    custody: GuestOperation, queue: TaskQueue, journal: AcquisitionJournal,
) -> tuple[FileIdentity, ...]:
    custody.assert_owned()
    device = STATE.lstat().st_dev
    if Path(queue.db_path) != DISPATCH or journal.db.db_path != BRIDGE:
        raise ValueError("initial case requires the fixed guest journal selection")
    if journal.db.read_only is not True:
        raise ValueError("initial case requires an already prepared read-only Bridge reader")
    identities = tuple(
        _private_path(path, directory=directory, device=device)
        for path, directory in (
            (DISPATCH.parent, True), (DISPATCH, False),
            (BRIDGE.parent, True), (BRIDGE, False),
        )
    )
    bridge = identities[-1]
    if journal.db._read_only_file_id != (bridge.device, bridge.inode):
        raise ValueError("initial case Bridge reader lost its original file")
    return identities


def _history_query(tables: tuple[str, ...]) -> str:
    return " UNION ALL ".join(
        f"SELECT '{table}' AS cohort FROM {table}" for table in tables
    ) + " LIMIT 1"


def _require_initial_journals(queue: TaskQueue, journal: AcquisitionJournal) -> None:
    started = time.monotonic()
    with queue._connect() as connection:
        connection.set_progress_handler(
            lambda: int(time.monotonic() - started >= MAX_OBSERVATION_SECONDS), 1000,
        )
        try:
            retained = connection.execute(_history_query(DISPATCH_HISTORY)).fetchone()
            if retained is None:
                for table in OPTIONAL_DISPATCH_HISTORY:
                    entry = connection.execute(
                        "SELECT type FROM sqlite_master WHERE name=?", (table,),
                    ).fetchone()
                    if entry is None:
                        continue
                    if entry[0] != "table":
                        raise ValueError("optional Dispatch history is not a supported table")
                    retained = connection.execute(_history_query((table,))).fetchone()
                    if retained is not None:
                        break
        finally:
            connection.set_progress_handler(None, 0)
    if retained is not None:
        raise ValueError("retained Dispatch work requires original-owner reconciliation; no replay")
    if time.monotonic() - started >= MAX_OBSERVATION_SECONDS:
        raise TimeoutError("initial case journal observation expired")
    if journal.db.execute_read(_history_query(BRIDGE_HISTORY)):
        raise ValueError("retained Bridge work requires original-owner reconciliation; no replay")
    if time.monotonic() - started >= MAX_OBSERVATION_SECONDS:
        raise TimeoutError("initial case journal observation expired")


def submit_initial_task(
    custody: GuestOperation, queue: TaskQueue, journal: AcquisitionJournal,
    diagnostics: DiagnosticLog,
) -> CreationOutcome:
    """Strictly observe before the existing queue's one initial synthetic create.

    This synchronous guest-only path requires separately qualified resources,
    storage monitoring, service configuration and an explicitly authorized case.
    It does not initialize services, launch a process or reconcile prior work.
    The operation context and this call must have the same execution owner.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("initial case submission must run outside an event loop")
    if (
        diagnostics.directory != DIAGNOSTICS or diagnostics.component != "reproducer"
        or diagnostics.service != "reproducer-lab"
    ):
        raise ValueError("initial case requires its existing diagnostic owner")
    identities = _journal_paths(custody, queue, journal)
    _require_initial_journals(queue, journal)
    if _journal_paths(custody, queue, journal) != identities:
        raise ValueError("initial case journals changed during observation")
    diagnostics.write_event_strict({
        "ts": datetime.now(timezone.utc).isoformat(),
        "service": "reproducer-lab", "component": "reproducer",
        "event": "initial_task_create_entry", "case": CASE,
    })
    # A successful sync is not permission to use changed journals or lost custody.
    _require_initial_journals(queue, journal)
    if _journal_paths(custody, queue, journal) != identities:
        raise ValueError("initial case journals changed before task creation")
    outcome = queue.create_outcome(
        "Offline Dispatch/Bridge reproducer",
        repo=LANE, source="reproducer-lab", dedup_key=CASE,
        prompt="Return the literal synthetic result: offline-case-complete.",
        requires=["agent:reproducer-lab"],
    )
    custody.assert_owned()
    if outcome.disposition != "created":
        raise ValueError("initial case did not create a new task; observe the retained task")
    return outcome
