"""Pool selection and claim binding inside the reservation owner's transaction."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3

from .pool_instances import PoolInstanceDefinition
from .queue_common import Task, machine_matches
from .queue_records import SpawnReservation, SpawnState, Status, TaskError
from .queue_pool_accountability import ReservationAccountability, reservation_accountable as reservation_accountable

POOL_PLACEMENT_PROTOCOL = "pool_placement/v1"
POOL_PLACEMENT_VERSION = 1


class ConfiguredPoolPlacementRequired(TaskError):
    """Fresh queued spawn admission cannot bypass a projected cohort."""

    def __init__(self) -> None:
        super().__init__("configured pool cohort requires the pool placement operation")

    def detail(self) -> dict[str, object]:
        return {
            "code": "configured_pool_placement_required",
            "reason": str(self),
            "retryable": False,
        }


def require_legacy_pool_admission(
    conn: sqlite3.Connection, task: Task, catalog_revision: int | None,
) -> None:
    """Fence fresh legacy spawning under the caller's reservation write lock."""
    if catalog_revision is not None or task.status != Status.QUEUED or task.source == "ci-repair":
        return
    revision = conn.execute(
        "SELECT cohort_revision FROM pool_catalog_clock WHERE singleton = 1",
    ).fetchone()["cohort_revision"]
    if revision > 0:
        raise ConfiguredPoolPlacementRequired()


def pool_placement_contract() -> dict[str, object]:
    """Describe explicit provider support, not automatic lane admission."""
    return {
        "protocol": POOL_PLACEMENT_PROTOCOL, "supported_versions": [POOL_PLACEMENT_VERSION],
        "supported_drivers": ["cli", "ordinary-bridge"],
        "ordinary_initial_start_versions": [1],
        "ordinary_writer_requirement_versions": [1],
        "prepared_writer_requirement_versions": [1],
        "session_origin_binding_versions": [1],
        "cli_initial_start_versions": [1],
        "instance_bound_placement_versions": [1],
        "instance_scoped_reservation_versions": [1],
        "atomic_claim_before_spawn": True, "affirmative_worker_start": True,
        "explicit_start_versions": [1],
        "suspended_recovery_versions": [1],
        "configured_cohort_reservation_fencing_versions": [1],
        "selected_legacy_history_versions": [1],
        "worker_decline_preparation_versions": [1],
        "worker_decline_local_closeout_versions": [1],
        "worker_decline_readiness_closeout_versions": [1],
        "worker_decline_binding_recovery_versions": [1],
        "worker_decline_closeout_complete": False,
        "worker_submission_readiness_versions": [1],
        "worker_submission_scope": "retained-workspace-assignments",
        "automatic_lane_migration": False, "startup_retry_policy_complete": False,
    }


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


def require_selected_instance(
    identity: tuple[str, str, str] | None, selected: PoolAssignment | PoolInstanceDefinition,
) -> None:
    """Check the global winner without changing selection or committing its claim."""
    actual = selected.registration_id, selected.machine, selected.env
    if identity is not None and identity != actual:
        raise TaskError("globally selected pool instance belongs to another launch owner; waiting")


def validate_pool_request(
    revision: int | None, reserved_by: str | None, suspended: bool,
    driver: str | None, timeout: float, instance: tuple[str, str, str] | None,
) -> None:
    if instance is not None:
        from .pool_instances import PoolDefinitionError, pool_instance_identity
        if not isinstance(instance, tuple) or len(instance) != 3:
            raise TaskError("pool placement requires an exact launch instance")
        try:
            pool_instance_identity(dict(zip(("registration_id", "machine", "env"), instance)))
        except PoolDefinitionError as exc:
            raise TaskError(str(exc)) from exc
        if revision is None:
            raise TaskError("instance-bound placement requires the pool placement operation")
        if instance[0] != reserved_by:
            raise TaskError("instance-bound placement requires its actual registration allocator")
    if revision is None:
        return
    from .queue_pool_startup import validate_startup_timeout
    validate_startup_timeout(timeout)
    if (
        type(revision) is not int or revision < 0
        or not isinstance(reserved_by, str) or not reserved_by.strip() or suspended
    ):
        raise TaskError("pool placement requires a catalog revision and explicit allocator")
    if driver not in {"cli", "ordinary-bridge"}:
        raise TaskError("pool placement requires a qualified startup driver")


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
    from .queue_pool_startup import migrate_pool_startup
    migrate_pool_startup(conn)
    from .queue_pool_decline import migrate_pool_decline
    migrate_pool_decline(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_pool_assignments)")}
    if "assignment_released" not in columns:
        conn.execute(
            "ALTER TABLE task_pool_assignments ADD COLUMN assignment_released INTEGER NOT NULL DEFAULT 0"
        )


def pool_assignment(
    conn: sqlite3.Connection, task_id: str, *, include_released: bool = False,
) -> PoolAssignment | None:
    row = conn.execute(
        "SELECT * FROM task_pool_assignments WHERE task_id = ?"
        + ("" if include_released else " AND assignment_released=0"), (task_id,),
    ).fetchone()
    return PoolAssignment(**{name: row[name] for name in PoolAssignment.__dataclass_fields__}) if row is not None else None


def _execution_excluded(
    task: Task, *, machine: str, env: str, registration_id: str,
    capabilities: frozenset[str], worktree: str | None,
) -> bool:
    from .bridge_remote import normalize_host
    tokens = {
        f"pool:{registration_id}", f"machine:{machine}", f"env:{env}",
        f"repo:{task.repo}", *capabilities,
    }
    if worktree is not None:
        tokens.add(f"worktree:{worktree}")
    return bool(tokens.intersection(task.excludes)) or any(
        token.startswith("machine:")
        and normalize_host(token.removeprefix("machine:")) == normalize_host(machine)
        for token in task.excludes
    )


def _permits(pool: PoolInstanceDefinition, task: Task, *, worktree: str | None = None) -> bool:
    """Whether ``pool`` may run ``task``, in ``worktree`` when one is recorded."""
    capabilities = pool.filters.permit.get("capabilities", frozenset())
    worktree = worktree or task.target_worktree or task.affinity.get("worktree")
    hard = _routing_requirements(
        task, machine=pool.machine, env=pool.env, registration_id=pool.registration_id,
        capabilities=capabilities, worktree=worktree,
    )
    if hard is None:
        return False
    if not machine_matches(task.target_machine, pool.machine):
        return False
    if _execution_excluded(
        task, machine=pool.machine, env=pool.env, registration_id=pool.registration_id,
        capabilities=capabilities, worktree=worktree,
    ):
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


def pool_lane_load(
    conn: sqlite3.Connection, identity: tuple[str, str, str], *,
    accounting: ReservationAccountability | None = None,
    ahead_of: tuple[float, str] | None = None,
    cutoff: int | None = None,
    exclude_key: str | None = None,
    exclude_waiter: str | None = None,
) -> int:
    """Lanes held by running bodies plus suspended reactivations waiting for one.

    A cold (parked) body holds no lane until its reactivation is in flight or a
    wake send to it is unresolved. Waiting reactivations take freed lanes before
    fresh placements, in the order they asked; ``ahead_of`` counts only the
    waiters before that one. Classification stops once the load reaches
    ``cutoff``, so a saturated pool never classifies its remaining history.
    A waiter counts only while its reservation is still cold; once its body
    runs, the reservation itself holds the lane.
    A body asking for its own lane omits only its waking reservation
    (``exclude_key``) and its own waiter entry (``exclude_waiter``); older
    accountable attempts of the same task still count.
    """
    waiting = conn.execute(
        "SELECT a.task_id, a.lane_wait_since FROM task_pool_assignments a"
        " JOIN tasks t ON t.id = a.task_id"
        " JOIN spawn_reservations s ON s.key = a.reservation_key AND s.state = ?"
        " AND s.release_requested = 0 AND s.startup_fenced = 0 AND s.conversation_retired = 0"
        " WHERE a.registration_id = ? AND a.machine = ? AND a.env = ?"
        " AND a.assignment_released = 0 AND a.lane_wait_since IS NOT NULL"
        " AND a.recovery_requested_at IS NULL AND t.status = ? AND t.resume_requested = 1"
        " AND t.hold_reason IS NULL AND t.awaiting_steer = 0",
        (SpawnState.COLD, *identity, Status.SUSPENDED),
    ).fetchall()
    load = sum(
        1 for row in waiting
        if row["task_id"] != exclude_waiter
        and (ahead_of is None or (row["lane_wait_since"], row["task_id"]) < ahead_of)
    )
    if cutoff is not None and load >= cutoff:
        return load
    accounting = accounting or ReservationAccountability(conn)
    for row in conn.execute(
        "SELECT s.*, t.recovery_requested_at AS lane_recovery_requested_at"
        " FROM pool_reservation_bindings a"
        " JOIN spawn_reservations s ON s.key = a.reservation_key"
        " LEFT JOIN task_pool_assignments t ON t.reservation_key = s.key AND t.assignment_released = 0"
        " WHERE a.registration_id = ? AND a.machine = ? AND a.env = ?",
        identity,
    ):
        if exclude_key is not None and row["key"] == exclude_key:
            continue
        if row["state"] == SpawnState.COLD and row["lane_recovery_requested_at"] is None:
            load += accounting.send_unresolved(row)
        else:
            load += accounting.accountable(row)
        if cutoff is not None and load >= cutoff:
            return load
    return load


def select_pool(
    conn: sqlite3.Connection, task: Task, definitions: tuple[PoolInstanceDefinition, ...],
    *, execution_machine: str | None = None,
) -> PoolInstanceDefinition:
    """Recheck retained routing and count accountable reservations, never caller slots."""
    accounting = ReservationAccountability(conn)
    legacy = conn.execute(
        "SELECT s.* FROM spawn_reservations s"
        " LEFT JOIN pool_reservation_bindings a ON a.reservation_key = s.key"
        " WHERE a.reservation_key IS NULL",
    ).fetchall()
    if any(accounting.accountable(row) for row in legacy):
        raise TaskError("pool placement requires reconciliation of unaccounted legacy capacity")
    retained = pool_assignment(conn, task.id)
    candidates = definitions
    if execution_machine is not None:
        from .bridge_remote import normalize_host
        candidates = tuple(
            pool for pool in candidates
            if normalize_host(pool.machine) == normalize_host(execution_machine)
        )
    if retained is not None:
        candidates = tuple(
            pool for pool in candidates
            if pool.identity == (retained.registration_id, retained.machine, retained.env)
        )
        if not candidates or not _permits(candidates[0], task):
            raise TaskError("retained pool assignment is unavailable or routing is unsatisfiable")
    for pool in candidates:
        if not _permits(pool, task):
            continue
        if pool_lane_load(
            conn, pool.identity, accounting=accounting, cutoff=pool.max_concurrent,
        ) < pool.max_concurrent:
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
    accounting = ReservationAccountability(conn)
    if any(accounting.accountable(holder) for holder in holders):
        raise TaskError("exclusive key has accountable historical execution; placement is waiting")


def require_pool_recovery_retirement(conn: sqlite3.Connection, task: Task) -> None:
    """Recovery cannot manufacture absence for an original pool-bound attempt."""
    require_historical_exclusivity(conn, task)
    rows = conn.execute(
        "SELECT s.* FROM spawn_reservations s"
        " JOIN pool_reservation_bindings p ON p.reservation_key = s.key"
        " WHERE s.task_id = ?", (task.id,),
    ).fetchall()
    accounting = ReservationAccountability(conn)
    if any(accounting.accountable(row) for row in rows):
        raise TaskError("pool recovery requires retirement of accountable original reservations")


def record_pool_claim(
    conn: sqlite3.Connection, task: Task, pool: PoolInstanceDefinition,
    key: str, catalog_revision: int, *, ts: float, startup_timeout: float = 600,
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
        "INSERT INTO task_pool_assignments"
        " (task_id, registration_id, machine, env, reservation_key, task_generation, catalog_revision)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(task_id) DO UPDATE SET reservation_key = excluded.reservation_key,"
        " task_generation = excluded.task_generation, catalog_revision = excluded.catalog_revision,"
        " registration_id=excluded.registration_id, machine=excluded.machine, env=excluded.env,"
        " assignment_released=0",
        (task.id, *pool.identity, key, task.generation + 1, catalog_revision),
    )
    from .queue_pool_startup import begin_pool_episode
    begin_pool_episode(conn, task.id, ts, startup_timeout)


def require_pool_workspace(
    conn: sqlite3.Connection, task: Task, worktree: str, execution_machine: object,
    *, driver: object, reservation_key: str | None = None,
) -> None:
    assignment = pool_assignment(conn, task.id)
    if assignment is None:
        if reservation_key is not None:
            _require_accounting_workspace(conn, task, reservation_key, worktree, execution_machine)
        return
    if driver not in {"cli", "ordinary-bridge"}:
        raise TaskError("pool workspace requires a qualified startup driver")
    from .bridge_remote import normalize_host
    if (
        not isinstance(execution_machine, str) or not execution_machine.strip()
        or normalize_host(execution_machine) != normalize_host(assignment.machine)
        or not machine_matches(task.target_machine, assignment.machine)
    ):
        raise TaskError("pool workspace requires its recorded execution machine")
    binding = conn.execute(
        "SELECT capabilities FROM pool_reservation_bindings WHERE reservation_key = ?",
        (assignment.reservation_key,),
    ).fetchone()
    if binding is None or binding["capabilities"] is None:
        raise TaskError("pool workspace capability binding requires reconciliation")
    conflict = bound_workspace_conflict(
        task, worktree, execution_machine, machine=assignment.machine, env=assignment.env,
        registration_id=assignment.registration_id,
        capabilities=frozenset(json.loads(binding["capabilities"])),
    )
    if conflict is not None:
        raise TaskError(conflict)
    _require_bound_filters(
        conn, task, worktree, (assignment.registration_id, assignment.machine, assignment.env),
    )


def _require_accounting_workspace(
    conn: sqlite3.Connection, task: Task, reservation_key: str, worktree: str | None,
    execution_machine: object,
) -> None:
    # An accounting-only (adopted) binding has no assignment; its immutable
    # binding still decides where the body may record or publish a workspace.
    bound = conn.execute(
        "SELECT * FROM pool_reservation_bindings WHERE reservation_key = ?", (reservation_key,),
    ).fetchone()
    if bound is None:
        return
    row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (reservation_key,)).fetchone()
    hosts = reservation_hosts(row, task, execution_machine, worktree=worktree)
    if len(hosts) != 1:
        raise TaskError("pool workspace requires its recorded execution machine")
    conflict = bound_workspace_conflict(
        task, worktree, next(iter(hosts)), machine=bound["machine"], env=bound["env"],
        registration_id=bound["registration_id"],
        capabilities=frozenset(json.loads(bound["capabilities"] or "[]")),
    )
    if conflict is not None:
        raise TaskError(conflict)
    _require_bound_filters(
        conn, task, worktree, (bound["registration_id"], bound["machine"], bound["env"]),
    )


def _require_bound_filters(
    conn: sqlite3.Connection, task: Task, worktree: str | None, identity: tuple[str, str, str],
) -> None:
    """The bound pool's own permit/reject filters must accept the workspace."""
    from .queue_pool_catalog import QueuePoolCatalogMixin
    definition = next((
        value
        for row in conn.execute(
            "SELECT * FROM pool_publication_scopes WHERE machine = ? AND env = ?", identity[1:],
        )
        for value in QueuePoolCatalogMixin._pool_scope_from_row(row).definitions
        if value.identity == identity
    ), None)
    if definition is None:
        from .queue_execution_custody import PoolLaneUnavailable
        raise PoolLaneUnavailable("pool workspace's bound pool is disabled or unavailable")
    if not _permits(definition, task, worktree=worktree):
        raise TaskError("pool workspace is refused by its bound pool filters")


def reservation_hosts(
    row: sqlite3.Row, task: Task | None, execution_machine: object = None,
    *, worktree: str | None = None,
) -> set[str]:
    """Normalized hosts the reservation's own evidence names for its body.

    The recorded execution machine (or a fleet-body handle's host) is
    authoritative; a body without either ran where its reservation was created.
    The owner's host counts when that owner is this body's worktree. Several
    hosts mean conflicting evidence; none means the host is unknown.
    """
    from .bridge_remote import normalize_host
    hosts: set[str] = set()
    recorded = execution_machine or row["execution_machine"]
    if isinstance(recorded, str) and recorded.strip():
        hosts.add(normalize_host(recorded))
    handle = row["session_handle"] or ""
    if handle.startswith("fleet-body:") and handle.split(":")[1]:
        hosts.add(normalize_host(handle.split(":")[1]))
    if not hosts and isinstance(row["creating_host"], str) and row["creating_host"].strip():
        hosts.add(normalize_host(row["creating_host"]))
    owner_host, separator, owner_worktree = (task.owner or "").partition("/") if task else ("", "", "")
    if separator and owner_host and owner_worktree == (worktree or row["worktree"]):
        hosts.add(normalize_host(owner_host))
    return hosts


def bound_workspace_conflict(
    task: Task, worktree: str | None, execution_machine: object, *,
    machine: str, env: str, registration_id: str, capabilities: frozenset[str],
) -> str | None:
    """Why a recorded workspace cannot run in a bound pool under current task routing."""
    from .bridge_remote import normalize_host
    if (
        isinstance(execution_machine, str) and execution_machine.strip()
        and normalize_host(execution_machine) != normalize_host(machine)
        or not machine_matches(task.target_machine, machine)
    ):
        return "pool workspace requires its recorded execution machine"
    if task.target_worktree is not None and task.target_worktree != worktree:
        return "pool workspace conflicts with the task target worktree"
    if _routing_requirements(
        task, machine=machine, env=env, registration_id=registration_id,
        capabilities=capabilities, worktree=worktree,
    ) is None:
        return "pool workspace conflicts with current task requirements"
    if _execution_excluded(
        task, machine=machine, env=env, registration_id=registration_id,
        capabilities=capabilities, worktree=worktree,
    ):
        return "pool workspace is excluded by current task routing"
    return None


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
            reservation_key=row["key"],
        )
    from .ordinary_native_bindings import native_binding_evidence, reservation_native_identity
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
    if (
        releasing and previous is None
        and row["allocation_reservation_key"] in (None, row["key"])
        and not row["session_handle"] and not row["agent_info"]
        and row["release_requested"] and not row["native_release_pending"]
        and (row["startup_complete"] or row["startup_fenced"])
    ):
        # Qualified absence for a handleless root cannot retire a first
        # native that was never recorded. Retain the late return for release.
        return bool(row["conversation_retired"])
    evidence = native_binding_evidence(
        conn, row, task, require_owned_workspace=False,
        reject_pending_native=None if releasing else native,
    )
    if native is not None and native in evidence.retired:
        raise TaskError("pool publication cannot revive a retired native")
    if row["conversation_retired"]:
        if (
            not (evidence.retired or evidence.qualified_empty)
            or native is None or row["session_handle"] or row["agent_info"]
        ):
            raise TaskError("pool publication requires a distinct proven replacement native")
        return True
    return False


def pool_worker_matches(worker_id: str | None, machine: str, worktree: str | None) -> bool:
    """Match the normalized host while retaining the exact worktree identity."""
    from .bridge_remote import normalize_host

    if not isinstance(worker_id, str):
        return False
    host, separator, worker_worktree = worker_id.partition("/")
    return bool(separator) and worker_worktree == worktree and (
        normalize_host(host) == normalize_host(machine)
    )


def require_pool_start(
    conn: sqlite3.Connection, task: Task, worker_id: str | None,
    native_id: object, expected_generation: int | None,
    *, declining: bool = False,
) -> bool:
    """Only an explicit current worker may accept a placed claim; delivery cannot."""
    assignment = pool_assignment(conn, task.id)
    if assignment is None:
        return False
    from .queue_task_relationships import require_relationship
    require_relationship(conn, task.id)
    if not declining:
        from .queue_pool_decline import require_no_pool_decline
        require_no_pool_decline(conn, task.id)
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
        or not pool_worker_matches(worker_id, assignment.machine, row["worktree"])
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
