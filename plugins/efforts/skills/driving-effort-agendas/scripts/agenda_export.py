"""Complete, read-only exports of explicitly selected context-qualified stores.

The SQLite member retains original stored text, not a paginated projection or a
replay. A manifest is a content/identity check, not a signature or restore grant.
Older observations retain an unknown projection time; matching an owner clock
frontier verifies their complete shape without inventing that missing timestamp.
Publication requires file and directory barriers, using native owned-directory
flush and write-through publication on Windows. Unsupported filesystems or
access fail closed; no administrative volume handle is opened.
"""

from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any, Iterator

from agenda import AgendaError, _object, _time, decode
import agenda_history as history
import agenda_history_contract as wire
from agenda_operations import fields
import agenda_store as store
import agenda_store_v2 as owner
from agenda_v2_projection import project
from agenda_v2_schema import MODE, validate

MANIFEST = "manifest.json"
PENDING = "manifest.pending.json"
SCHEMA = "efforts.agenda-export"
VERSION = 1
CAPABILITY = "agenda-complete-export-v1"
_WINDOWS = os.name == "nt"
# These are failure budgets, never truncation or retention policies.
MAX_BYTES = 1 << 30
DEADLINE_SECONDS = 120
COPY_BYTES = 1 << 20
BACKUP_PAGES = 256
MANIFEST_BYTES = 16384
TABLES = {
    "state": ("singleton", "store_id", "revision", "committed_at", "documents", "metadata"),
    "operations": ("operation_id", "revision", "request", "result", "documents"),
    "delivery": ("singleton", "revision", "observation", "status"),
    "agenda_records": ("id", "body"),
}
ORDER = {"state": "singleton", "operations": "revision",
         "delivery": "singleton", "agenda_records": "id"}


class ExportError(AgendaError):
    """A refused, incomplete, corrupt or unqualified export."""


class _Budget:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + DEADLINE_SECONDS

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise ExportError("agenda export deadline exceeded; no history was truncated")

    def progress(self) -> int:
        self.check()
        return 0


def _selection(worktree: str, store_id: str, expected_revision: int | None) -> None:
    wire.text(worktree, "selected worktree")
    wire.store_id(store_id)
    if expected_revision is not None:
        if store._revision(expected_revision, "expected revision") < 1:
            raise ExportError("expected revision must name an accepted revision")


def _directory(path: Path, *, empty: bool = False) -> Path:
    path = store.safe_path(Path(path), directory=True)
    if not path.is_dir() or path == path.parent:
        raise ExportError("export directory must explicitly name an existing non-root directory")
    info = path.stat()
    if empty:
        if not _WINDOWS:
            if os.name != "posix" or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
                raise ExportError("agenda export requires a supported directory durability barrier")
            if info.st_uid != os.geteuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ExportError("export destination must be owned and not group/world writable")
        if any(path.iterdir()):
            raise ExportError("export destination must be empty; never overwrite or adopt")
    return path


def _windows_kernel() -> Any:
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_error() -> OSError:
    import ctypes

    return ctypes.WinError(ctypes.get_last_error())


def _windows_flush_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = _windows_kernel()
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_WRITE + BACKUP_SEMANTICS opens this explicitly owned directory,
    # not a volume, and requires no elevation. Unsupported access/FS refuses.
    handle = kernel.CreateFileW(str(path), 0x40000000, 0x7, None, 3, 0x02000000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise _windows_error()
    failed = False
    try:
        if not kernel.FlushFileBuffers(handle):
            failed = True
            raise _windows_error()
    finally:
        closed = kernel.CloseHandle(handle)
        if not closed and not failed:
            raise _windows_error()


def _windows_publish(source: Path, destination: Path) -> None:
    import ctypes

    move = _windows_kernel().MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    move.restype = ctypes.c_int
    # WRITE_THROUGH without REPLACE_EXISTING: never overwrite an adopted name.
    if not move(str(source), str(destination), 0x8):
        raise _windows_error()


@contextmanager
def _pinned_directory(path: Path) -> Iterator[int | None]:
    before = path.stat()
    if _WINDOWS:
        # Probe the exact required native barrier before creating any member.
        _windows_flush_directory(path)
        fd = None
    else:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = path.stat() if fd is None else os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ExportError("export directory changed during open")
        if fd is not None:
            os.fsync(fd)
        yield fd
        after = store.safe_path(path, directory=True).stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ExportError("export directory changed during capture")
    finally:
        if fd is not None:
            os.close(fd)


def _sync_directory(path: Path, fd: int | None) -> None:
    if _WINDOWS:
        _windows_flush_directory(path)
    else:
        assert fd is not None
        os.fsync(fd)


def _schema(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise ExportError("complete export supports only context-qualified store format 2")
    with closing(sqlite3.connect(":memory:")) as expected:
        owner.schema(expected)
        query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        if db.execute(query).fetchall() != expected.execute(query).fetchall():
            raise ExportError("agenda export requires the exact declared owner tables/schema")
        for name in TABLES:
            if db.execute(f"PRAGMA table_xinfo({name})").fetchall() != expected.execute(
                    f"PRAGMA table_xinfo({name})").fetchall():
                raise ExportError("agenda table schema differs from the declared owner schema")
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ExportError("agenda database integrity check failed")


def _bounded_rows(db: sqlite3.Connection) -> None:
    """Reject malformed/oversize cells before owner APIs materialize their bodies."""
    for table, columns in TABLES.items():
        bounds = {
            "documents": 1048576, "metadata": 262144,
            "request": 262144, "result": 131072, "body": 131072,
            "observation": 32768, "status": 131072,
            "store_id": 32, "operation_id": 1024, "id": 1024, "committed_at": 1024,
        }
        for column in columns:
            if column not in bounds:
                continue
            if db.execute(
                f"SELECT 1 FROM {table} WHERE typeof({column})!='text' "
                f"OR length(CAST({column} AS BLOB)) NOT BETWEEN 1 AND ? LIMIT 1",
                (bounds[column],),
            ).fetchone():
                raise ExportError(f"invalid or oversized agenda {table}.{column}")
    if db.execute("SELECT COUNT(*) FROM state").fetchone()[0] != 1 or db.execute(
            "SELECT COUNT(*) FROM delivery").fetchone()[0] != 1:
        raise ExportError("agenda state/delivery singleton is missing or inconsistent")


def _documents(raw: str, records: dict[str, Any], worktree: str, revision: int) -> dict:
    value = decode(raw.encode("utf-8"), "accepted documents", limit=1048576)
    validate(value, records)
    if value["cursor"]["revision"] != revision or value["cursor"]["worktree"] != worktree:
        raise ExportError("accepted documents changed worktree or revision")
    return value


def _ledger(
    db: sqlite3.Connection, state: dict, source: wire.Source, budget: _Budget,
) -> None:
    records: dict[str, Any] = {}
    latest = None
    latest_documents = None
    expected = 1
    for operation, revision, request_text, documents_text in db.execute(
            "SELECT operation_id,revision,request,documents FROM operations ORDER BY revision"):
        budget.check()
        if type(revision) is not int or revision != expected:
            raise ExportError("accepted operation ledger is incomplete or non-contiguous")
        result = history._result(db, source, operation, revision)
        request = decode(request_text.encode(), "original accepted request", limit=262144)
        fields(request, {"request", "identity"}, {"initial_store_id"})
        if request.get("initial_store_id") is not None and (
                revision != 1 or request["initial_store_id"] != source.store_id):
            raise ExportError("accepted initial store identity disagrees with its store")
        fields(request["request"],
               {"schema", "version", "mode", "expected_revision", "operation_id", "change"})
        body = request["request"]
        change = _object(body["change"], "accepted semantic change")
        if (
            body["schema"] != store.SCHEMA or type(body["version"]) is not int
            or body["version"] != 2 or body["mode"] != MODE
            or type(body["expected_revision"]) is not int or body["expected_revision"] != revision - 1
            or body["operation_id"] != operation or request["identity"] != result["actor"]
            or change.get("operation") != result["operation"]
            or (revision == 1) != (result["operation"] == "init")
        ):
            raise ExportError("original accepted request and result identities disagree")
        if revision == 1 and change.get("worktree") != source.worktree:
            raise ExportError("initial accepted request names a different worktree")
        for record in result["records"]:
            identity = record["id"]
            if identity in records:
                raise ExportError("immutable record was accepted more than once")
            stored = db.execute("SELECT body FROM agenda_records WHERE id=?", (identity,)).fetchone()
            if stored is None or stored[0] != store.encode(record):
                raise ExportError("accepted immutable record is missing or changed")
            records[identity] = record
        latest_documents = _documents(documents_text, records, source.worktree, revision)
        latest = result
        expected += 1
    if latest is None or expected - 1 != state["revision"]:
        raise ExportError("complete accepted history does not reach the current revision")
    if records != state["records"]:
        raise ExportError("immutable record table is not exactly the complete accepted ledger")
    current_text = db.execute("SELECT documents FROM state").fetchone()[0]
    if latest_documents != state["documents"] or current_text != documents_text:
        raise ExportError("current documents differ from the final accepted documents")
    metadata = state["metadata"]
    fields(metadata, {"schema", "version", "mode", "actor", "operation_id", "committed_at"})
    if metadata != {
        "schema": "efforts.agenda-store", "version": 2, "mode": MODE,
        "actor": latest["actor"], "operation_id": latest["operation_id"],
        "committed_at": latest["committed_at"],
    } or latest["committed_at"] != state["committed_at"]:
        raise ExportError("current metadata does not identify the last accepted operation")
    revision, observation_text, status = db.execute(
        "SELECT revision,observation,status FROM delivery").fetchone()
    observation = decode(observation_text.encode())
    if revision != state["revision"] or not _delivery_matches(
            observation, state, latest["source"], budget):
        raise ExportError("delivery observation does not match the captured state")
    _object(decode(status.encode()), "delivery status")


def _delivery_matches(observation: dict, state: dict, source: dict, budget: _Budget) -> bool:
    accepted_at = _time(state["committed_at"], "accepted commit time")

    def matches(at: Any) -> bool:
        budget.check()
        candidate = {
            "schema": "efforts.agenda-observation", "version": 2, "source": source,
            "revision": state["revision"], "committed_at": state["committed_at"],
            "snapshot": asdict(project(state["documents"], state["records"], now=at)),
        }
        if len(store.encode(candidate).encode()) + 1 > 32768:
            candidate = {
                "schema": "efforts.agenda-undeliverable", "version": 1, "revision": state["revision"],
                "error": {"code": "observation-too-large",
                          "message": "Observation exceeds 32 KiB; no argv invoked."},
            }
        return observation == candidate

    if matches(accepted_at):
        return True
    # Older producers captured committed_at before apply/validation but projected
    # afterward, without recording that projection's time. The owner projection
    # changes only at these declared boundaries. Check each whole possible shape,
    # never mix temporal fields or claim that a matched boundary was the real
    # projection time. Work is bounded by the validated current deferred list.
    boundaries = set()
    for saved in state["documents"]["cursor"]["deferred"]:
        budget.check()
        boundary = _time(saved["revisit_at"], "deferred revisit")
        if boundary > accepted_at:
            boundaries.add(boundary)
    return any(matches(boundary) for boundary in sorted(boundaries))


def _table_hashes(db: sqlite3.Connection, budget: _Budget) -> dict:
    summaries = {}
    for name, columns in TABLES.items():
        digest, count = hashlib.sha256(), 0
        for row in db.execute(f"SELECT {','.join(columns)} FROM {name} ORDER BY {ORDER[name]}"):
            budget.check()
            raw = store.encode(list(row)).encode("utf-8")
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
            count += 1
        summaries[name] = {"rows": count, "sha256": digest.hexdigest()}
    return summaries


def _inspect(
    db: sqlite3.Connection, home: Path, worktree: str, store_id: str,
    expected_revision: int | None, budget: _Budget,
) -> dict:
    db.set_progress_handler(budget.progress, 1000)
    db.execute("PRAGMA query_only=ON")
    _schema(db)
    _bounded_rows(db)
    source, revision, committed_at = history._header(db, worktree, store_id)
    if expected_revision is not None and revision != expected_revision:
        raise ExportError("agenda export revision differs from expected revision")
    # Board names are logical database keys, not files to collect. Validate
    # their paths below a nonexistent owned namespace so a legitimate board
    # named manifest.json cannot collide with the exported manifest hardlink.
    logical_home = home / ".agenda-export-logical-boards"
    if logical_home.exists() or logical_home.is_symlink():
        raise ExportError("export contains an undeclared logical-board directory")
    state = owner.state(db, logical_home)
    budget.check()
    _ledger(db, state, source, budget)
    budget.check()
    return {"store_version": 2, "mode": MODE, "worktree": source.worktree,
            "store_id": source.store_id, "revision": revision, "committed_at": committed_at,
            "tables": _table_hashes(db, budget)}


def _size(db: sqlite3.Connection) -> int:
    size = db.execute("PRAGMA page_size").fetchone()[0] * db.execute(
        "PRAGMA page_count").fetchone()[0]
    if not 0 < size <= MAX_BYTES:
        raise ExportError(f"agenda export exceeds the {MAX_BYTES}-byte complete-copy budget")
    return size


def _member(path: Path, budget: _Budget) -> dict:
    path = store.safe_path(path)
    size = path.stat().st_size
    if not 0 < size <= MAX_BYTES:
        raise ExportError("export member is empty or exceeds the complete-copy budget")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_BYTES):
            budget.check()
            count += len(chunk)
            if count > MAX_BYTES:
                raise ExportError("export member grew beyond its complete-copy budget")
            digest.update(chunk)
    if count != size or path.stat().st_size != size:
        raise ExportError("export member changed while hashing")
    return {"bytes": size, "sha256": digest.hexdigest()}


def _new_file(directory: Path, name: str, directory_fd: int | None) -> int:
    store.safe_path(directory / name)
    if _WINDOWS:
        return os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                   0o600, dir_fd=directory_fd)


def _manifest_shape(manifest: dict) -> None:
    fields(manifest, {"schema", "version", "completeness", "store_version", "mode", "worktree",
                      "store_id", "revision", "committed_at", "tables", "members"})
    if (
        manifest["schema"] != SCHEMA or type(manifest["version"]) is not int
        or manifest["version"] != VERSION or manifest["completeness"] != "complete"
        or type(manifest["store_version"]) is not int or manifest["store_version"] != 2
        or manifest["mode"] != MODE
    ):
        raise ExportError("unsupported or incomplete agenda export manifest")
    if type(manifest["revision"]) is not int:
        raise ExportError("export revision must be an accepted integer revision")
    _selection(manifest["worktree"], manifest["store_id"], manifest["revision"])
    fields(manifest["members"], {store.DATABASE})
    fields(manifest["tables"], set(TABLES))
    for name, summary in (
        *manifest["members"].items(), *manifest["tables"].items(),
    ):
        count_key = "bytes" if name == store.DATABASE else "rows"
        fields(summary, {count_key, "sha256"})
        if type(summary[count_key]) is not int or summary[count_key] < 1:
            raise ExportError("export counts must be positive integers")
        if not isinstance(summary["sha256"], str) or re.fullmatch(
                "[0-9a-f]{64}", summary["sha256"]) is None:
            raise ExportError("export content digest is not a SHA-256 digest")


def _backup(source: sqlite3.Connection, destination: sqlite3.Connection, budget: _Budget) -> None:
    page_size = source.execute("PRAGMA page_size").fetchone()[0]

    def progress(status: int, remaining: int, total: int) -> None:
        budget.check()
        if total * page_size > MAX_BYTES:
            raise ExportError("agenda snapshot exceeds the complete-copy budget")
        if status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE):
            raise ExportError("agenda backup could not make bounded progress")

    _size(source)
    source.backup(destination, pages=BACKUP_PAGES, progress=progress, sleep=0.01)


def export_store(
    home: Path, destination: Path, *, worktree: str, store_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Export a complete consistent existing format-2 store without modifying it.

    Destination is an explicitly supplied, existing empty adopter-owned directory.
    Failures retain diagnostic partial files; no deletion, adoption or retry is
    performed. Budgets fail the whole export rather than dropping old history.
    """
    _selection(worktree, store_id, expected_revision)
    home = store.safe_path(Path(home), directory=True)
    destination = _directory(destination, empty=True)
    if home == destination or home in destination.parents or destination in home.parents:
        raise ExportError("source and export destination must be disjoint")
    budget = _Budget()
    try:
        with _pinned_directory(destination) as directory_fd:
            # Opening the source is passive and never creates a missing store.
            with store.transaction(home, readonly=True, store_version=2) as source:
                # This first read pins the snapshot before backup; a concurrent
                # writer cannot make the identity and ledger come from two cuts.
                source.set_progress_handler(budget.progress, 1000)
                _size(source)
                _schema(source)
                history._header(source, worktree, store_id)
                actual_revision = source.execute("SELECT revision FROM state").fetchone()[0]
                if expected_revision is not None and actual_revision != expected_revision:
                    raise ExportError("agenda export revision differs from expected revision")
                fd = _new_file(destination, store.DATABASE, directory_fd)
                os.close(fd)
                target = store.safe_path(destination / store.DATABASE)
                with closing(sqlite3.connect(target.as_uri() + "?mode=rw", uri=True)) as copied:
                    _backup(source, copied, budget)
            with store.transaction(destination, readonly=True, store_version=2) as snapshot:
                details = _inspect(snapshot, destination, worktree, store_id, actual_revision, budget)
            member = _member(target, budget)
            manifest = {"schema": SCHEMA, "version": VERSION, "completeness": "complete",
                        **details, "members": {store.DATABASE: member}}
            raw = (store.encode(manifest) + "\n").encode()
            if len(raw) > MANIFEST_BYTES:
                raise ExportError("export manifest exceeds its declared bound")
            # The final name is published without overwrite only after the
            # complete SQLite member and prepared manifest have reached fsync.
            # Windows FlushFileBuffers (via fsync) requires a write-capable
            # handle. This is the newly created member, never the source.
            with target.open("r+b") as stream:
                os.fsync(stream.fileno())
            fd = _new_file(destination, PENDING, directory_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(destination, directory_fd)
            budget.check()
            if set(item.name for item in destination.iterdir()) != {store.DATABASE, PENDING}:
                raise ExportError("export destination changed during capture")
            if _WINDOWS:
                _windows_publish(destination / PENDING, destination / MANIFEST)
            else:
                # Retain the prepared file: exclusive atomic publication
                # without deleting staging evidence or replacing a name.
                os.link(PENDING, MANIFEST, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                        follow_symlinks=False)
            _sync_directory(destination, directory_fd)
            return manifest
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ExportError(f"agenda export failed; partial destination retained: {exc}") from exc


def verify_export(
    destination: Path, *, worktree: str, store_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Verify all exported bytes, owner schema, identities and complete ledger.

    Verification is passive and does not initialize, migrate, publish or replay.
    POSIX publication retains the prepared manifest beside the published name;
    Windows write-through publication moves it. Transport may preserve or omit
    the prepared name, but never substitute different content for it.
    A matching manifest cannot authenticate its author or authorize restoration.
    """
    _selection(worktree, store_id, expected_revision)
    destination = _directory(destination)
    budget = _Budget()
    try:
        manifest_path = destination / MANIFEST
        # Publication creates a hardlink pair. Content-preserving transport need
        # not preserve inode identity, but must preserve both complete names.
        info = manifest_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink not in (1, 2):
            raise ExportError("export has no exclusively published manifest pair")
        pending = destination / PENDING
        try:
            pending_info = pending.lstat()
        except FileNotFoundError:
            pending_info = None
        if pending_info is None:
            if info.st_nlink != 1:
                raise ExportError("published manifest has an undeclared hardlink")
        else:
            same_inode = (info.st_dev, info.st_ino) == (pending_info.st_dev, pending_info.st_ino)
            if not stat.S_ISREG(pending_info.st_mode) or (
                (same_inode and (info.st_nlink != 2 or pending_info.st_nlink != 2))
                or (not same_inode and (info.st_nlink != 1 or pending_info.st_nlink != 1))
            ):
                raise ExportError("prepared and published manifest have unsafe file identities")
        store.safe_path(manifest_path.parent, directory=True)
        if info.st_size > MANIFEST_BYTES or (
                pending_info is not None and pending_info.st_size > MANIFEST_BYTES):
            raise ExportError("export manifest exceeds its declared bound")
        with manifest_path.open("rb") as stream:
            raw = stream.read(MANIFEST_BYTES + 1)
            manifest = decode(raw, "export manifest", limit=MANIFEST_BYTES)
        if pending_info is not None:
            with pending.open("rb") as stream:
                if stream.read(MANIFEST_BYTES + 1) != raw:
                    raise ExportError("prepared and published manifest bytes disagree")
        _manifest_shape(manifest)
        expected_names = {store.DATABASE, MANIFEST} | ({PENDING} if pending_info is not None else set())
        if set(item.name for item in destination.iterdir()) != expected_names:
            raise ExportError("export members are missing or undeclared")
        actual_member = _member(destination / store.DATABASE, budget)
        if manifest["members"][store.DATABASE] != actual_member:
            raise ExportError("export member size/hash mismatch")
        with store.transaction(destination, readonly=True, store_version=2) as db:
            _size(db)
            actual = _inspect(db, destination, worktree, store_id, expected_revision, budget)
        if manifest != {"schema": SCHEMA, "version": VERSION, "completeness": "complete",
                        **actual, "members": {store.DATABASE: actual_member}}:
            raise ExportError("manifest and complete owner snapshot disagree")
        if _member(destination / store.DATABASE, budget) != actual_member:
            raise ExportError("export changed during verification")
        return manifest
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ExportError(f"agenda export verification failed: {exc}") from exc
