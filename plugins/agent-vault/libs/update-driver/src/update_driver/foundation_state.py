"""Native foundation progress under the same installation execution authority."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from single_instance_lease.operation import OperationLock

from . import boot
from .participant import BorrowedCustody

SCHEMA = "copilot-extensions.installation-foundation-operation"
VERSION = 1
FILENAME = "foundation-operation.json"
ARTIFACT_DIRECTORY = "foundation"
MAX_BYTES = 32768


class FoundationStateError(RuntimeError):
    """Missing ownership, corrupt progress or unproved native settlement."""


class _SnapshotReplaced(FoundationStateError):
    """An ordinary progress file changed between its path and descriptor probes."""


@dataclass(frozen=True)
class FoundationOperation:
    operation_id: str
    source_hash: str
    boot_id: str
    state: str
    started_at: float
    finished_at: float | None = None
    exit_code: int | None = None
    error: str | None = None
    settled_boot_id: str | None = None
    settled_at: float | None = None

    @property
    def fenced(self) -> bool:
        return self.state in {"running", "interrupted"} and self.settled_at is None


def _root(root: Path) -> Path:
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise FoundationStateError("foundation root must be absolute and unlinked")
    return root


def _validate(value: object) -> FoundationOperation:
    fields = set(FoundationOperation.__dataclass_fields__)
    if (
        not isinstance(value, dict) or set(value) != fields | {"schema", "version"}
        or value.get("schema") != SCHEMA or type(value.get("version")) is not int
        or value["version"] != VERSION
    ):
        raise FoundationStateError("unsupported foundation operation schema")
    data = {key: value[key] for key in fields}
    for key, pattern in (("operation_id", r"[0-9a-f]{32}"), ("source_hash", r"[0-9a-f]{64}")):
        if not isinstance(data[key], str) or not re.fullmatch(pattern, data[key]):
            raise FoundationStateError("malformed foundation operation identity")
    if not boot.valid_boot_identity(data["boot_id"]):
        raise FoundationStateError("malformed foundation admission boot identity")
    state = data["state"]
    if not isinstance(state, str) or state not in {"running", "completed", "failed", "interrupted"}:
        raise FoundationStateError("malformed foundation operation state")
    for key in ("started_at", "finished_at", "settled_at"):
        number = data[key]
        if number is None and key != "started_at":
            continue
        try:
            valid = type(number) in (int, float) and math.isfinite(number) and number >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise FoundationStateError("malformed foundation operation timestamp")
    code, error = data["exit_code"], data["error"]
    if code is not None and (type(code) is not int or not -(1 << 63) <= code < (1 << 63)):
        raise FoundationStateError("malformed foundation exit status")
    if error is not None and (
        not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error)
    ):
        raise FoundationStateError("foundation errors must be bounded reason codes")
    if (
        (state == "running" and (data["finished_at"] is not None or code is not None or error is not None))
        or (state == "completed" and (data["finished_at"] is None or code != 0 or error is not None))
        or (state == "failed" and (data["finished_at"] is None or code in (None, 0) or error is None))
        or (state == "interrupted" and (data["finished_at"] is None or code is not None or error is None))
    ):
        raise FoundationStateError("inconsistent foundation operation outcome")
    settled, settled_at = data["settled_boot_id"], data["settled_at"]
    if (settled is None) != (settled_at is None) or (
        settled is not None and (
            state != "interrupted" or not boot.valid_boot_identity(settled)
            or settled == data["boot_id"]
            or settled.split(":", 1)[0] != data["boot_id"].split(":", 1)[0]
        )
    ):
        raise FoundationStateError("malformed foundation positive-settlement evidence")
    return FoundationOperation(**data)


def _plain(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise FoundationStateError("duplicate foundation progress field")
        value[key] = item
    return value


def _open_read(path: Path):
    if os.name != "nt":
        return os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Share deletion for atomic replacement; open the reparse point itself.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def read(root: Path) -> FoundationOperation | None:
    """Read a bounded snapshot; absence never creates or repairs anything."""
    attempts = 0
    while True:
        try:
            return _read_snapshot(root)
        except _SnapshotReplaced as error:
            attempts += 1
            if attempts == 3:
                raise FoundationStateError(
                    "foundation operation changed during repeated observation",
                ) from error


def _read_snapshot(root: Path) -> FoundationOperation | None:
    path = _root(root) / FILENAME
    try:
        before = path.lstat()
    except FileNotFoundError:
        try:
            (path.parent / ARTIFACT_DIRECTORY).lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise FoundationStateError(f"foundation artifacts unavailable: {type(error).__name__}") from error
        raise FoundationStateError("foundation progress missing for existing artifacts")
    except OSError as error:
        raise FoundationStateError(f"foundation state unavailable: {type(error).__name__}") from error
    try:
        if not _plain(before) or before.st_size > MAX_BYTES:
            raise FoundationStateError("foundation operation must be a bounded plain file")
        with _open_read(path) as stream:
            opened = os.fstat(stream.fileno())
            if not _plain(opened):
                raise FoundationStateError("foundation operation descriptor must be a plain file")
            if not os.path.samestat(before, opened):
                raise _SnapshotReplaced("foundation operation changed during observation")
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise FoundationStateError("foundation operation exceeds its size bound")
        return _validate(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object))
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise FoundationStateError(f"foundation state unavailable: {type(error).__name__}") from error


def _owned(root: Path, custody: OperationLock | BorrowedCustody) -> Path:
    root = _root(root)
    if isinstance(custody, BorrowedCustody):
        matches = custody.root == root
    else:
        matches = isinstance(custody, OperationLock) and custody.path == root / "execution.lock"
    if not matches:
        raise FoundationStateError("foundation requires the installation execution lock")
    custody.assert_owned()
    return root


def _move_windows(temporary: Path, path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    if not move(str(temporary), str(path), 0x1 | 0x8):  # REPLACE_EXISTING | WRITE_THROUGH
        raise ctypes.WinError(ctypes.get_last_error())


def _publish(temporary: Path, path: Path) -> None:
    if os.name == "nt":
        deadline = time.monotonic() + 2
        while True:
            try:
                _move_windows(temporary, path)
                break
            except OSError as error:
                # MoveFileEx can refuse even a delete-sharing reader until it closes.
                if error.winerror not in {5, 32, 33} or time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
        return
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write(
    root: Path, value: FoundationOperation, custody: OperationLock | BorrowedCustody,
) -> FoundationOperation:
    root = _owned(root, custody)
    document = {"schema": SCHEMA, "version": VERSION, **asdict(value)}
    value = _validate(document)
    path = root / FILENAME
    temporary = root / f".foundation-operation-{uuid.uuid4().hex}.tmp"
    created = False
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(document, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        custody.assert_owned()
        _publish(temporary, path)
    except OSError as error:
        raise FoundationStateError(f"foundation progress persistence failed: {type(error).__name__}") from error
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return value


def _admit_queue(root: Path, custody: OperationLock | BorrowedCustody) -> None:
    from . import core

    path = root / "queue.sqlite3"
    try:
        identity = path.lstat()
    except FileNotFoundError:
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                path.with_name(path.name + suffix).lstat()
            except FileNotFoundError:
                continue
            raise FoundationStateError("foundation queue sidecar exists without its database")
        return
    current = core._active_transaction.get()
    enclosing = (
        current is not None and current.root == root and current._lock is custody
    )
    with core._database(root, expected_identity=identity) as db:
        for row in db.execute(
            "SELECT * FROM requests WHERE state IN ('running','interrupted','blocked')",
        ):
            request = core._request(row, root)
            if request.settled_at is not None:
                continue
            if enclosing and request == current.request and request.state == "running":
                current.assert_owned()
                continue
            raise FoundationStateError("foundation fenced by unsettled queue transaction")


def begin(
    root: Path, source_hash: str, custody: OperationLock | BorrowedCustody,
) -> FoundationOperation:
    """Admit only an explicitly owned new bootstrap, never replay an uncertain one."""
    root = _owned(root, custody)
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise FoundationStateError("foundation requires a full source digest")
    prior = read(root)
    if prior is not None and prior.fenced:
        raise FoundationStateError("foundation fenced; explicit positive settlement required")
    _admit_queue(root, custody)
    try:
        admission_boot = boot.kernel_boot_identity()
    except Exception as error:
        raise FoundationStateError(f"foundation boot evidence unavailable: {type(error).__name__}") from error
    return _write(root, FoundationOperation(
        uuid.uuid4().hex, source_hash, admission_boot, "running", time.time(),
    ), custody)


def finish(
    root: Path, operation: FoundationOperation, custody: OperationLock, *,
    exit_code: int | None, error: str | None = None,
) -> FoundationOperation:
    """The trusted producer certifies settlement with an integer; uncertainty has none."""
    root = _owned(root, custody)
    if read(root) != operation or operation.state != "running":
        raise FoundationStateError("foundation completion does not match its active operation")
    state = "interrupted" if exit_code is None else ("completed" if exit_code == 0 else "failed")
    return _write(root, replace(
        operation, state=state, exit_code=exit_code, error=error, finished_at=time.time(),
    ), custody)


def reconcile_lost(root: Path, custody: OperationLock) -> FoundationOperation | None:
    """Free kernel custody proves owner loss, never descendant settlement."""
    root = _owned(root, custody)
    operation = read(root)
    if operation is not None and operation.state == "running":
        operation = _write(root, replace(
            operation, state="interrupted", finished_at=time.time(),
            error="owner_lost_custody",
        ), custody)
    return operation


def recover_after_restart(root: Path, custody: OperationLock) -> FoundationOperation | None:
    """Explicitly settle an old-boot fence, retaining the interrupted outcome."""
    root = _owned(root, custody)
    operation = read(root)
    if operation is None or not operation.fenced:
        return operation
    try:
        current = boot.kernel_boot_identity()
    except Exception as error:
        raise FoundationStateError(f"foundation boot evidence unavailable: {type(error).__name__}") from error
    if (
        not boot.valid_boot_identity(current) or current == operation.boot_id
        or current.split(":", 1)[0] != operation.boot_id.split(":", 1)[0]
    ):
        raise FoundationStateError("foundation recovery requires a different native kernel boot")
    if read(root) != operation:
        raise FoundationStateError("foundation recovery state changed during observation")
    return _write(root, replace(
        operation, state="interrupted", finished_at=operation.finished_at or time.time(),
        error=operation.error or "owner_lost_custody", settled_boot_id=current, settled_at=time.time(),
    ), custody)
