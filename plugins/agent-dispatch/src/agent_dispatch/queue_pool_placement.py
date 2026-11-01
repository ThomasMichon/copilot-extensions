"""Pool selection and claim binding inside the reservation owner's transaction."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from .pool_instances import PoolInstanceDefinition
from .queue_common import Task, machine_matches, worker_id_for
from .queue_records import SpawnReservation, SpawnState, Status, TaskError

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


@dataclass(frozen=True)
class PoolPlacement:
    reservation: SpawnReservation
    reserved: bool
    assignment: PoolAssignment


_INTRINSIC_DIMS = frozenset({"machine", "repo", "env", "pool", "worktree"})


def _routing_requirements(
    task: Task, *, machine: str, env: str, registration_id: str,
    capabilities: frozenset[str], worktree: str | None,
) -> set[str] | None:
    """Separate real execution selectors from explicitly supplied capabilities."""
    intrinsic = {
        "machine": machine, "repo": task.repo, "env": env,
        "pool": registration_id, "worktree": worktree,
    }
    hard: set[str] = set()
    for token in task.requires:
        dim, separator, value = token.partition(":")
        if separator and dim in _INTRINSIC_DIMS:
            actual = intrinsic[dim]
            matches = machine_matches(value, actual) if dim == "machine" else value == actual
            if actual is None or not matches:
                return None
        else:
            hard.add(token)
    return hard if hard.issubset(capabilities) else None


def migrate_pool_placement(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_pool_assignments ("
        " task_id TEXT PRIMARY KEY, registration_id TEXT NOT NULL,"
        " machine TEXT NOT NULL, env TEXT NOT NULL, reservation_key TEXT NOT NULL UNIQUE,"
        " task_generation INTEGER NOT NULL, catalog_revision INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pool_reservation_bindings ("
        " reservation_key TEXT PRIMARY KEY, registration_id TEXT NOT NULL,"
        " machine TEXT NOT NULL, env TEXT NOT NULL, capabilities TEXT)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO pool_reservation_bindings"
        " (reservation_key, registration_id, machine, env)"
        " SELECT reservation_key, registration_id, machine, env FROM task_pool_assignments"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pool_reservation_identity"
        " ON pool_reservation_bindings(registration_id, machine, env)"
    )


def pool_assignment(conn: sqlite3.Connection, task_id: str) -> PoolAssignment | None:
    row = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id = ?", (task_id,),
    ).fetchone()
    return PoolAssignment(**dict(row)) if row is not None else None


def _permits(pool: PoolInstanceDefinition, task: Task) -> bool:
    capabilities = pool.filters.permit.get("capabilities", frozenset())
    worktree = task.target_worktree or task.affinity.get("worktree")
    hard = _routing_requirements(
        task, machine=pool.machine, env=pool.env, registration_id=pool.registration_id,
        capabilities=capabilities, worktree=worktree,
    )
    if hard is None:
        return False
    if not machine_matches(task.target_machine, pool.machine):
        return False
    tokens = {
        f"pool:{pool.registration_id}", f"machine:{pool.machine}", f"env:{pool.env}",
        f"repo:{task.repo}", *capabilities,
    }
    if tokens.intersection(task.excludes):
        return False
    if worktree and f"worktree:{worktree}" in task.excludes:
        return False
    attrs: dict[str, object] = {
        "repo": task.repo, "machine": pool.machine, "env": pool.env,
        "worktree": worktree, "capabilities": sorted(hard),
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


def reservation_accountable(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Terminal bookkeeping never substitutes for native or transport retirement."""
    if row["state"] in SpawnState.ACTIVE or row["native_release_pending"]:
        return True
    from .queue_execution_custody import reservation_transport_accountable
    if reservation_transport_accountable(conn, row):
        return True
    from .ordinary_native_bindings import is_native_retired, native_bindings, reservation_native_identity
    if not (row["startup_complete"] or row["startup_fenced"]):
        return True
    native = reservation_native_identity(conn, row)
    if native:
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["task_id"],)).fetchone()
        if task_row is None:
            raise TaskError("pool capacity has an orphaned reservation; reconcile its execution custody")
        task = Task._from_row(task_row)
        _, retired = native_bindings(conn, row, task, require_owned_workspace=False)
        return native not in retired
    if is_native_retired(row):
        return False
    return bool(row["session_handle"])


def select_pool(
    conn: sqlite3.Connection, task: Task, definitions: tuple[PoolInstanceDefinition, ...],
) -> PoolInstanceDefinition:
    """Recheck retained routing and count accountable reservations, never caller slots."""
    legacy = conn.execute(
        "SELECT s.* FROM spawn_reservations s"
        " LEFT JOIN pool_reservation_bindings a ON a.reservation_key = s.key"
        " WHERE a.reservation_key IS NULL",
    ).fetchall()
    if any(reservation_accountable(conn, row) for row in legacy):
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
        reservations = conn.execute(
            "SELECT s.* FROM pool_reservation_bindings a"
            " JOIN spawn_reservations s ON s.key = a.reservation_key"
            " WHERE a.registration_id = ? AND a.machine = ? AND a.env = ?",
            pool.identity,
        ).fetchall()
        count = sum(reservation_accountable(conn, row) for row in reservations)
        if count < pool.max_concurrent:
            return pool
    if retained is not None:
        raise TaskError("retained pool assignment is waiting for capacity")
    raise TaskError("no eligible pool instance has capacity")


def require_historical_exclusivity(conn: sqlite3.Connection, task: Task) -> None:
    """A fresh reservation cannot overtake an accountable historical holder."""
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id = ? "
        "AND native_release_pending = 1 LIMIT 1", (task.id,),
    ).fetchone():
        raise TaskError("task has pending historical native retirement; placement is waiting")
    if task.exclusive_key is None:
        return
    holders = conn.execute(
        "SELECT s.* FROM spawn_reservations s "
        "JOIN pool_reservation_bindings p ON p.reservation_key = s.key "
        "WHERE s.exclusive_key = ? AND s.task_id <> ? AND s.exclusive_released = 0",
        (task.exclusive_key, task.id),
    ).fetchall()
    if any(reservation_accountable(conn, holder) for holder in holders):
        raise TaskError("exclusive key has accountable historical execution; placement is waiting")


def require_pool_recovery_retirement(conn: sqlite3.Connection, task: Task) -> None:
    """Recovery cannot manufacture absence for an original pool-bound attempt."""
    require_historical_exclusivity(conn, task)
    rows = conn.execute(
        "SELECT s.* FROM spawn_reservations s"
        " JOIN pool_reservation_bindings p ON p.reservation_key = s.key"
        " WHERE s.task_id = ?", (task.id,),
    ).fetchall()
    if any(reservation_accountable(conn, row) for row in rows):
        raise TaskError("pool recovery requires retirement of accountable original reservations")


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
        "INSERT INTO pool_reservation_bindings VALUES (?, ?, ?, ?, ?)",
        (key, *pool.identity, json.dumps(sorted(pool.filters.permit.get("capabilities", frozenset())))),
    )
    conn.execute(
        "INSERT INTO task_pool_assignments VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(task_id) DO UPDATE SET reservation_key = excluded.reservation_key,"
        " task_generation = excluded.task_generation, catalog_revision = excluded.catalog_revision",
        (task.id, *pool.identity, key, task.generation + 1, catalog_revision),
    )


def require_pool_workspace(
    conn: sqlite3.Connection, task: Task, worktree: str, execution_machine: object,
    *, driver: object,
) -> None:
    assignment = pool_assignment(conn, task.id)
    if assignment is None:
        return
    if driver != "cli":
        raise TaskError("pool workspace supports only the qualified cli startup driver")
    from .bridge_remote import normalize_host
    if (
        not isinstance(execution_machine, str) or not execution_machine.strip()
        or normalize_host(execution_machine) != normalize_host(assignment.machine)
        or not machine_matches(task.target_machine, assignment.machine)
    ):
        raise TaskError("pool workspace requires its recorded execution machine")
    if task.target_worktree is not None and task.target_worktree != worktree:
        raise TaskError("pool workspace conflicts with the task target worktree")
    binding = conn.execute(
        "SELECT capabilities FROM pool_reservation_bindings WHERE reservation_key = ?",
        (assignment.reservation_key,),
    ).fetchone()
    if binding is None or binding["capabilities"] is None:
        raise TaskError("pool workspace capability binding requires reconciliation")
    capabilities = frozenset(json.loads(binding["capabilities"]))
    if _routing_requirements(
        task, machine=assignment.machine, env=assignment.env,
        registration_id=assignment.registration_id, capabilities=capabilities, worktree=worktree,
    ) is None:
        raise TaskError("pool workspace conflicts with current task requirements")
    if {
        f"worktree:{worktree}", f"pool:{assignment.registration_id}",
        f"machine:{assignment.machine}", f"env:{assignment.env}",
        f"repo:{task.repo}", *capabilities,
    }.intersection(task.excludes):
        raise TaskError("pool workspace is excluded by current task routing")


def pool_native_publication(
    conn: sqlite3.Connection, task: Task, row: sqlite3.Row, session_handle: str,
    *, releasing: bool = False,
) -> bool:
    """Clear only inherited retirement when publishing a distinct live native."""
    if conn.execute(
        "SELECT 1 FROM pool_reservation_bindings WHERE reservation_key = ?", (row["key"],),
    ).fetchone() is None:
        return False
    if not releasing and not row["worktree"]:
        raise TaskError("pool publication requires its recorded workspace")
    if not releasing:
        require_pool_workspace(
            conn, task, row["worktree"], row["execution_machine"], driver=row["driver"],
        )
    from .ordinary_native_bindings import native_bindings, reservation_native_identity
    candidate = {**dict(row), "session_handle": session_handle, "conversation_retired": 0}
    native = reservation_native_identity(conn, candidate)
    previous = reservation_native_identity(conn, row)
    if (
        previous is not None and previous != native
        or releasing and row["session_handle"] and row["session_handle"] != session_handle
    ):
        raise TaskError("pool publication cannot replace its retained native identity")
    if releasing and previous is not None and previous == native:
        return False
    _, retired = native_bindings(
        conn, row, task, require_owned_workspace=False,
        reject_pending_native=None if releasing else native,
    )
    if native is not None and native in retired:
        raise TaskError("pool publication cannot revive a retired native")
    if row["conversation_retired"]:
        if not retired or native is None or row["session_handle"] or row["agent_info"]:
            raise TaskError("pool publication requires a distinct proven replacement native")
        return True
    return False


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
    require_pool_workspace(conn, task, row["worktree"], row["execution_machine"], driver=row["driver"])
    from .ordinary_native_bindings import native_bindings, reservation_native_identity
    if reservation_native_identity(conn, row) != native_id:
        raise TaskError("pool start native does not match retained startup identity")
    _, retired = native_bindings(
        conn, row, task, require_owned_workspace=False, reject_pending_native=native_id,
    )
    if native_id in retired:
        raise TaskError("pool start cannot accept a retired native")
    return True
