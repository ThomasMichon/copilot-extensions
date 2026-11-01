"""Standalone SQLite agenda authority. No provider imports or cached cursor writes."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any, Iterator
from uuid import uuid4

from agenda import AgendaError, Snapshot, _number, _object, _read, _text, decode, inspect_documents
from agenda_operations import apply_operation, chains, fields
import agenda_migration

DATABASE = "agenda.sqlite3"
SCHEMA = "efforts.agenda-request"
MAX_REVISION = (1 << 63) - 1


def _revision(value: Any, label: str, *, increment: bool = False) -> int:
    number = _number(value, label)
    if number > MAX_REVISION:
        raise AgendaError(f"{label} exceeds the managed signed-63-bit revision bound")
    if increment and number == MAX_REVISION:
        raise AgendaError(f"{label} is exhausted; cannot increment its revision")
    return number


def encode(value: Any) -> str:
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                            allow_nan=False)
        result.encode("utf-8")
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise AgendaError(f"invalid JSON document: {exc}") from exc


def safe_path(path: Path, *, directory: bool = False) -> Path:
    """Refuse ambiguous names and symlink components, including database sidecars."""
    if ".." in path.parts:
        raise AgendaError("unsafe path: parent traversal")
    path = path.absolute()
    for component in [*reversed(path.parents), path]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise AgendaError(f"cannot inspect state path: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise AgendaError(f"unsafe symlink path: {component.name}")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise AgendaError("unsafe path component is not a directory")
        if component == path:
            if directory and not stat.S_ISDIR(info.st_mode):
                raise AgendaError("state home must be a directory")
            if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise AgendaError("state file must be a regular, unshared file")
    return path


def board_name(home: Path, value: Any) -> str:
    text = _text(value, "board path")
    name = Path(text)
    if name.is_absolute() or ".." in name.parts or "." == text or name.as_posix() != text:
        raise AgendaError("board path must be a canonical relative path")
    if name.name in {DATABASE, DATABASE + "-journal", DATABASE + "-wal", DATABASE + "-shm",
                     "cursor.json", agenda_migration.RECOVERY}:
        raise AgendaError("board path conflicts with store files")
    safe_path(home / name)
    return text


def validate_documents(home: Path, documents: Any) -> Snapshot:
    fields(documents, {"cursor", "boards"})
    cursor = _object(documents["cursor"], "cursor")
    _revision(cursor.get("revision"), "cursor revision")
    boards = _object(documents["boards"], "boards")
    decode(encode(cursor).encode(), "cursor")
    for name, board in boards.items():
        board_name(home, name)
        decode(encode(board).encode(), "board")
    # Validate every supplied board and every reference, not only queue[0].
    from agenda import validate_board

    for board in boards.values():
        validate_board(_object(board, "board"))

    def load(name: Any) -> dict[str, Any]:
        name = board_name(home, name)
        if name not in boards:
            raise AgendaError(f"missing referenced board: {name}")
        return boards[name]

    queue = cursor.get("queue")
    if not isinstance(queue, list):
        raise AgendaError("queue must be a list")
    if len(queue) != len(set(_text(name, "queued board") for name in queue)):
        raise AgendaError("duplicate queued board")
    for name in queue:
        load(name)
    snapshot = inspect_documents(home, cursor, load)
    for name in references(cursor):
        load(name)
    return snapshot


def references(cursor: dict[str, Any]) -> set[str]:
    """Retain referenced archived results as well as the active/deferred frontier."""
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "agenda" in value:
                names.add(_text(value["agenda"], "referenced board"))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(cursor)
    queue = cursor.get("queue")
    if not isinstance(queue, list):
        raise AgendaError("queue must be a list")
    names.update(_text(name, "queued board") for name in queue)
    return names


def import_documents(home: Path) -> dict[str, Any]:
    home = safe_path(home, directory=True)
    cursor = _read(safe_path(home / "cursor.json"))
    boards: dict[str, Any] = {}
    total = len(encode(cursor).encode())
    for name in references(cursor):
        boards[board_name(home, name)] = _read(safe_path(home / name))
        total += len(encode(boards[name]).encode()) + len(name.encode()) + 4
        if total > 1048576:
            raise AgendaError("imported managed state exceeds 1024 KiB")
    documents = {"cursor": cursor, "boards": boards}
    validate_documents(home, documents)
    return documents


def _schema(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
               "store_id TEXT NOT NULL, revision INTEGER NOT NULL, committed_at TEXT NOT NULL, "
               "documents TEXT NOT NULL, metadata TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS operations (operation_id TEXT PRIMARY KEY, "
               "revision INTEGER UNIQUE NOT NULL, request TEXT NOT NULL, result TEXT NOT NULL, "
               "documents TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS delivery (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
               "revision INTEGER NOT NULL, observation TEXT NOT NULL, status TEXT NOT NULL)")
    db.execute("PRAGMA user_version=1")


@contextmanager
def transaction(home: Path, *, create: bool = False, write: bool = False) -> Iterator[sqlite3.Connection]:
    home = safe_path(home, directory=True)
    if not home.is_dir():
        raise AgendaError("explicit state home must already exist")
    path = safe_path(home / DATABASE)
    for suffix in ("-journal", "-wal", "-shm"):
        safe_path(home / (DATABASE + suffix))
    if not create and not path.exists():
        raise AgendaError("managed store does not exist; explicitly init/import first")
    before = home.stat()
    try:
        db = sqlite3.connect(path.as_uri() + ("?mode=rwc" if create else "?mode=rw"),
                             uri=True, timeout=5, isolation_level=None)
        try:
            after = home.stat()
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise AgendaError("state home changed during open")
            safe_path(path)
            db.execute("PRAGMA synchronous=FULL")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise AgendaError("unsupported agenda journal mode; standalone store requires DELETE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and not create and (home / agenda_migration.RECOVERY).exists():
                raise AgendaError("initialization interrupted; retry the saved init request in "
                                  "agenda.init-recovery.json; never restore stale cursor.json")
            if version not in (0, 1) or (version == 0 and not create):
                raise AgendaError("unsupported or uninitialized agenda store version")
            if version == 0 and db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                raise AgendaError("refusing an unrelated SQLite database")
            # SQLite owns journal fsync and commit ordering; sync a newly-created directory entry.
            if create and os.name == "posix":
                fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if create:
                _schema(db)
            yield db
            db.execute("COMMIT")
        finally:
            if db.in_transaction:
                db.execute("ROLLBACK")
            db.close()
    except (sqlite3.Error, OSError) as exc:
        recovery = ("; adoption may be fenced: preserve cursor.json and agenda.init-recovery.json "
                    "and retry the saved init request" if create and
                    (home / agenda_migration.RECOVERY).exists() else "")
        raise AgendaError(f"agenda store transaction failed: {exc}{recovery}") from exc


def _state(db: sqlite3.Connection, home: Path) -> dict[str, Any]:
    row = db.execute("SELECT store_id, revision, committed_at, documents, metadata FROM state").fetchone()
    if row is None:
        raise AgendaError("agenda store has no accepted initialization")
    documents = decode(row[3].encode(), "stored documents", limit=1048576)
    snapshot = validate_documents(home, documents)
    if snapshot.revision != row[1]:
        raise AgendaError("stored cursor and commit revision disagree")
    return {"store_id": row[0], "revision": row[1], "committed_at": row[2],
            "documents": documents, "metadata": decode(row[4].encode(), limit=262144),
            "snapshot": asdict(snapshot)}


def read(home: Path, expected_revision: int | None = None,
         worktree: str | None = None) -> dict[str, Any]:
    if expected_revision is not None:
        _revision(expected_revision, "expected revision")
    with transaction(home) as db:
        result = _state(db, home)
        if expected_revision is not None and result["revision"] != expected_revision:
            raise AgendaError("revision changed; reread before continuing")
        if worktree is not None and result["snapshot"]["worktree"] != worktree:
            raise AgendaError("cursor belongs to a different worktree")
        status = db.execute("SELECT status FROM delivery").fetchone()
        result["delivery"] = decode(status[0].encode()) if status else None
        return result


def history(home: Path, limit: int = 100, before_revision: int | None = None) -> list[dict[str, Any]]:
    if not 1 <= _number(limit, "history limit", 1) <= 100:
        raise AgendaError("history limit must be <= 100; paginate with before-revision")
    if before_revision is not None:
        _revision(before_revision, "before revision")
    with transaction(home) as db:
        _state(db, home)
        rows = db.execute(
            "SELECT result FROM operations WHERE (? IS NULL OR revision < ?) "
            "ORDER BY revision DESC LIMIT ?", (before_revision, before_revision, limit))
        return [decode(row[0].encode()) for row in rows]


def mutate(home: Path, request: dict[str, Any], *, session: str | None = None,
           actor: str | None = None, publisher_configured: bool = False,
           import_home: Path | None = None) -> tuple[dict[str, Any], bool]:
    fields(request, {"schema", "version", "expected_revision", "operation_id", "change"})
    if request["schema"] != SCHEMA or type(request["version"]) is not int or request["version"] != 1:
        raise AgendaError("unsupported agenda request schema/version")
    expected = _revision(request["expected_revision"], "expected revision")
    operation_id = _text(request["operation_id"], "stable operation id")
    change = _object(request["change"], "semantic change")
    initializing = change.get("operation") == "init"
    if initializing:
        fields(change, {"operation"}, {"documents"})
        if expected != 0:
            raise AgendaError("init requires expected revision 0")
        if ("documents" in change) == (import_home is not None):
            raise AgendaError("init needs either documents or explicit --import-home")
    elif import_home is not None:
        raise AgendaError("--import-home is only supported for init")
    for text, label in ((session, "session"), (actor, "actor")):
        if text is not None:
            _text(text, label)
            if len(encode(text).encode()) > 1024:
                raise AgendaError(f"{label} metadata exceeds 1 KiB")
    if len(encode(operation_id).encode()) > 1024:
        raise AgendaError("operation id exceeds 1 KiB")
    # Metadata is caller-asserted, never inferred from environment or source text.
    identity = {"actor": actor, "session": session,
                "qualification": "caller-asserted" if actor or session else "unknown"}
    canonical = encode({"request": request, "identity": identity,
                        "import_home": str(import_home.absolute()) if import_home else None})
    decode(encode(request).encode(), "request")
    # Reject a new lane's unincrementable input before even creating a DB marker.
    if initializing and not safe_path(home / DATABASE).exists():
        recovery = agenda_migration.recover(
            home, canonical, checked_path=safe_path, encoder=encode)
        if recovery is not None:
            initial_cursor = _object(recovery[0]["cursor"], "initial cursor")
        elif import_home is not None:
            initial_cursor = _read(safe_path(import_home / "cursor.json"))
        else:
            initial_cursor = _object(_object(change["documents"], "documents").get("cursor"),
                                     "initial cursor")
        _revision(initial_cursor.get("revision"), "initial imported revision", increment=True)
    with transaction(home, create=initializing, write=True) as db:
        prior = db.execute("SELECT request, result FROM operations WHERE operation_id=?",
                           (operation_id,)).fetchone()
        if prior:
            if prior[0] != canonical:
                raise AgendaError("operation id reused with mismatched request or caller metadata")
            _state(db, home)
            return decode(prior[1].encode()), False
        existing = db.execute("SELECT revision FROM state").fetchone()
        now = datetime.now(timezone.utc)
        at = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
        if initializing:
            if existing is not None:
                raise AgendaError("store already initialized; reread its revision")
            if import_home is not None and import_home.absolute() != home.absolute():
                marker = import_home / DATABASE
                if marker.exists() or marker.is_symlink():
                    raise AgendaError("import source is managed; refusing stale legacy JSON inputs")
            recovery = agenda_migration.recover(
                home, canonical, checked_path=safe_path, encoder=encode)
            documents = (recovery[0] if recovery is not None else
                         import_documents(import_home) if import_home else deepcopy(change["documents"]))
            validate_documents(home, documents)
            original_revision = _revision(documents["cursor"]["revision"],
                                          "initial imported revision", increment=True)
            decode(encode(documents).encode(), "complete managed state", limit=1048576)
            if recovery is None and (home / "cursor.json").exists():
                original = import_documents(home)
                if original["cursor"] != documents["cursor"] or any(
                    documents["boards"].get(name) != board
                    for name, board in original["boards"].items()
                ):
                    raise AgendaError("adoption must preserve original cursor, boards and tighter limits; "
                                      "use semantic refinement after initialization")
            fence = agenda_migration.prepare(
                home, canonical, documents, checked_path=safe_path, encoder=encode)
            store_id = uuid4().hex
            revision = original_revision + 1
            metadata: dict[str, Any] = {"imported_revision": original_revision,
                                       "imported_at": at, "import_identity": identity,
                                       "legacy_fence": fence}
            for chain in chains(documents["cursor"]):
                for frame in chain:
                    frame.setdefault("entered_at", None)
                    frame.setdefault("occurrence_id", None)
                    frame.setdefault("timing_qualification",
                                     "unknown" if frame["entered_at"] is None else "caller-asserted")
        else:
            state = _state(db, home)
            if state["revision"] != expected:
                raise AgendaError("stale expected revision; reread before continuing")
            documents, store_id, metadata = state["documents"], state["store_id"], state["metadata"]
            revision = _revision(expected, "current revision", increment=True) + 1
            if change.get("operation") == "restore":
                branch_id = _text(change.get("id"), "branch id")
                if branch_id not in metadata.get("rechecks", {}):
                    raise AgendaError("restore requires an explicit eligible recheck accepted by this store")
            apply_operation(documents, change, at, now)
            if change.get("operation") == "recheck":
                metadata.setdefault("rechecks", {})[change["id"]] = revision
            elif change.get("operation") in ("defer", "restore"):
                metadata.get("rechecks", {}).pop(change["id"], None)
        documents["cursor"]["revision"] = revision
        snapshot = validate_documents(home, documents)
        metadata = {**metadata, "schema": "efforts.agenda-store", "version": 1,
                    "actor": identity, "operation_id": operation_id, "committed_at": at}
        observation = {"schema": "efforts.agenda-observation", "version": 1,
            "source": {"worktree": snapshot.worktree, "session": session, "store_id": store_id},
            "revision": revision, "committed_at": at, "snapshot": asdict(snapshot)}
        observation_text = encode(observation)
        oversized = len(observation_text.encode()) + 1 > 32768
        if oversized:
            observation_text = encode({"schema": "efforts.agenda-undeliverable", "version": 1,
                "revision": revision, "error": {"code": "observation-too-large",
                    "message": "Observation exceeds 32 KiB; no argv invoked."}})
        delivery = {"state": "pending" if publisher_configured else "not-configured",
                    "error": {"code": "awaiting-publish" if publisher_configured else "not-configured",
                              "message": "No delivery has been confirmed."}}
        if oversized and publisher_configured:
            delivery["error"] = decode(observation_text.encode())["error"]
        result = {"schema": "efforts.agenda-result", "version": 1, "persisted": True,
                  "store_id": store_id, "revision": revision, "operation_id": operation_id,
                  "operation": change["operation"], "source": observation["source"],
                  "committed_at": at, "actor": identity, "delivery": delivery}
        # Full state, immutable original result, event and pending observation share one barrier.
        encoded_documents = encode(documents)
        decode(encoded_documents.encode(), "complete managed state", limit=1048576)
        metadata_text, result_text = encode(metadata), encode(result)
        decode(metadata_text.encode(), "generated metadata", limit=262144)
        decode(result_text.encode(), "accepted result")
        db.execute("INSERT OR REPLACE INTO state VALUES (1,?,?,?,?,?)",
                   (store_id, revision, at, encoded_documents, metadata_text))
        db.execute("INSERT INTO operations VALUES (?,?,?,?,?)",
                   (operation_id, revision, canonical, result_text, encoded_documents))
        db.execute("INSERT OR REPLACE INTO delivery VALUES (1,?,?,?)",
                   (revision, observation_text, encode(delivery)))
        return result, True


def delivery_record(home: Path) -> tuple[int, dict[str, Any]]:
    with transaction(home) as db:
        _state(db, home)
        row = db.execute("SELECT revision, observation FROM delivery").fetchone()
        if row is None:
            raise AgendaError("store has no delivery record")
        return row[0], decode(row[1].encode())


def record_delivery(home: Path, revision: int, status: dict[str, Any]) -> dict[str, Any]:
    _revision(revision, "delivery revision")
    with transaction(home, write=True) as db:
        _state(db, home)
        changed = db.execute("UPDATE delivery SET status=? WHERE revision=?",
                             (encode(status), revision)).rowcount
        if not changed:
            return {"state": "pending", "error": {"code": "superseded",
                    "message": "A newer commit requires its own delivery; late reply not recorded."}}
        return status
