"""Pool selection and claim binding inside the reservation owner's transaction."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from .pool_instances import PoolInstanceDefinition
from .queue_common import Task, machine_matches, worker_id_for
from .queue_records import SpawnState, Status, TaskError

POOL_PLACEMENT_PROTOCOL = "pool_placement/v1"
POOL_PLACEMENT_VERSION = 1


@dataclass(frozen=True)
class PoolAssignment:
    task_id: str
    registration_id: str
    machine: str
    env: str
    reservation_key: str
    task_generation: int
    catalog_revision: int


def migrate_pool_placement(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_pool_assignments ("
        " task_id TEXT PRIMARY KEY, registration_id TEXT NOT NULL,"
        " machine TEXT NOT NULL, env TEXT NOT NULL, reservation_key TEXT NOT NULL UNIQUE,"
        " task_generation INTEGER NOT NULL, catalog_revision INTEGER NOT NULL)"
    )


def pool_assignment(conn: sqlite3.Connection, task_id: str) -> PoolAssignment | None:
    row = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id = ?", (task_id,),
    ).fetchone()
    return PoolAssignment(**dict(row)) if row is not None else None


def _permits(pool: PoolInstanceDefinition, task: Task) -> bool:
    if not set(task.requires).issubset(pool.filters.permit.get("capabilities", frozenset())):
        return False
    if not machine_matches(task.target_machine, pool.machine):
        return False
    tokens = {
        f"pool:{pool.registration_id}", f"machine:{pool.machine}", f"env:{pool.env}",
    }
    if tokens.intersection(task.excludes):
        return False
    if task.target_worktree and f"worktree:{task.target_worktree}" in task.excludes:
        return False
    attrs: dict[str, object] = {
        "repo": task.repo, "machine": task.target_machine,
        "worktree": task.target_worktree, "capabilities": task.requires,
    }
    for dim in ("env", "role"):
        values = {token.split(":", 1)[1] for token in task.requires if token.startswith(dim + ":")}
        if len(values) > 1:
            return False
        if values:
            attrs[dim] = next(iter(values))
    if attrs.get("env") not in (None, pool.env):
        return False
    rejected_types = pool.filters.reject.get("task-type", frozenset())
    if rejected_types.intersection(task.labels):
        return False
    if task.labels:
        return any(pool.filters.permits({**attrs, "task-type": label}) for label in task.labels)
    return pool.filters.permits(attrs)


def select_pool(
    conn: sqlite3.Connection, task: Task, definitions: tuple[PoolInstanceDefinition, ...],
) -> PoolInstanceDefinition:
    """Recheck retained routing and count accountable reservations, never caller slots."""
    legacy = conn.execute(
        "SELECT s.key FROM spawn_reservations s"
        " LEFT JOIN task_pool_assignments a ON a.reservation_key = s.key"
        " WHERE s.state IN (?, ?, ?, ?) AND a.task_id IS NULL LIMIT 1",
        tuple(SpawnState.ACTIVE),
    ).fetchone()
    if legacy is not None:
        raise TaskError("pool placement requires reconciliation of unaccounted legacy capacity")
    retained = pool_assignment(conn, task.id)
    candidates = definitions
    if retained is not None:
        candidates = tuple(
            pool for pool in definitions
            if pool.identity == (retained.registration_id, retained.machine, retained.env)
        )
        if not candidates or not _permits(candidates[0], task):
            raise TaskError("retained pool assignment is unavailable or routing is unsatisfiable")
    for pool in candidates:
        if not _permits(pool, task):
            continue
        count = conn.execute(
            "SELECT COUNT(*) FROM task_pool_assignments a"
            " JOIN spawn_reservations s ON s.key = a.reservation_key"
            " WHERE a.registration_id = ? AND a.machine = ? AND a.env = ?"
            " AND s.state IN (?, ?, ?, ?)",
            (*pool.identity, *SpawnState.ACTIVE),
        ).fetchone()[0]
        if count < pool.max_concurrent:
            return pool
    if retained is not None:
        raise TaskError("retained pool assignment is waiting for capacity")
    raise TaskError("no eligible pool instance has capacity")


def record_pool_claim(
    conn: sqlite3.Connection, task: Task, pool: PoolInstanceDefinition,
    key: str, catalog_revision: int, *, ts: float,
) -> None:
    """Bind one slot and the allocator's claim before any external spawn effect."""
    changed = conn.execute(
        "UPDATE tasks SET status = ?, claimed_at = ?, updated_at = ?,"
        " generation = generation + 1, attempts = attempts + 1,"
        " owner = NULL, owner_session_id = NULL, lease_expires_at = NULL,"
        " last_seen_at = NULL, last_liveness = NULL, activity = NULL"
        " WHERE id = ? AND status = ? AND owner IS NULL AND generation = ?",
        (Status.CLAIMED, ts, ts, task.id, Status.QUEUED, task.generation),
    ).rowcount
    if changed != 1:
        raise TaskError("pool placement task incarnation changed")
    conn.execute(
        "INSERT INTO task_pool_assignments VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(task_id) DO UPDATE SET reservation_key = excluded.reservation_key,"
        " task_generation = excluded.task_generation, catalog_revision = excluded.catalog_revision",
        (task.id, *pool.identity, key, task.generation + 1, catalog_revision),
    )


def require_pool_workspace(
    conn: sqlite3.Connection, task: Task, worktree: str, creating_host: object,
) -> None:
    assignment = pool_assignment(conn, task.id)
    if assignment is None:
        return
    from .bridge_remote import normalize_host
    if (
        not isinstance(creating_host, str) or not creating_host.strip()
        or normalize_host(creating_host) != normalize_host(assignment.machine)
        or not machine_matches(task.target_machine, assignment.machine)
    ):
        raise TaskError("pool workspace requires its recorded execution machine")
    if task.target_worktree is not None and task.target_worktree != worktree:
        raise TaskError("pool workspace conflicts with the task target worktree")
    if {
        f"worktree:{worktree}", f"pool:{assignment.registration_id}",
        f"machine:{assignment.machine}", f"env:{assignment.env}",
    }.intersection(task.excludes):
        raise TaskError("pool workspace is excluded by current task routing")


def require_pool_start(
    conn: sqlite3.Connection, task: Task, worker_id: str | None,
    native_id: object, expected_generation: int | None,
) -> bool:
    """Only an explicit current worker may accept a placed claim; delivery cannot."""
    assignment = pool_assignment(conn, task.id)
    if assignment is None:
        return False
    row = conn.execute(
        "SELECT * FROM spawn_reservations WHERE key = ?", (assignment.reservation_key,),
    ).fetchone()
    if (
        row is None or row["task_id"] != task.id
        or row["state"] not in {SpawnState.RESERVING, SpawnState.SPAWNED}
        or row["startup_fenced"] or row["release_requested"]
        or row["conversation_retired"] or row["cooling_token"] is not None
        or row["conclusion_state"] == "pending" or not row["worktree"]
        or assignment.task_generation != task.generation
        or type(expected_generation) is not int
        or expected_generation != task.generation
        or not isinstance(native_id, str) or not native_id
        or worker_id != worker_id_for(assignment.machine, row["worktree"])
        or task.owner not in (None, worker_id)
    ):
        raise TaskError("pool start requires the current reservation, worker and native incarnation")
    require_pool_workspace(conn, task, row["worktree"], row["creating_host"])
    from .ordinary_native_bindings import reservation_native_identity
    if reservation_native_identity(conn, row) != native_id:
        raise TaskError("pool start native does not match retained startup identity")
    return True
