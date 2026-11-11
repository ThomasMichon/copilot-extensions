"""SQLite journal and kernel-custodied finite worker."""

from __future__ import annotations

import hashlib
import codecs
import json
import importlib
import math
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from single_instance_lease.operation import OperationLock, OperationLockedError
from . import _invocation, authority, boot, foundation_state
from ._directory import create_queue_directory

SCHEMA = 4
STATES = frozenset({"pending", "running", "completed", "interrupted", "blocked"})
_active_transaction = ContextVar("update_driver_active_transaction", default=None)


class DriverError(RuntimeError):
    """Installation driver failure; never a successful update."""


class InvalidRequestError(DriverError):
    """The safe request or trusted invocation is invalid."""


class StateError(DriverError):
    """Unsupported, malformed or inaccessible durable state."""


class RequestNotFoundError(DriverError):
    """No durable request has this identifier."""


class SpawnError(DriverError):
    """A caller-independent worker could not be launched."""


class InterruptedError(DriverError):
    """Uncertain work fences further installation."""


@dataclass(frozen=True)
class RequestSpec:
    """Credential-free normalized request identity, supplied by a product adapter."""

    backend: str
    argv: tuple[str, ...] = ()
    project: str = ""
    cwd: str = ""
    source: str = ""
    context: tuple[tuple[str, str], ...] = ()
    repair: bool = False
    killswitch_path: str = ""
    authority: str = ""


@dataclass(frozen=True)
class TrustedRunner:
    """Internal pinned invocation, never constructed from an arbitrary CLI command."""

    argv: tuple[str, ...]
    cwd: str
    timeout: float = 7200
    env: tuple[tuple[str, str], ...] = ()
    context_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class Request:
    id: str
    spec: RequestSpec
    runner: TrustedRunner
    state: str
    exit_code: int | None
    error: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    log_path: Path
    boot_id: str | None = None
    settled_boot_id: str | None = None
    settled_at: float | None = None
    settlement_kind: str | None = None
    recovery_authority: str | None = None
    released_at: float | None = None

    @property
    def unresolved(self) -> bool:
        return (
            (
                self.state in {"running", "interrupted", "blocked"}
                or (self.state == "pending" and any(
                    value is not None for value in (self.started_at, self.finished_at, self.boot_id)
                ))
            )
            and self.settled_at is None and self.released_at is None
        )


def default_root() -> Path:
    """Per-user execution-space root; no ambient root override is trusted."""
    return Path.home() / ".copilot-extensions" / "update-driver"


def _root(root: Path | None) -> Path:
    path = Path(root) if root is not None else default_root()
    if not path.is_absolute():
        raise StateError("driver root must be absolute")
    # Never let a changed junction/symlink silently select another execution space.
    if path.resolve() != path:
        raise StateError("driver root must not traverse a symlink or reparse point")
    return path


def _text(value: object) -> str:
    if not isinstance(value, str) or "\0" in value or len(value) > 32768:
        raise InvalidRequestError("request fields must be bounded NUL-free strings")
    return value


def _vector(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 512:
        raise InvalidRequestError("argument vector must contain at most 512 strings")
    return tuple(_text(item) for item in value)


def _spec(spec: RequestSpec) -> RequestSpec:
    return _invocation.validate_spec(spec)


def _spec_document(spec: RequestSpec) -> dict:
    document = asdict(spec)
    if not spec.repair:
        # Preserve identities and downlevel readability of ordinary queued jobs.
        document.pop("repair")
    if not spec.killswitch_path:
        document.pop("killswitch_path")
    if not spec.authority:
        document.pop("authority")
    return document


def automatic_update_refusal(spec: RequestSpec) -> str | None:
    """Read applicable automation vetoes; explicit updates never consult them."""
    # Older Manager/engine update requests retained flags without the verb.
    update = spec.argv[:1] == ("update",) or spec.backend in {
        "manager", "engine", "engine-launch-update",
    }
    if not update or "--auto" not in spec.argv:
        return None
    context = dict(spec.context)
    cell = bool(context.get("COPILOT_EXTENSIONS_CONTEXT", "").strip())
    manager_root = context.get("WORKTREE_MANAGER_ROOT")
    if not cell or manager_root or spec.backend == "manager":
        try:
            root = Path(manager_root).expanduser() if manager_root else Path.home() / ".worktree-manager"
            if not root.is_absolute():
                if not spec.cwd or not Path(spec.cwd).is_absolute():
                    return "Manager automatic-maintenance policy requires an absolute request cwd; nothing ran"
                root = Path(spec.cwd) / root
                if not root.is_absolute():
                    return "Manager automatic-maintenance policy root cannot resolve against request cwd; nothing ran"
            path = root.resolve() / "config.toml"
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = None
        except (OSError, UnicodeError, RuntimeError) as error:
            return f"Manager automatic-maintenance policy unavailable: {error}; nothing ran"
        if content is not None:
            try:
                parser = importlib.import_module("tomllib" if sys.version_info >= (3, 11) else "tomli")
                policy = parser.loads(content).get("maintenance", {})
                if (
                    not isinstance(policy, dict) or policy.keys() - {"automatic"}
                    or type(policy.get("automatic", True)) is not bool
                ):
                    raise ValueError("maintenance.automatic must be a boolean")
                if not policy.get("automatic", True):
                    return "Manager maintenance.automatic is false; automatic update not run"
            except (ModuleNotFoundError, ValueError) as error:
                return f"Manager automatic-maintenance policy invalid or unsupported: {error}; nothing ran"
    if cell or (spec.backend == "engine-launch-update" and "--no-update" in spec.argv):
        return None
    override = spec.killswitch_path
    try:
        base = Path.home() / ".copilot-extensions"
        path = Path(override).expanduser() if override else base / "killSwitches" / "bootstrap-killswitch.json"
        path = path.parent.resolve() / path.name
        if not override and not path.exists():
            path = base / "bootstrap-killswitch.json"
        with path.open("rb") as stream:
            data = stream.read(65537)
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError) as error:
        print(f"[bootstrap-killswitch] unreadable state; automatic update allowed: "
              f"{type(error).__name__}", file=sys.stderr)
        return None
    try:
        if len(data) > 65536:
            raise ValueError("state exceeds size limit")
        state = json.loads(data.decode("utf-8-sig"))
        if not isinstance(state, dict) or type(state.get("active")) is not bool:
            raise ValueError("state requires a boolean active field")
    except (UnicodeError, ValueError):
        print("[bootstrap-killswitch] invalid state; automatic update allowed", file=sys.stderr)
        return None
    return "automatic-update KillSwitch active; nothing ran" if state["active"] else None


def _automatic_update_inhibited(spec: RequestSpec) -> bool:
    return automatic_update_refusal(spec) is not None


def _runner(runner: TrustedRunner) -> TrustedRunner:
    return _invocation.validate_runner(runner)


def runtime_command(
    module: str, args: Sequence[str] = (),
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Construct this runtime's retained module invocation, not a latest-slot lookup."""
    return _invocation.runtime_command(module, args)


def runtime_script_command(
    path: str | os.PathLike[str], args: Sequence[str] = (),
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Construct this runtime's retained absolute-script invocation."""
    return _invocation.runtime_script_command(path, args)


def _runtime_command(
    entry: tuple[str, ...], args: Sequence[str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    return _invocation.retained_runtime_command(entry, args)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _validate_db(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in {2, 3, SCHEMA, authority.LEGACY_SCHEMA_VERSION, authority.SCHEMA_VERSION}:
        raise StateError("unsupported update-driver schema")
    columns = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
    expected = {
        "id", "identity", "spec", "runner", "state", "exit_code", "error",
        "created_at", "started_at", "finished_at", "boot_id", "settled_boot_id", "settled_at",
    }
    if version >= 3:
        expected.add("settlement_kind")
    if version >= 4:
        expected.update({"recovery_authority", "released_at"})
    if columns != expected:
        raise StateError("malformed update-driver schema")
    if version in (authority.LEGACY_SCHEMA_VERSION, authority.SCHEMA_VERSION):
        try:
            authority.validate(db)
        except authority.AuthorityError as error:
            raise StateError("malformed update authority state") from error


def _upgrade_schema(db: sqlite3.Connection, root: Path, custody: OperationLock) -> None:
    """Migrate only under installation custody and the caller's SQLite transaction."""
    if custody.path != root / "execution.lock" or not db.in_transaction:
        raise StateError("schema migration requires installation custody and a transaction")
    custody.assert_owned()
    _validate_db(db)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version in (SCHEMA, authority.LEGACY_SCHEMA_VERSION, authority.SCHEMA_VERSION):
        return
    for row in db.execute("SELECT * FROM requests"):
        _request(row, root)
    if version == 2:
        db.execute("ALTER TABLE requests ADD COLUMN settlement_kind TEXT")
        db.execute(
            "UPDATE requests SET settlement_kind='kernel_restart' WHERE settled_at IS NOT NULL",
        )
    db.execute("ALTER TABLE requests ADD COLUMN recovery_authority TEXT")
    db.execute("ALTER TABLE requests ADD COLUMN released_at REAL")
    db.execute(f"PRAGMA user_version={SCHEMA}")
    _validate_db(db)


def _assert_database_identity(path: Path, expected: os.stat_result) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError as error:
        raise StateError("update-driver database disappeared") from error
    if not os.path.samestat(current, expected):
        raise StateError("update-driver database was replaced")


@contextmanager
def _database(
    root: Path, *, create: bool = False, writable: bool = False,
    expected_identity: os.stat_result | None = None, allow_pristine: bool = False,
):
    db = None
    try:
        if create:
            create_queue_directory(root)
        path = root / "queue.sqlite3"
        if path.is_symlink() or (path.exists() and path.resolve() != path):
            raise StateError("queue must be a plain local database")
        if expected_identity is not None:
            _assert_database_identity(path, expected_identity)
        mode = "rwc" if create else ("rw" if writable else "ro")
        db = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        if writable:
            db.execute("PRAGMA synchronous=FULL")
        if create:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not version and not tables:
                db.execute(
                    """CREATE TABLE requests (
                    id TEXT PRIMARY KEY, identity TEXT NOT NULL, spec TEXT NOT NULL,
                    runner TEXT NOT NULL, state TEXT NOT NULL
                      CHECK(state IN ('pending','running','completed','interrupted','blocked')),
                    exit_code INTEGER, error TEXT, created_at REAL NOT NULL,
                    started_at REAL, finished_at REAL, boot_id TEXT,
                    settled_boot_id TEXT, settled_at REAL, settlement_kind TEXT,
                    recovery_authority TEXT, released_at REAL)"""
                )
                db.execute(
                    "CREATE UNIQUE INDEX pending_identity ON requests(identity) WHERE state='pending'"
                )
                db.execute(f"PRAGMA user_version={SCHEMA}")
            _validate_db(db)
            db.commit()
        else:
            if allow_pristine:
                # Keep the header and schema in one read-only snapshot while a
                # first writer may still be publishing its initialization.
                db.execute("BEGIN")
            pristine = (
                allow_pristine
                and db.execute("PRAGMA user_version").fetchone()[0] == 0
                and not db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            )
            if not pristine:
                _validate_db(db)
        if expected_identity is not None:
            _assert_database_identity(path, expected_identity)
        yield db
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as error:
        raise StateError(f"update-driver state failure: {type(error).__name__}") from error
    finally:
        if db is not None:
            db.close()


def _request(row: sqlite3.Row, root: Path) -> Request:
    try:
        identifier = row["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise StateError("malformed request identifier")
        spec = _spec(RequestSpec(**json.loads(row["spec"])))
        runner = _runner(TrustedRunner(**json.loads(row["runner"])))
        if row["state"] not in STATES:
            raise StateError("malformed request state")
        code = row["exit_code"]
        if code is not None and type(code) is not int:
            raise StateError("malformed request exit status")
        if (row["state"] == "completed") != (code is not None):
            raise StateError("inconsistent request outcome")
        for field in ("created_at", "started_at", "finished_at", "settled_at"):
            value = row[field]
            if value is None and field != "created_at":
                continue
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise StateError("malformed request timestamp")
        if row["error"] is not None and not isinstance(row["error"], str):
            raise StateError("malformed request error")
        kind = (
            row["settlement_kind"] if "settlement_kind" in row.keys()
            else ("kernel_restart" if row["settled_at"] is not None else None)
        )
        settled = row["settled_at"] is not None
        if (
            settled != (row["settled_boot_id"] is not None)
            or settled != (kind is not None)
            or (settled and (
                row["state"] not in {"interrupted", "blocked"}
                or not boot.valid_boot_identity(row["boot_id"])
                or not boot.valid_boot_identity(row["settled_boot_id"])
                or row["boot_id"].split(":", 1)[0] != row["settled_boot_id"].split(":", 1)[0]
                or kind not in {"kernel_restart", "native_scope"}
                or (kind == "kernel_restart" and row["boot_id"] == row["settled_boot_id"])
                or (kind == "native_scope" and (
                    row["boot_id"] != row["settled_boot_id"] or row["state"] != "interrupted"
                    or row["finished_at"] is None or not row["error"]
                ))
            ))
        ):
            raise StateError("malformed positive-settlement evidence")
        authority = row["recovery_authority"] if "recovery_authority" in row.keys() else None
        released = row["released_at"] if "released_at" in row.keys() else None
        if (
            (authority is None) != (released is None)
            or (authority is not None and (
                authority != "operator_repair" or settled
                or row["state"] not in {"interrupted", "blocked"}
                or row["finished_at"] is None or not row["error"]
                or type(released) not in (int, float)
                or not math.isfinite(released) or released < 0
            ))
        ):
            raise StateError("malformed recovery-authorization evidence")
        expected = hashlib.sha256(
            (_json(_spec_document(spec)) + "\n" + _json(asdict(runner))).encode(),
        ).hexdigest()
        if row["identity"] != expected:
            raise StateError("request identity does not match safe invocation")
        return Request(
            identifier, spec, runner, row["state"], code, row["error"], row["created_at"],
            row["started_at"], row["finished_at"], root / f"{identifier}.log",
            row["boot_id"], row["settled_boot_id"], row["settled_at"], kind, authority, released,
        )
    except (ValueError, TypeError, InvalidRequestError) as error:
        raise StateError("malformed durable request") from error


UPDATE_AUTHORITY_CAPABILITY = authority.CAPABILITY
QUEUED_NATIVE_WRITER_CAPABILITY = authority.QUEUED_NATIVE_WRITER_CAPABILITY
current_authority = authority.current
change_authority = authority.change


def enqueue(
    spec: RequestSpec, *, runner: TrustedRunner, root: Path | None = None,
    coalesce_running: bool = False,
    _exclusive_custody: OperationLock | None = None,
) -> Request:
    """Commit acceptance; only trusted adapters with pinned targets may share active work."""
    root = _root(root)
    spec, runner = _spec(spec), _runner(runner)
    override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE")
    if override and not spec.killswitch_path:
        path = Path(override).expanduser()
        if not path.is_absolute():
            path = Path(spec.cwd or Path.cwd()) / path
        spec = _spec(replace(spec, killswitch_path=os.path.abspath(path)))
    if type(coalesce_running) is not bool:
        raise InvalidRequestError("active coalescing must be an explicit boolean")
    with authority.submission(root, spec) as spec:
        spec_json, runner_json = _json(_spec_document(spec)), _json(asdict(runner))
        identity = hashlib.sha256((spec_json + "\n" + runner_json).encode()).hexdigest()
        if _exclusive_custody is None:
            _reconcile_lost_owner(root)
        else:
            _exclusive_custody.assert_owned()
        _assert_foundation_clear(root, include_running=_exclusive_custody is not None)
        with _database(root, create=True) as db:
            db.execute("BEGIN IMMEDIATE")
            if _exclusive_custody is not None and db.execute(
                "SELECT 1 FROM requests WHERE state IN ('pending','running') LIMIT 1",
            ).fetchone() is not None:
                raise StateError("request-local execution requires an idle queue; nothing replayed")
            if _has_fence(db, root):
                raise InterruptedError("installation queue fenced; explicit positive settlement required")
            row = None
            if coalesce_running:
                row = db.execute(
                    "SELECT * FROM requests WHERE identity=? AND state='running' LIMIT 1", (identity,),
                ).fetchone()
            if row is None:
                row = db.execute(
                    "SELECT * FROM requests WHERE identity=? AND state='pending'", (identity,),
                ).fetchone()
            if row is None:
                identifier = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO requests(id,identity,spec,runner,state,created_at) VALUES(?,?,?,?,?,?)",
                    (identifier, identity, spec_json, runner_json, "pending", time.time()),
                )
                row = db.execute("SELECT * FROM requests WHERE id=?", (identifier,)).fetchone()
            request = _request(row, root)
            db.commit()
            return request


def status(
    request_id: str | None = None, *, root: Path | None = None,
) -> Request | tuple[Request, ...]:
    """Read only: an absent execution space is an empty queue, never created."""
    root = _root(root)
    if not (root / "queue.sqlite3").exists():
        if request_id is not None:
            raise RequestNotFoundError(request_id)
        return ()
    with _database(root) as db:
        if request_id is not None:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise RequestNotFoundError(request_id)
            return _request(row, root)
        return tuple(_request(row, root) for row in db.execute("SELECT * FROM requests ORDER BY rowid"))


def _outcome(
    root: Path, request_id: str, state: str, code=None, error=None, *,
    settled_boot_id: str | None = None,
) -> None:
    with _database(root, create=True) as db:
        with db:
            db.execute(
                """UPDATE requests SET state=?,exit_code=?,error=?,finished_at=?,
                settled_boot_id=?,settled_at=?,settlement_kind=? WHERE id=?""",
                (state, code, error, time.time(), settled_boot_id,
                 time.time() if settled_boot_id is not None else None,
                 "native_scope" if settled_boot_id is not None else None, request_id),
            )


def _request_fenced(request: Request) -> bool:
    return request.state in {"pending", "interrupted", "blocked"} and request.unresolved


def _has_fence(db: sqlite3.Connection, root: Path) -> bool:
    requests = tuple(_request(row, root) for row in db.execute(
        """SELECT * FROM requests WHERE state IN ('running','interrupted','blocked')
        OR (state='pending' AND
            (started_at IS NOT NULL OR finished_at IS NOT NULL OR boot_id IS NOT NULL))""",
    ))
    return any(_request_fenced(request) for request in requests)


def foundation_status(*, root: Path | None = None) -> foundation_state.FoundationOperation | None:
    """Read native foundation progress independently of the plugin request queue."""
    return _foundation_operation(_root(root))


def _foundation_operation(root: Path) -> foundation_state.FoundationOperation | None:
    try:
        return foundation_state.read(root)
    except foundation_state.FoundationStateError as error:
        raise StateError(str(error)) from error


def _assert_foundation_clear(root: Path, *, include_running: bool = False) -> None:
    operation = _foundation_operation(root)
    if operation is not None and operation.fenced and (
        include_running or operation.state == "interrupted"
    ):
        raise InterruptedError("installation queue fenced by unsettled foundation construction")


def _reconcile_foundation(root: Path, custody: OperationLock) -> None:
    try:
        foundation_state.reconcile_lost(root, custody)
    except foundation_state.FoundationStateError as error:
        raise StateError(str(error)) from error
    _assert_foundation_clear(root)


def _reconcile_lost_owner(root: Path) -> None:
    """Observation only: free kernel custody proves no transaction owner remains."""
    queue_exists = (root / "queue.sqlite3").exists()
    if not queue_exists and _foundation_operation(root) is None:
        return
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError:
        return
    try:
        _reconcile_foundation(root, lock)
        if not queue_exists:
            return
        with _database(root, create=True) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                _has_fence(db, root)
                db.execute(
                    """UPDATE requests SET state='interrupted',error=?,finished_at=?
                    WHERE state='running'""",
                    ("previous owner lost custody; descendant settlement unconfirmed", time.time()),
                )
    finally:
        lock.release()


def ensure_worker(
    worker_argv: tuple[str, ...] | list[str], *, root: Path | None = None,
    cwd: Path | None = None, env: Mapping[str, str] | None = None,
    startup_timeout: float = 15,
    python_bootstrap_root: Path | None = None,
):
    """Launch a trusted pinned product worker; its entry point must call drain."""
    from agent_procutil import spawn_daemon

    root = _root(root)
    argv = _vector(worker_argv)
    if not argv or not Path(argv[0]).is_absolute():
        raise InvalidRequestError("worker requires an absolute trusted executable")
    if not math.isfinite(startup_timeout) or startup_timeout <= 0:
        raise InvalidRequestError("startup timeout must be finite and positive")
    create_queue_directory(root)
    _reconcile_lost_owner(root)
    _assert_foundation_clear(root)
    if any(_request_fenced(row) for row in status(root=root)):
        raise InterruptedError("installation queue fenced; explicit positive settlement required")
    token = uuid.uuid4().hex
    receipt = root / f".worker-started-{token}"
    try:
        process = spawn_daemon(
            argv, cwd=cwd or root, log_path=root / "worker.log",
            env_overrides={**(env or {}), "UPDATE_DRIVER_STARTUP": token},
            console_children=True, breakaway=True, contained_fallback=False,
            **({"python_bootstrap_root": python_bootstrap_root}
               if python_bootstrap_root is not None else {}),
        )
        deadline = time.monotonic() + startup_timeout
        while not receipt.is_file():
            code = process.poll()
            if code is not None:
                admission_errors = [
                    row.error for row in status(root=root)
                    if row.state == "pending" and row.error
                    and row.error.startswith("worker admission failed:")
                ]
                if admission_errors:
                    raise SpawnError(admission_errors[0])
                raise SpawnError(f"worker exited before queue startup: exit {code}")
            if time.monotonic() >= deadline:
                raise SpawnError("worker did not acknowledge queue startup before deadline")
            time.sleep(.02)
        with _database(root, create=True) as db:
            with db:
                db.execute(
                    """UPDATE requests SET error=NULL WHERE state='pending'
                    AND (error LIKE ? OR error LIKE ? OR error LIKE ?)""",
                    ("worker launch failed:%", "worker admission failed:%",
                     "worker exited before claiming request:%"),
                )
        return process
    except Exception as error:
        # Do not expose argv/environment or allow a foreground fallback.
        detail = str(error) if isinstance(error, SpawnError) else type(error).__name__
        with _database(root, create=True) as db:
            with db:
                db.execute(
                    "UPDATE requests SET error=COALESCE(error,?) WHERE state='pending'",
                    (f"worker launch failed: {detail}",),
                )
        raise SpawnError(f"worker launch failed: {detail}") from error
    finally:
        receipt.unlink(missing_ok=True)


class Transaction:
    """Authority and logging for exactly one complete executor transaction."""

    def __init__(self, request: Request, root: Path, lock: OperationLock):
        self.request, self.root, self.log_path = request, root, request.log_path
        self._lock = lock
        self._witness = None
        self._native_settled = False

    def _confirm_native_settlement(self) -> None:
        self.assert_owned()
        self._native_settled = True

    def assert_owned(self) -> None:
        self._lock.assert_owned()

    def participant_env(self) -> dict[str, str]:
        """Ephemeral authenticated IPC witness; never persist these values."""
        self.assert_owned()
        if self._witness is None:
            from .participant import Witness
            self._witness = Witness(self.root, self.request.id)
        return self._witness.environment()

    def _close(self) -> None:
        if self._witness is not None:
            self._witness.close()


def _execute(transaction: Transaction) -> int:
    import agent_procutil
    from agent_procutil.owned_process import run_installation_owned

    request = transaction.request
    witness = transaction.participant_env()
    # The shim changes cwd/env and directs output before exec. The product
    # invocation itself remains exactly the pinned argv supplied by its adapter.
    command = [
        sys.executable, "-I", "-S", *agent_procutil.python_cache_args(transaction.root),
        str(Path(__file__).with_name("_runner.py")),
        str(transaction.root), request.id, witness["UPDATE_DRIVER_WITNESS"],
        str(Path(agent_procutil.__file__).resolve()),
    ]
    result = run_installation_owned(
        command, timeout=request.runner.timeout,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
        on_settled=transaction._confirm_native_settlement,
    )
    if result.stdout or result.stderr:
        with transaction.log_path.open("ab", buffering=0) as logfile:
            logfile.write(((result.stdout or "") + (result.stderr or "")).encode("utf-8"))
    return result.returncode


def drain(
    *, root: Path | None = None, executor: Callable[[Transaction], int] | None = None,
    _request_id: str | None = None, _custody: OperationLock | None = None,
) -> int:
    """Finite worker or supervised debug; always the same kernel/journal authority.

    A busy worker waits for custody instead of dropping its wakeup: this closes
    the enqueue/last-owner-exit race without a resident scheduler.
    """
    root = _root(root)
    with _database(root, create=True):
        pass
    admission_failure = "executor_unavailable"
    try:
        if executor is None:
            from agent_procutil.owned_process import require_caller_owned_support

            require_caller_owned_support()
        admission_failure = "boot_identity_unavailable"
        admission_boot = boot.kernel_boot_identity()
        if not boot.valid_boot_identity(admission_boot):
            raise StateError("native boot identity malformed")
    except Exception as error:
        reason = "boot_identity_malformed" if isinstance(error, StateError) else admission_failure
        detail = f"worker admission failed: {reason}:{type(error).__name__}"
        with _database(root, writable=True) as db, db:
            db.execute(
                "UPDATE requests SET error=? WHERE state='pending'"
                + (" AND id=?" if _request_id is not None else ""),
                (detail, _request_id) if _request_id is not None else (detail,),
            )
        raise StateError(detail) from error
    startup = os.environ.pop("UPDATE_DRIVER_STARTUP", "")
    if re.fullmatch(r"[0-9a-f]{32}", startup):
        # Observation only, never custody: a caller can distinguish import/
        # entrypoint failure from a real worker waiting on OperationLock.
        pending = root / f".worker-starting-{startup}"
        try:
            with pending.open("xb") as receipt:
                receipt.write(b"queue-ready\n")
                receipt.flush()
                os.fsync(receipt.fileno())
            pending.rename(root / f".worker-started-{startup}")
        finally:
            pending.unlink(missing_ok=True)
    lock = _custody or OperationLock(root / "execution.lock")
    if _custody is None:
        while True:
            try:
                lock.acquire()
                break
            except OperationLockedError:
                time.sleep(.05)
    reconcile = _reconcile_foundation if _request_id is None else (
        lambda root, custody: _assert_foundation_clear(root, include_running=True)
    )
    try:
        if _request_id is None:
            with _database(root, writable=True) as db:
                db.execute("BEGIN IMMEDIATE")
                _upgrade_schema(db, root, lock)
                db.commit()
        reconcile(root, lock)
        with _database(root, create=True) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                _has_fence(db, root)
                if _request_id is None:
                    db.execute(
                        """UPDATE requests SET state='interrupted',error=?,finished_at=?
                        WHERE state='running'""",
                        ("previous owner lost custody; descendant settlement unconfirmed", time.time()),
                    )
            if _has_fence(db, root):
                raise InterruptedError("installation queue fenced by unsettled prior transaction")
        while True:
            lock.assert_owned()
            reconcile(root, lock)
            with _database(root, create=True) as db:
                db.execute("BEGIN IMMEDIATE")
                if _has_fence(db, root):
                    raise InterruptedError("installation queue fenced by unsettled prior transaction")
                row = db.execute(
                    "SELECT * FROM requests WHERE state='pending' ORDER BY rowid LIMIT 1",
                ).fetchone()
                if row is None:
                    db.commit()
                    return 0
                if _request_id is not None and row["id"] != _request_id:
                    raise StateError("request-local execution cannot consume another queued request")
                request = _request(row, root)
                reason = authority.refusal(root, request.spec, db=db) or automatic_update_refusal(request.spec)
                if reason is not None:
                    db.execute(
                        """UPDATE requests SET state='completed',exit_code=3,error=?,
                        finished_at=? WHERE id=?""",
                        (reason, time.time(), request.id),
                    )
                    db.commit()
                    print(f"Update request {request.id}: {reason}", file=sys.stderr)
                    if _request_id is not None:
                        return 0
                    continue
                db.execute(
                    "UPDATE requests SET state='running',started_at=?,boot_id=?,error=NULL WHERE id=?",
                    (time.time(), admission_boot, request.id),
                )
                request = _request(
                    db.execute("SELECT * FROM requests WHERE id=?", (request.id,)).fetchone(), root,
                )
                db.commit()
            transaction = Transaction(request, root, lock)
            scope = _active_transaction.set(transaction)
            settled_failure = False
            try:
                try:
                    code = (executor or _execute)(transaction)
                except BaseException:
                    settled_failure = executor is None and transaction._native_settled
                    raise
                transaction._close()
                transaction.assert_owned()
                reconcile(root, lock)
                if type(code) is not int:
                    raise StateError("executor did not return an integer exit status")
                if request.log_path.exists():
                    with request.log_path.open("ab") as logfile:
                        logfile.flush()
                        os.fsync(logfile.fileno())
                _outcome(
                    root, request.id, "completed", code,
                    None if code == 0 else f"canonical executor exited with status {code}",
                )
            except BaseException as error:
                try:
                    transaction._close()
                    transaction.assert_owned()
                except BaseException:
                    settled_failure = False
                    raise
                finally:
                    _outcome(
                        root, request.id, "interrupted",
                        error=(
                            f"executor interrupted: {type(error).__name__}; "
                            + ("native scope settled" if settled_failure else "settlement required")
                        ),
                        settled_boot_id=request.boot_id if settled_failure else None,
                    )
                raise
            finally:
                _active_transaction.reset(scope)
            if _request_id is not None:
                return 0
    finally:
        if _custody is None:
            lock.release()


def run_explicit(
    spec: RequestSpec, *, runner: TrustedRunner, root: Path | None = None,
    executor: Callable[[Transaction], int] | None = None,
) -> Request:
    """Run only a newly admitted explicit operation; never drain or repair peers."""
    root = _root(root)
    with _database(root, create=True):
        pass
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError as error:
        raise StateError("request-local execution is busy; no request admitted") from error
    try:
        _assert_foundation_clear(root, include_running=True)
        with _database(root, writable=True) as db:
            db.execute("BEGIN IMMEDIATE")
            lock.assert_owned()
            if db.execute("SELECT 1 FROM requests WHERE state='running' LIMIT 1").fetchone() is not None:
                raise InterruptedError("installation queue has unsettled running work; recovery required")
            if db.execute(
                "SELECT 1 FROM requests WHERE state='pending' LIMIT 1",
            ).fetchone() is not None or _has_fence(db, root):
                raise StateError("request-local execution requires an idle, unfenced queue; nothing replayed")
            _upgrade_schema(db, root, lock)
            db.commit()
        request = enqueue(spec, runner=runner, root=root, _exclusive_custody=lock)
        try:
            drain(root=root, executor=executor, _request_id=request.id, _custody=lock)
        except BaseException as error:
            setattr(error, "request_id", request.id)
            raise
        result = status(request.id, root=root)
        if not isinstance(result, Request):
            raise StateError("request-local execution returned no request")
        return result
    finally:
        lock.release()


def wait(
    request_id: str, *, root: Path | None = None, timeout: float | None = None,
    output: TextIO | None = None, poll_interval: float = .05, worker=None,
) -> Request:
    """Follow durable output and return the canonical recorded outcome."""
    if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
        raise InvalidRequestError("wait timeout must be finite and nonnegative")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise InvalidRequestError("poll interval must be finite and positive")
    deadline = None if timeout is None else time.monotonic() + timeout
    offset = 0
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        request = status(request_id, root=root)
        if output is not None and request.log_path.exists():
            with request.log_path.open("rb") as logfile:
                logfile.seek(offset)
                data = logfile.read(65536)
                offset = logfile.tell()
            if data:
                output.write(decoder.decode(data))
                output.flush()
        if request.state in {"completed", "interrupted", "blocked"}:
            if output is not None and request.log_path.exists() and offset < request.log_path.stat().st_size:
                continue
            if output is not None:
                output.write(decoder.decode(b"", final=True))
                if request.started_at is None and request.error:
                    output.write(request.error + "\n")
                output.flush()
            return request
        _reconcile_lost_owner(_root(root))
        _assert_foundation_clear(_root(root))
        if status(request_id, root=root).state != request.state:
            continue
        if request.state == "pending" and any(_request_fenced(item) for item in status(root=root)):
            raise InterruptedError("installation queue fenced by unsettled prior transaction")
        if request.state == "pending" and request.error:
            raise SpawnError(request.error)
        if request.state == "pending" and worker is not None:
            code = worker.poll()
            if code is not None:
                _observe_pending_worker_exit(_root(root), request_id, code)
                if status(request_id, root=root).state != "pending":
                    continue
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("installation request is not yet terminal")
        time.sleep(poll_interval)


def _observe_pending_worker_exit(root: Path, request_id: str, code: int) -> None:
    """A pinned caller-supplied handle plus free custody, never a PID/age probe."""
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError:
        return
    try:
        _reconcile_foundation(root, lock)
        with _database(root, writable=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise RequestNotFoundError(request_id)
            request = _request(row, root)
            if request.state != "pending":
                return
            if _has_fence(db, root):
                raise InterruptedError("installation queue fenced by unsettled prior transaction")
            error = f"worker exited before claiming request: exit {code}"
            db.execute("UPDATE requests SET error=? WHERE id=?", (error, request_id))
            db.commit()
        raise SpawnError(error)
    finally:
        lock.release()
