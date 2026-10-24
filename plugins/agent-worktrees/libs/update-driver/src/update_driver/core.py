"""SQLite journal and kernel-custodied finite worker."""

from __future__ import annotations

import hashlib
import codecs
import json
import math
import os
import re
import sqlite3
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from single_instance_lease.operation import OperationLock, OperationLockedError
from . import boot, foundation_state
from ._directory import create_queue_directory

SCHEMA = 3
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
    if (
        not isinstance(spec, RequestSpec) or not isinstance(spec.backend, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", spec.backend)
    ):
        raise InvalidRequestError("backend must be a bounded adapter identifier")
    if not isinstance(spec.context, (list, tuple)):
        raise InvalidRequestError("safe context must contain bounded key/value pairs")
    context = tuple(spec.context)
    if len(context) > 64 or any(
        not isinstance(item, (list, tuple)) or len(item) != 2 for item in context
    ):
        raise InvalidRequestError("safe context must contain bounded key/value pairs")
    pairs = tuple(sorted((_text(k), _text(v)) for k, v in context))
    if len({k for k, _v in pairs}) != len(pairs):
        raise InvalidRequestError("safe context keys must be unique")
    cwd = _text(spec.cwd)
    if cwd:
        if not Path(cwd).is_absolute():
            raise InvalidRequestError("request cwd must be absolute")
        cwd = os.path.normcase(os.path.normpath(cwd))
    return RequestSpec(
        spec.backend, _vector(spec.argv), _text(spec.project), cwd, _text(spec.source), pairs,
    )


def _runner(runner: TrustedRunner) -> TrustedRunner:
    if not isinstance(runner, TrustedRunner):
        raise InvalidRequestError("trusted adapter runner is required")
    argv = _vector(runner.argv)
    cwd = _text(runner.cwd)
    if not argv or not Path(argv[0]).is_absolute() or not Path(cwd).is_absolute():
        raise InvalidRequestError("trusted runner executable and cwd must be absolute")
    if (
        isinstance(runner.timeout, bool) or not isinstance(runner.timeout, (int, float))
        or not math.isfinite(runner.timeout) or runner.timeout <= 0
    ):
        raise InvalidRequestError("runner timeout must be finite and positive")
    env = runner.env.items() if isinstance(runner.env, Mapping) else runner.env
    try:
        pairs = tuple(sorted((_text(k), _text(v)) for k, v in env))
    except (TypeError, ValueError) as error:
        raise InvalidRequestError("runner environment must contain key/value pairs") from error
    if any(k not in {"__PYVENV_LAUNCHER__", "PYTHONPATH"} for k, _v in pairs):
        raise InvalidRequestError("runner environment accepts only Python import mapping")
    if len({k for k, _v in pairs}) != len(pairs):
        raise InvalidRequestError("runner environment keys must be unique")
    context_keys = _vector(runner.context_keys)
    if len(context_keys) > 64 or len(set(context_keys)) != len(context_keys) or any(
        not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", key)
        or key.startswith("UPDATE_DRIVER_")
        or key in {"PYTHONPATH", "__PYVENV_LAUNCHER__"}
        for key in context_keys
    ):
        raise InvalidRequestError("runner context keys must be unique environment identifiers")
    return TrustedRunner(argv, cwd, runner.timeout, pairs, tuple(sorted(context_keys)))


def runtime_command(
    module: str, args: Sequence[str] = (),
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Construct this runtime's retained module invocation, not a latest-slot lookup."""
    from agent_procutil import direct_python

    if not isinstance(module, str) or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", module):
        raise InvalidRequestError("runtime module must be an import identifier")
    executable, env = direct_python(sys.executable)
    prefix = Path(sys.prefix)
    bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
    installed = (
        (prefix / ".install-complete.json").exists()
        or (prefix / ".install-complete.json").is_symlink()
        or bootstrap.parent.exists() or bootstrap.parent.is_symlink()
        or prefix.parent.name == "versions"
    )
    if bootstrap.exists() or bootstrap.is_symlink():
        info = bootstrap.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or bootstrap.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400
            or bootstrap.parent.resolve() != bootstrap.parent
        ):
            raise StateError("retained runtime bootstrap is linked or unsafe")
    if bootstrap.is_file():
        return (
            executable, "-I", "-S", "-B", "-X", "utf8", str(bootstrap), "-m", module,
            *_vector(args),
        ), env
    if installed:
        raise StateError("installed runtime is missing its retention bootstrap")
    return (executable, "-m", module, *_vector(args)), env


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _validate_db(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in {2, SCHEMA}:
        raise StateError("unsupported update-driver schema")
    columns = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
    expected = {
        "id", "identity", "spec", "runner", "state", "exit_code", "error",
        "created_at", "started_at", "finished_at", "boot_id", "settled_boot_id", "settled_at",
    }
    if version == SCHEMA:
        expected.add("settlement_kind")
    if columns != expected:
        raise StateError("malformed update-driver schema")


def _upgrade_schema(db: sqlite3.Connection, root: Path, custody: OperationLock) -> None:
    """Migrate only under installation custody and the caller's SQLite transaction."""
    if custody.path != root / "execution.lock" or not db.in_transaction:
        raise StateError("schema migration requires installation custody and a transaction")
    custody.assert_owned()
    _validate_db(db)
    if db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA:
        return
    for row in db.execute("SELECT * FROM requests"):
        _request(row, root)
    db.execute("ALTER TABLE requests ADD COLUMN settlement_kind TEXT")
    db.execute(
        "UPDATE requests SET settlement_kind='kernel_restart' WHERE settled_at IS NOT NULL",
    )
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
    expected_identity: os.stat_result | None = None,
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
                    settled_boot_id TEXT, settled_at REAL, settlement_kind TEXT)"""
                )
                db.execute(
                    "CREATE UNIQUE INDEX pending_identity ON requests(identity) WHERE state='pending'"
                )
                db.execute(f"PRAGMA user_version={SCHEMA}")
            _validate_db(db)
            db.commit()
        else:
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
        expected = hashlib.sha256(
            (_json(asdict(spec)) + "\n" + _json(asdict(runner))).encode(),
        ).hexdigest()
        if row["identity"] != expected:
            raise StateError("request identity does not match safe invocation")
        return Request(
            identifier, spec, runner, row["state"], code, row["error"], row["created_at"],
            row["started_at"], row["finished_at"], root / f"{identifier}.log",
            row["boot_id"], row["settled_boot_id"], row["settled_at"], kind,
        )
    except (ValueError, TypeError, InvalidRequestError) as error:
        raise StateError("malformed durable request") from error


def enqueue(
    spec: RequestSpec, *, runner: TrustedRunner, root: Path | None = None,
    coalesce_running: bool = False,
) -> Request:
    """Commit acceptance; only trusted adapters with pinned targets may share active work."""
    root = _root(root)
    spec, runner = _spec(spec), _runner(runner)
    if type(coalesce_running) is not bool:
        raise InvalidRequestError("active coalescing must be an explicit boolean")
    spec_json, runner_json = _json(asdict(spec)), _json(asdict(runner))
    identity = hashlib.sha256((spec_json + "\n" + runner_json).encode()).hexdigest()
    _reconcile_lost_owner(root)
    _assert_foundation_clear(root)
    with _database(root, create=True) as db:
        db.execute("BEGIN IMMEDIATE")
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


def _has_fence(db: sqlite3.Connection, root: Path) -> bool:
    requests = tuple(_request(row, root) for row in db.execute(
        "SELECT * FROM requests WHERE state IN ('running','interrupted','blocked')",
    ))
    return any(
        request.state in {"interrupted", "blocked"} and request.settled_at is None
        for request in requests
    )


def foundation_status(*, root: Path | None = None) -> foundation_state.FoundationOperation | None:
    """Read native foundation progress independently of the plugin request queue."""
    return _foundation_operation(_root(root))


def _foundation_operation(root: Path) -> foundation_state.FoundationOperation | None:
    try:
        return foundation_state.read(root)
    except foundation_state.FoundationStateError as error:
        raise StateError(str(error)) from error


def _assert_foundation_clear(root: Path) -> None:
    operation = _foundation_operation(root)
    if operation is not None and operation.state == "interrupted" and operation.fenced:
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
    if any(
        row.state in {"interrupted", "blocked"} and row.settled_at is None
        for row in status(root=root)
    ):
        raise InterruptedError("installation queue fenced; explicit positive settlement required")
    token = uuid.uuid4().hex
    receipt = root / f".worker-started-{token}"
    try:
        process = spawn_daemon(
            argv, cwd=cwd or root, log_path=root / "worker.log",
            env_overrides={**(env or {}), "UPDATE_DRIVER_STARTUP": token},
            console_children=True, breakaway=True, contained_fallback=False,
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
    from agent_procutil.owned_process import run_caller_owned

    request = transaction.request
    witness = transaction.participant_env()
    # The shim changes cwd/env and directs output before exec. The product
    # invocation itself remains exactly the pinned argv supplied by its adapter.
    command = [
        sys.executable, "-I", str(Path(__file__).with_name("_runner.py")),
        str(transaction.root), request.id, witness["UPDATE_DRIVER_WITNESS"],
        str(Path(agent_procutil.__file__).resolve()),
    ]
    result = run_caller_owned(
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
            db.execute("UPDATE requests SET error=? WHERE state='pending'", (detail,))
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
    lock = OperationLock(root / "execution.lock")
    while True:
        try:
            lock.acquire()
            break
        except OperationLockedError:
            time.sleep(.05)
    try:
        with _database(root, writable=True) as db:
            db.execute("BEGIN IMMEDIATE")
            _upgrade_schema(db, root, lock)
            db.commit()
        _reconcile_foundation(root, lock)
        with _database(root, create=True) as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                _has_fence(db, root)
                db.execute(
                    """UPDATE requests SET state='interrupted',error=?,finished_at=?
                    WHERE state='running'""",
                    ("previous owner lost custody; descendant settlement unconfirmed", time.time()),
                )
            if _has_fence(db, root):
                raise InterruptedError("installation queue fenced by unsettled prior transaction")
        while True:
            lock.assert_owned()
            _reconcile_foundation(root, lock)
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
                request = _request(row, root)
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
                _reconcile_foundation(root, lock)
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
                output.flush()
            return request
        _reconcile_lost_owner(_root(root))
        _assert_foundation_clear(_root(root))
        if status(request_id, root=root).state != request.state:
            continue
        if request.state == "pending" and request.error:
            raise SpawnError(request.error)
        if request.state == "pending" and any(
            item.state in {"interrupted", "blocked"} and item.settled_at is None
            for item in status(root=root)
        ):
            raise InterruptedError("installation queue fenced by unsettled prior transaction")
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
