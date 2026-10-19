"""Read scoped accounting scalars from a live SQLite snapshot.

Lexical ancestors stay pinned and file identities are checked around SQLite
reads. SQLite's stdlib filename API cannot bind its connection to our no-follow
read handles: hostile path swaps restored between checks remain a residual race.
"""

from __future__ import annotations

import math
import os
import sqlite3
import stat
import time
from contextlib import ExitStack, closing
from pathlib import Path
from typing import Any

from .sync.archive_directory import ArchiveDirectory
from .sync.provenance import is_link_or_reparse, windows_extended_path
from .sync.publication_directory import publication_directory

_REQUIRED = ("session_id", "created_at", "model")
_TEXT = (*_REQUIRED, "api_call_id", "agent_id", "parent_tool_call_id")
_INTEGERS = (
    "turn_index",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_nano_aiu",
)
_NUMBERS = ("request_multiplier", "duration_ms")
_COLUMNS = (*_TEXT, *_INTEGERS, *_NUMBERS)
_SIDECARS = ("-wal", "-shm", "-journal")
_BATCH_SIZE = 200


class UsageReadError(ValueError):
    """Accounting coverage could not be read safely or completely."""


class UsageStoreMissingError(UsageReadError):
    """The accounting store or its containing directory is absent."""


def _pin_file(
    directory: ArchiveDirectory,
    name: str,
    handles: ExitStack,
    *,
    required: bool = False,
) -> os.stat_result | None:
    try:
        info = directory.stat(name)
    except FileNotFoundError:
        if required:
            raise UsageStoreMissingError("accounting store is missing") from None
        return None
    if is_link_or_reparse(directory.path / name, info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise UsageReadError("accounting store path is unsafe")
    handle = handles.enter_context(directory.open(name))
    opened = os.fstat(handle.fileno())
    if not opened.st_ino or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
        raise UsageReadError("accounting store file identity could not be verified")
    return opened


def _check_files(
    directory: ArchiveDirectory,
    name: str,
    handles: ExitStack,
    identities: dict[str, os.stat_result],
) -> None:
    for suffix in ("", *_SIDECARS):
        member = f"{name}{suffix}"
        current = _pin_file(directory, member, handles, required=not suffix)
        previous = identities.get(member)
        if previous is not None and (
            current is None
            or (previous.st_dev, previous.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise UsageReadError("accounting store path changed")
        if current is not None:
            identities[member] = current


def _check_sqlite_names(path: Path, identities: dict[str, os.stat_result]) -> None:
    """Verify SQLite's filename route still names the descriptor-admitted files."""
    for suffix in ("", *_SIDECARS):
        candidate = Path(f"{path}{suffix}")
        try:
            named = os.stat(windows_extended_path(candidate), follow_symlinks=False)
        except FileNotFoundError:
            if candidate.name in identities:
                raise UsageReadError("accounting store path changed") from None
            continue
        held = identities.get(candidate.name)
        if (
            held is None
            or is_link_or_reparse(candidate, named.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino)
        ):
            raise UsageReadError("accounting store filename identity could not be verified")


def _validate_scalar(column: str, value: Any) -> None:
    if column == "created_at" and (not isinstance(value, str) or not value):
        raise UsageReadError("accounting store timestamp is unavailable")
    if value is None:
        return
    if column in _TEXT:
        valid = isinstance(value, str) and len(value) <= 2048
    elif column in _INTEGERS:
        valid = type(value) is int and value >= 0
    else:
        valid = (
            type(value) in (int, float)
            and value >= 0
            and (type(value) is int or math.isfinite(value))
        )
    if not valid:
        raise UsageReadError("accounting store contains invalid scalar values")


def _bounded_projection(columns: list[str]) -> str:
    """Do not materialize oversized text/blob values in the Python process."""
    checks = []
    expressions = []
    for column in columns:
        quoted = f'"{column}"'
        if column in _TEXT:
            valid = (
                f"(typeof({quoted}) = 'text' AND length({quoted}) <= 2048 "
                f"AND length(CAST({quoted} AS BLOB)) <= 8192)"
            )
        elif column in _INTEGERS:
            valid = f"(typeof({quoted}) = 'integer' AND {quoted} >= 0)"
        else:
            valid = f"(typeof({quoted}) IN ('integer', 'real') AND {quoted} >= 0)"
        valid = f"({quoted} IS NULL OR {valid})"
        checks.append(valid)
        expressions.append(f"CASE WHEN {valid} THEN {quoted} ELSE NULL END")
    return ", ".join([*expressions, f"CASE WHEN {' AND '.join(checks)} THEN 1 ELSE 0 END"])


def read_sqlite_usage(
    path: Path,
    session_ids: set[str],
    *,
    max_rows: int = 20000,
    timeout: float = 5.0,
) -> dict[str, list[dict[str, Any]]]:
    """Return allowlisted accounting rows for the admitted session IDs only.

    ``session_id`` is top-level only; ``values`` preserves the other present
    allowlisted columns, including nulls, without generating identifiers.
    A read-only transaction includes committed WAL data without copying files.
    Missing/schema-invalid stores, invalid scalars, and exceeded budgets raise
    ``UsageReadError``; callers never receive partial accounting as success.
    Filesystem/SQLite calls cannot be preempted by the progress callback; the
    wall budget is checked between calls and during SQLite VM execution.
    """
    if type(max_rows) is not int or not 0 < max_rows < 2**63 - 1:
        raise UsageReadError("max_rows must be a positive SQLite integer")
    if (
        type(timeout) not in (int, float)
        or timeout <= 0
        or timeout > (2**31 - 1) / 1000
        or not math.isfinite(timeout)
    ):
        raise UsageReadError("timeout must be a positive finite SQLite timeout")
    if not isinstance(session_ids, set) or any(
        not isinstance(session_id, str) or len(session_id) > 2048
        for session_id in session_ids
    ):
        raise UsageReadError("session_ids must contain bounded strings")
    if not session_ids:
        return {}

    deadline = time.monotonic() + timeout

    def check_budget() -> None:
        if time.monotonic() >= deadline:
            raise UsageReadError("accounting store read exceeded time budget")

    def progress() -> int:
        return int(time.monotonic() >= deadline)

    result: dict[str, list[dict[str, Any]]] = {}
    try:
        with ExitStack() as handles:
            directory = handles.enter_context(publication_directory(path.parent, create=False))
            absolute = directory.path / path.name
            identities: dict[str, os.stat_result] = {}
            _check_files(directory, path.name, handles, identities)
            _check_sqlite_names(absolute, identities)
            check_budget()
            with closing(
                sqlite3.connect(
                    f"{absolute.as_uri()}?mode=ro&cache=private",
                    uri=True,
                    timeout=max(0.0, deadline - time.monotonic()),
                )
            ) as connection:
                connection.set_progress_handler(progress, 1000)
                connection.execute("PRAGMA query_only = ON")
                connection.execute("BEGIN")
                table = connection.execute(
                    "SELECT type FROM sqlite_schema WHERE name = ?",
                    ("assistant_usage_events",),
                ).fetchone()
                _check_files(directory, path.name, handles, identities)
                _check_sqlite_names(absolute, identities)
                if table != ("table",):
                    raise UsageReadError("accounting store usage table is missing or unsupported")
                schema = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(assistant_usage_events)")
                }
                if not set(_REQUIRED).issubset(schema):
                    raise UsageReadError("accounting store required columns are missing")
                columns = [column for column in _COLUMNS if column in schema]
                projection = _bounded_projection(columns)
                admitted = sorted(session_ids)
                count = 0
                for offset in range(0, len(admitted), _BATCH_SIZE):
                    check_budget()
                    remaining_time = max(0, int((deadline - time.monotonic()) * 1000))
                    connection.execute(f"PRAGMA busy_timeout = {remaining_time}")
                    batch = admitted[offset : offset + _BATCH_SIZE]
                    placeholders = ", ".join("?" for _ in batch)
                    cursor = connection.execute(
                        f"SELECT {projection} FROM assistant_usage_events "  # noqa: S608 - fixed allowlist
                        f"WHERE session_id IN ({placeholders}) LIMIT ?",
                        (*batch, max_rows - count + 1),
                    )
                    for row in cursor:
                        check_budget()
                        count += 1
                        if count > max_rows:
                            raise UsageReadError("accounting store read exceeded row budget")
                        if row[-1] != 1:
                            raise UsageReadError("accounting store contains invalid scalar values")
                        values = dict(zip(columns, row[:-1], strict=True))
                        for column, value in values.items():
                            _validate_scalar(column, value)
                        session_id = values.pop("session_id")
                        if not isinstance(session_id, str) or session_id not in session_ids:
                            raise UsageReadError("accounting store returned an unscoped session")
                        result.setdefault(session_id, []).append(
                            {
                                "source": "session-store",
                                "session_id": session_id,
                                "values": values,
                            }
                        )
                check_budget()
                _check_files(directory, path.name, handles, identities)
                _check_sqlite_names(absolute, identities)
                check_budget()
    except UsageReadError:
        raise
    except FileNotFoundError:
        raise UsageStoreMissingError("accounting store is missing") from None
    except OSError:
        raise UsageReadError("accounting store path is unsafe or unreadable") from None
    except (sqlite3.Error, RuntimeError, ValueError):
        raise UsageReadError("accounting store could not be read safely") from None
    return result
