"""Durable admission of an explicitly selected thin client; never execution."""

from contextlib import ExitStack, contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time

from . import artifact_staging
from .planning import PlanError, canonical, decode, identifier, integer, record
from .private_state import windows_private as _windows_private
from .release_selection import ReleaseSelection
from .staging_cleanup import _windows_handle

DATABASE = "client-admission.sqlite3"
LEASE = "client-admission"
APPLICATION_ID = 0x464D4349
SCHEMA_VERSION = 1
_TABLES = {
    "installation": "CREATE TABLE installation (singleton INTEGER PRIMARY KEY CHECK "
                    "(singleton = 1), identity TEXT NOT NULL)",
    "admission": "CREATE TABLE admission (singleton INTEGER PRIMARY KEY CHECK "
                 "(singleton = 1), sequence INTEGER NOT NULL CHECK (sequence > 0), "
                 "receipt TEXT NOT NULL)",
}
_READ_HEADER = """
import os,stat,sys
fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
try:
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(int(sys.argv[2]),int(sys.argv[3])):
        raise SystemExit(2)
    sys.stdout.buffer.write(os.read(fd,100))
finally:
    os.close(fd)
"""


def _identity(value):
    value = decode(canonical(value))
    record(value, "fleet-manager.client-installation",
           {"authority", "installation", "role", "initialized_by", "receipt",
            "selection_owner"})
    for name in ("authority", "installation", "initialized_by", "receipt", "selection_owner"):
        identifier(value[name], f"installation {name}")
    if value["role"] != "client":
        raise PlanError("client admission refuses hosting roles")
    return value


def _selection(value, identity):
    selected = ReleaseSelection.from_dict(value)
    value = selected.to_dict()
    if (value["authority"], value["installation"], value["role"], value["owner"]) != (
        identity["authority"], identity["installation"], "client", identity["selection_owner"],
    ):
        raise PlanError("selected release owner or installation scope mismatch")
    if len(value["components"]) > 256:
        raise PlanError("selected client closure exceeds its component bound")
    return value


@contextmanager
def _root(root):
    root = Path(root)
    original = artifact_staging._directory(root)
    with ExitStack() as stack:
        if os.name == "nt":
            for entry in (*reversed(root.parents), root):
                stack.enter_context(_windows_handle(entry))
            _windows_private(root)
            database_root = root
        else:
            info = root.lstat()
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise PlanError("client admission root is not private to its owner")
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            # Use the held Linux directory namespace; root mutators still must be quiesced.
            anchored = Path("/proc/self/fd") / str(descriptor)
            database_root = anchored if anchored.is_dir() else root

        def unchanged():
            if artifact_staging._directory(root) != original:
                raise PlanError("client admission root identity changed")

        unchanged()
        yield root, database_root, unchanged


def _ordinary_file(path):
    info = artifact_staging._file_stat(path)
    if info.st_nlink != 1:
        raise PlanError("client admission refuses multiply linked state")
    if os.name != "nt" and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise PlanError("client admission state is not private to its owner")
    if info.st_size > 4 * 1024 * 1024:
        raise PlanError("client admission state exceeds its byte bound")
    if os.name == "nt":
        _windows_private(path)
    return info


def _lease_shape(root):
    directory = root / ".construction-leases"
    expected = hashlib.sha256(LEASE.encode("ascii")).hexdigest() + ".lock"
    if (root / DATABASE).exists() and not (directory / expected).exists():
        raise PlanError("client admission ownership metadata is missing; owned recovery is required")
    if directory.exists() or directory.is_symlink():
        artifact_staging._directory(directory)
        for child in directory.iterdir():
            if child.name != expected:
                raise PlanError("client admission lease directory contains foreign state")
            _ordinary_file(child)


@contextmanager
def _owned(root):
    with _root(root) as (path, database_root, unchanged):
        _lease_shape(path)
        with artifact_staging._primitive() as primitive:
            with primitive.construction_lease(path, LEASE):
                unchanged()
                _lease_shape(path)
                yield path, database_root, unchanged


def _check_database_header(path, expected, *, initializing):
    if os.name == "nt":
        with path.open("rb") as stream:
            if artifact_staging._identity(os.fstat(stream.fileno())) != artifact_staging._identity(expected):
                raise PlanError("client admission database identity changed")
            header = stream.read(100)
    else:
        # A raw close in this process would drop sibling SQLite fcntl locks.
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", _READ_HEADER, str(path),
             str(expected.st_dev), str(expected.st_ino)],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            raise PlanError("client admission database header could not be read")
        header = result.stdout
    if initializing and expected.st_size == 0 and not header:
        return
    if len(header) != 100 or header[:16] != b"SQLite format 3\0":
        raise PlanError("client admission database header is invalid")
    if header[18:20] != b"\1\1":
        raise PlanError("client admission refuses unsupported WAL database header")


@contextmanager
def _connection(root, *, database_root, readonly, initializing=False):
    before = _ordinary_file(root / DATABASE)
    database = database_root / DATABASE
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = root / (DATABASE + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            _ordinary_file(sidecar)
            if suffix != "-journal":
                raise PlanError("client admission refuses unsupported WAL state")
    with ExitStack() as stack:
        if os.name == "nt":
            stack.enter_context(_windows_handle(database))
        _check_database_header(root / DATABASE, before, initializing=initializing)
        connection = sqlite3.connect(
            database.absolute().as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True, timeout=30, isolation_level=None,
        )
        try:
            if artifact_staging._identity(_ordinary_file(root / DATABASE)) != artifact_staging._identity(before):
                raise PlanError("client admission database identity changed")
            connection.execute("PRAGMA trusted_schema = OFF")
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise PlanError("client admission requires the owned rollback-journal format")
            if not readonly:
                connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()


def _read_state(connection, identity):
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    ):
        raise PlanError("unsupported client admission state version or application")
    tables = dict(connection.execute(
        "SELECT name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall())
    if tables != _TABLES or connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise PlanError("client admission schema or integrity mismatch")
    rows = connection.execute("SELECT singleton, identity FROM installation").fetchall()
    expected = canonical(identity).decode("utf-8")
    if rows != [(1, expected)]:
        raise PlanError("client admission installation identity mismatch")
    rows = connection.execute("SELECT singleton, sequence, receipt FROM admission").fetchall()
    if not rows:
        return None
    if len(rows) != 1 or rows[0][0] != 1:
        raise PlanError("invalid client admission high-water state")
    receipt = decode(rows[0][2].encode("utf-8"))
    record(receipt, "fleet-manager.client-admitted-release",
           {"sequence", "release_digest", "selection_digest", "selection", "admitted_at"})
    selection = _selection(receipt["selection"], identity)
    integer(receipt["admitted_at"], "admission time")
    if (
        receipt["sequence"] != rows[0][1] or receipt["sequence"] != selection["sequence"]
        or receipt["release_digest"] != selection["release_digest"]
        or receipt["selection_digest"] != ReleaseSelection.from_dict(selection).selection_digest
        or canonical(receipt).decode("utf-8") != rows[0][2]
    ):
        raise PlanError("client admission receipt identity mismatch")
    return receipt


def _report(identity, receipt, *, changed, status):
    return {
        "schema": "fleet-manager.client-admission", "version": 1,
        "status": status, "identity": identity, "admitted": receipt,
        "evidence": "local-durable-admission-only", "mutations_performed": changed,
        "constructed": False, "healthy": False, "active": False,
        "activation_authorized": False, "live_readiness_verified": False,
    }


def _commit(connection):
    try:
        connection.commit()
    except sqlite3.Error:
        raise PlanError(
            "client admission commit outcome is unknown; reread durable state under ownership"
        ) from None


def _initialization_name(identity):
    return ".client-initialization-" + hashlib.sha256(canonical(identity)).hexdigest()


def _clear_initialization(path):
    artifact_staging._directory(path)
    children = list(path.iterdir())
    for child in children:
        if child.name not in {DATABASE, DATABASE + "-journal"}:
            raise PlanError("client initialization contains foreign state")
        _ordinary_file(child)
    for child in children:
        child.unlink()
    path.rmdir()


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sync_initialization_lease(root):
    if os.name != "nt":
        lock = root / ".construction-leases" / (
            hashlib.sha256(LEASE.encode("ascii")).hexdigest() + ".lock"
        )
        before = _ordinary_file(lock)
        descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if artifact_staging._identity(os.fstat(descriptor)) != artifact_staging._identity(before):
                raise PlanError("client initialization ownership metadata changed")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _sync_directory(lock.parent)
        _sync_directory(root)


def initialize_client_admission(root, identity):
    """Explicitly initialize an empty private root, or inspect its exact owned identity."""
    identity = _identity(identity)
    initialization = _initialization_name(identity)
    try:
        with _root(root) as (path, database_root, unchanged):
            _lease_shape(path)
            if (path / DATABASE).exists() and not (path / initialization).exists():
                with _connection(path, database_root=database_root, readonly=True) as connection:
                    connection.execute("BEGIN")
                    receipt = _read_state(connection, identity)
                    unchanged()
                    return _report(identity, receipt, changed=False,
                                   status="admitted" if receipt else "initialized")
            if not (path / DATABASE).exists() and (
                set(child.name for child in path.iterdir()) - {".construction-leases", initialization}
            ):
                raise PlanError("client admission initialization refuses a nonempty foreign root")
            unchanged()
        with _owned(root) as (path, database_root, unchanged):
            database = database_root / DATABASE
            if database.exists():
                with _connection(path, database_root=database_root, readonly=True) as connection:
                    connection.execute("BEGIN")
                    receipt = _read_state(connection, identity)
                    unchanged()
                    cleared = (path / initialization).exists()
                    if cleared:
                        _sync_directory(database_root)
                        _clear_initialization(path / initialization)
                        _sync_directory(database_root)
                    return _report(identity, receipt, changed=cleared,
                                   status="admitted" if receipt else "initialized")
            if set(child.name for child in path.iterdir()) - {".construction-leases", initialization}:
                raise PlanError("client admission initialization refuses a nonempty foreign root")
            if (path / initialization).exists():
                _clear_initialization(path / initialization)
            candidate = database_root / initialization
            candidate.mkdir(mode=0o700)
            descriptor = os.open(candidate / DATABASE, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
            with _connection(
                path / initialization, database_root=candidate, readonly=False, initializing=True,
            ) as connection:
                connection.execute("BEGIN IMMEDIATE")
                for sql in _TABLES.values():
                    connection.execute(sql)
                connection.execute("PRAGMA application_id = " + str(APPLICATION_ID))
                connection.execute("PRAGMA user_version = " + str(SCHEMA_VERSION))
                connection.execute("INSERT INTO installation VALUES (1, ?)",
                                   (canonical(identity).decode("utf-8"),))
                unchanged()
                _commit(connection)
                _read_state(connection, identity)
                unchanged()
            unchanged()
            if database.exists():
                raise PlanError("client admission database appeared during initialization")
            _sync_initialization_lease(path)
            os.replace(candidate / DATABASE, database)
            _sync_directory(database_root)
            _clear_initialization(path / initialization)
            _sync_directory(database_root)
            unchanged()
            return _report(identity, None, changed=True, status="initialized")
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client admission initialization could not establish durable state") from None


def inspect_client_admission(root, identity):
    """Read an existing journal without creating state, leases, credentials or runtime slots."""
    identity = _identity(identity)
    try:
        with _root(root) as (path, database_root, unchanged):
            with _connection(path, database_root=database_root, readonly=True) as connection:
                connection.execute("BEGIN")
                receipt = _read_state(connection, identity)
                unchanged()
                return _report(identity, receipt, changed=False,
                               status="admitted" if receipt else "initialized")
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client admission state could not be inspected without mutation") from None


def recover_client_admission(root, identity):
    """Validate existing state under ownership, allowing SQLite rollback recovery."""
    identity = _identity(identity)
    try:
        with _root(root) as (path, _, unchanged):
            _ordinary_file(path / DATABASE)
            _lease_shape(path)
            unchanged()
        with _owned(root) as (path, database_root, unchanged):
            with _connection(path, database_root=database_root, readonly=False) as connection:
                connection.execute("BEGIN IMMEDIATE")
                _read_state(connection, identity)
                unchanged()
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client admission state could not be recovered under ownership") from None


def admit_client_release(root, identity, selection, staged, policy, *, clock=lambda: int(time.time())):
    """Persist a supplied selected release after fresh live-stage authentication, never execute it."""
    identity = _identity(identity)
    selection = _selection(selection, identity)
    if not isinstance(staged, artifact_staging.StagedArtifacts):
        raise PlanError("client admission requires a live retained artifact stage")
    if (staged.authority, staged.installation, staged.role) != (
        identity["authority"], identity["installation"], "client",
    ):
        raise PlanError("retained stage installation scope mismatch")
    try:
        with _owned(root) as (path, database_root, unchanged):
            with _connection(path, database_root=database_root, readonly=False) as connection:
                connection.execute("BEGIN IMMEDIATE")
                previous = _read_state(connection, identity)
                evidence = staged.reverify(policy, clock=clock)
                if (
                    evidence["release_digest"] != selection["release_digest"]
                    or evidence["sequence"] != selection["sequence"]
                    or sorted(staged.artifacts) != selection["components"]
                ):
                    raise PlanError("authenticated release was not selected for this installation")
                staged.read_artifacts(policy, clock=clock)
                evidence = staged.reverify(policy, clock=clock)
                decision_digest = ReleaseSelection.from_dict(selection).selection_digest
                if previous:
                    if selection["sequence"] < previous["sequence"]:
                        raise PlanError("selected release is below durable admitted sequence")
                    if selection["sequence"] == previous["sequence"]:
                        if (
                            selection["release_digest"] != previous["release_digest"]
                            or decision_digest != previous["selection_digest"]
                        ):
                            raise PlanError("same-sequence release or selection content conflicts")
                        unchanged()
                        return _report(identity, previous, changed=False, status="admitted")
                receipt = {
                    "schema": "fleet-manager.client-admitted-release", "version": 1,
                    "sequence": selection["sequence"], "release_digest": selection["release_digest"],
                    "selection_digest": decision_digest, "selection": selection,
                    "admitted_at": evidence["verified_at"],
                }
                connection.execute(
                    "INSERT INTO admission VALUES (1, ?, ?) ON CONFLICT(singleton) DO UPDATE "
                    "SET sequence = excluded.sequence, receipt = excluded.receipt",
                    (selection["sequence"], canonical(receipt).decode("utf-8")),
                )
                unchanged()
                _commit(connection)
                if _read_state(connection, identity) != receipt:
                    raise PlanError("client admission commit reread differs; outcome is unknown")
                unchanged()
                return _report(identity, receipt, changed=True, status="admitted")
    except (OSError, sqlite3.Error, subprocess.SubprocessError):
        raise PlanError("client release admission could not establish durable state") from None
