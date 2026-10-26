"""Protected launch evidence, never a queue or permission to replay a launch."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID

from .private_state import protect, reject_links, verify_private
from .self_install import default_root

RETENTION_SECONDS = 90 * 24 * 60 * 60
_SCHEMA_VERSION = 2
_UNKNOWN_LOCALITY = -1
_APPLICATION_ID = 0x574C4131
_ACTIONS = {"new", "resume", "open", "restore", "base", "bare-resume", "open-venue"}
_PHASES = {
    "confirmed", "refused", "resolution-requested", "resolved", "launch-requested", "launcher-returned",
    "restore-requested", "restore-returned", "embodiment-requested", "embodiment-returned",
    "target-admitted", "allocated", "copilot-started", "prompt-handoff", "unknown",
}
_OWNERS = {"manager", "engine", "daemon", "native"}
_CODES = {
    "unsupported", "locality-unresolved", "engine-error", "transport-error", "storage-error",
    "interrupted",
}
_TABLES = {
    "attempts": (
        "CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, submitted_at REAL NOT NULL, "
        "action TEXT NOT NULL, project TEXT NOT NULL, surface TEXT NOT NULL, "
        "machine TEXT, environment TEXT, provider TEXT, venue_id TEXT, "
        "is_local INTEGER NOT NULL, worktree_id TEXT, "
        "inputs TEXT)"
    ),
    "events": (
        "CREATE TABLE events (sequence INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL "
        "REFERENCES attempts(attempt_id), observed_at REAL NOT NULL, "
        "owner TEXT NOT NULL, phase TEXT NOT NULL, details TEXT NOT NULL)"
    ),
}
_INDEXES = {
    "attempts_time": "CREATE INDEX attempts_time ON attempts(submitted_at)",
    "events_attempt": "CREATE INDEX events_attempt ON events(attempt_id, sequence)",
}
_METADATA = (
    "attempt_id, submitted_at, action, project, surface, machine, environment, provider, venue_id, "
    "is_local, worktree_id"
)
MANAGER_OUTCOMES = ("unknown", "refused", "failed", "interrupted", "returned")
_MANAGER_SEQUENCE = (
    "(SELECT sequence FROM events "
    "WHERE events.attempt_id=observed.attempt_id AND owner='manager' "
    "ORDER BY sequence DESC LIMIT 1)"
)
_MANAGER_OUTCOME = (
    "manager_outcome(manager_sequence, manager_observed_at, manager_phase, manager_details)"
)
_OBSERVED_METADATA = (
    f"WITH observed AS (SELECT {_METADATA}, "
    "inputs IS NOT NULL AND submitted_at>? AS inputs_available FROM attempts), "
    "snapshots AS (SELECT observed.*, latest.sequence AS manager_sequence, "
    "latest.observed_at AS manager_observed_at, latest.phase AS manager_phase, "
    "latest.details AS manager_details FROM observed "
    f"LEFT JOIN events AS latest ON latest.sequence={_MANAGER_SEQUENCE}) "
    f"SELECT *, {_MANAGER_OUTCOME} AS manager_outcome FROM snapshots"
)


class LaunchAttemptError(RuntimeError):
    """Journal failure; messages must not contain submitted input or raw receipts."""


def _identity(value: str) -> None:
    if not isinstance(value, str) or UUID(value).hex != value:
        raise ValueError("Invalid launch-attempt identity")


def _text(value: object, *, optional: bool = False) -> bool:
    return optional and value is None or (
        isinstance(value, str) and bool(value) and "\0" not in value
    )


def _timestamp() -> float:
    value = time.time()
    if not math.isfinite(value) or value < 0:
        raise ValueError("Invalid journal clock")
    return value


def _metadata(row: sqlite3.Row, *, version: int) -> dict:
    result = dict(row)
    _identity(result["attempt_id"])
    if (
        result["action"] not in _ACTIONS or not _text(result["project"]) or not _text(result["surface"])
        or not isinstance(result["submitted_at"], (int, float))
        or not math.isfinite(result["submitted_at"]) or result["submitted_at"] < 0
        or result["is_local"] not in ((0, 1, _UNKNOWN_LOCALITY) if version == 2 else (0, 1))
        or result["inputs_available"] not in (0, 1)
        or not all(_text(result[key], optional=True) for key in (
            "machine", "environment", "provider", "venue_id", "worktree_id",
        ))
    ):
        raise ValueError("Invalid stored launch metadata")
    return {
        **result,
        "is_local": None if result["is_local"] == _UNKNOWN_LOCALITY else bool(result["is_local"]),
        "inputs_available": bool(result["inputs_available"]),
    }


def _validate_observation(owner: str, phase: str, details: dict) -> None:
    if owner not in _OWNERS or phase not in _PHASES or not isinstance(details, dict):
        raise ValueError("Invalid launch observation")
    for key, value in details.items():
        if key == "worktree_id":
            # Opaque engine IDs can contain Unicode, including combining marks.
            if (
                not isinstance(value, str) or not 1 <= len(value) <= 512
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError("Invalid launch observation worktree identity")
        elif key in {"request_id", "seed_id", "conversation_id"}:
            if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,512}", value) is None:
                raise ValueError("Invalid launch observation identity")
        elif key == "exit_code":
            if type(value) is not int:
                raise ValueError("Invalid launch exit code")
        elif key == "code":
            if value not in _CODES:
                raise ValueError("Invalid launch observation code")
        else:
            raise ValueError("Unsupported launch observation field")


def _manager_observation(sequence, observed_at, phase, details) -> dict | None:
    event = None if sequence is None else {
        "sequence": sequence, "observed_at": observed_at, "phase": phase,
        "details": json.loads(details),
    }
    if event is not None:
        _validate_observation("manager", event["phase"], event["details"])
        if (
            type(event["sequence"]) is not int or event["sequence"] <= 0
            or not isinstance(event["observed_at"], (int, float))
            or not math.isfinite(event["observed_at"]) or event["observed_at"] < 0
        ):
            raise ValueError("Invalid stored Manager observation")
    return event


def _manager_outcome(sequence, observed_at, phase, details) -> str:
    event = _manager_observation(sequence, observed_at, phase, details)
    if event is None:
        return "unknown"
    phase, details = event["phase"], event["details"]
    if phase == "refused":
        return "refused"
    if phase == "unknown" and details.get("code") == "interrupted":
        return "interrupted"
    if phase == "launcher-returned" and "exit_code" in details:
        return "returned" if details["exit_code"] == 0 else "failed"
    return "unknown"


def _observed_metadata(row: sqlite3.Row, *, version: int) -> dict:
    result = _metadata(row, version=version)
    event = _manager_observation(*(
        result.pop("manager_" + key) for key in ("sequence", "observed_at", "phase", "details")
    ))
    return {
        **result, "last_manager_event": event, "manager_outcome_scope": "manager-boundary",
    }


class LaunchAttempts:
    def __init__(self, root: Path | None = None) -> None:
        self.path = (root if root is not None else default_root()) / "launch-attempts" / "attempts.sqlite3"

    def _exists(self) -> bool:
        reject_links(self.path)
        exists = self.path.exists()
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            if sidecar.exists() and not exists:
                exists = self.path.exists()
                if not exists:
                    raise LaunchAttemptError("Launch journal is missing but a SQLite sidecar remains.")
        return exists

    def _protect(self) -> None:
        protect(self.path.parent, directory=True)
        if self.path.exists():
            protect(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            try:
                protect(sidecar)
            except FileNotFoundError:
                pass  # Another SQLite connection can finish its transaction here.

    def _verify_private(self) -> None:
        verify_private(self.path.parent, directory=True)
        verify_private(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            try:
                verify_private(sidecar, allow_inherited=True)
            except FileNotFoundError:
                pass

    def _verify_header(self) -> None:
        # SQLite mode=ro can still create WAL bookkeeping. Inspect before opening it.
        with self.path.open("rb") as stream:
            header = stream.read(100)
        if len(header) != 100 or header[:16] != b"SQLite format 3\0":
            raise LaunchAttemptError("Launch journal has an incompatible database header.")
        if header[18:20] != b"\x01\x01":
            raise LaunchAttemptError("Launch journal requires DELETE journaling.")
        if any(
            self.path.with_name(self.path.name + suffix).exists()
            for suffix in ("-wal", "-shm")
        ):
            raise LaunchAttemptError("Launch journal has an unexpected WAL sidecar.")
        if (
            int.from_bytes(header[60:64], "big") not in (1, _SCHEMA_VERSION)
            or int.from_bytes(header[68:72], "big") != _APPLICATION_ID
        ):
            raise LaunchAttemptError("Launch journal has an incompatible database schema.")

    @staticmethod
    def _schema(connection: sqlite3.Connection) -> int:
        objects = [tuple(row) for row in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' "
            "ORDER BY type, name, sql"
        )]
        expected = sorted([
            *(("table", name, sql) for name, sql in _TABLES.items()),
            *(("index", name, sql) for name, sql in _INDEXES.items()),
        ])
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if (
            connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or version not in (1, _SCHEMA_VERSION)
            or objects != expected
        ):
            raise LaunchAttemptError("Launch journal has an incompatible database schema.")
        return version

    @staticmethod
    def _upgrade(connection: sqlite3.Connection) -> None:
        # V2 retains V1's rows/schema, adding -1 for explicitly unknown locality.
        # Validate before admitting that value so a corrupt V1 row cannot gain meaning.
        for row in connection.execute(
            f"SELECT {_METADATA}, 0 AS inputs_available FROM attempts"
        ):
            _metadata(row, version=1)
        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")

    def _initialize(self) -> None:
        if self._exists():
            return
        with tempfile.TemporaryDirectory(prefix="journal-init-", dir=self.path.parent) as temporary:
            staged = Path(temporary) / self.path.name
            with closing(sqlite3.connect(staged)) as connection, connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                for statement in (*_TABLES.values(), *_INDEXES.values()):
                    connection.execute(statement)
                connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            protect(staged)
            try:
                os.link(staged, self.path)
            except FileExistsError:
                return
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

    @contextmanager
    def _connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        if write:
            reject_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._protect()
            self._initialize()
            self._protect()
        else:
            self._verify_private()
        self._verify_header()
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(
            self.path.absolute().as_uri() + f"?mode={mode}", uri=True, timeout=5,
        )) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.create_function("manager_outcome", 4, _manager_outcome, deterministic=True)
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA secure_delete=ON")
                connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = self._schema(connection)
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise LaunchAttemptError("Launch journal requires DELETE journaling.")
            if write and version == 1:
                self._upgrade(connection)
            yield connection

    def record_intent(
        self, attempt_id: str, *, action: str, project: str, surface: str,
        is_local: bool | None, machine: str | None = None, environment: str | None = None,
        provider: str | None = None, venue_id: str | None = None,
        worktree_id: str | None = None, prompt: str = "", options: dict | None = None,
    ) -> None:
        """Commit exact confirmed input once, before dismissal or any launch effect."""
        try:
            _identity(attempt_id)
            if (
                action not in _ACTIONS or not _text(project) or not _text(surface)
                or is_local is not None and type(is_local) is not bool
                or not all(_text(value, optional=True) for value in (
                    machine, environment, provider, venue_id, worktree_id,
                ))
                or not isinstance(prompt, str) or "\0" in prompt
                or options is not None and not isinstance(options, dict)
            ):
                raise ValueError("Invalid launch intent")
            inputs = json.dumps(
                {"prompt": prompt, "options": options if options is not None else {}},
                ensure_ascii=False, allow_nan=False, sort_keys=True,
            )
            metadata = (
                action, project, surface, machine, environment, provider, venue_id,
                _UNKNOWN_LOCALITY if is_local is None else int(is_local), worktree_id,
            )
            now = _timestamp()
            with self._connect(write=True) as connection:
                previous = connection.execute(
                    "SELECT action, project, surface, machine, environment, provider, venue_id, is_local, "
                    "worktree_id, inputs, submitted_at FROM attempts WHERE attempt_id=?", (attempt_id,),
                ).fetchone()
                if previous is not None:
                    if (
                        tuple(previous)[:-1] != (*metadata, inputs)
                        or previous["submitted_at"] <= now - RETENTION_SECONDS
                    ):
                        raise LaunchAttemptError("Launch-attempt identity was reused for different or expired input.")
                    return
                connection.execute(
                    "INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (attempt_id, now, *metadata, inputs),
                )
                connection.execute(
                    "INSERT INTO events(attempt_id, observed_at, owner, phase, details) "
                    "VALUES (?, ?, 'manager', 'confirmed', '{}')", (attempt_id, now),
                )
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise LaunchAttemptError("Could not save launch intent; launch has not been authorized.") from exc

    def append(self, attempt_id: str, *, owner: str, phase: str, **details: str | int) -> None:
        """Retain bounded owner observations, never prompt text or a raw exception."""
        try:
            _identity(attempt_id)
            _validate_observation(owner, phase, details)
            if phase == "confirmed":
                raise ValueError("Confirmation belongs to the immutable intent transaction")
            if not self._exists():
                raise LaunchAttemptError("Cannot append to a missing launch attempt.")
            with self._connect(write=True) as connection:
                connection.execute(
                    "INSERT INTO events(attempt_id, observed_at, owner, phase, details) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (attempt_id, _timestamp(), owner, phase, json.dumps(details, sort_keys=True)),
                )
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise LaunchAttemptError("Could not retain launch observation; inspect the original attempt.") from exc

    def entries(
        self, *, limit: int = 100, before: float | None = None,
        project: str | None = None, action: str | None = None,
        after: float | None = None, machine: str | None = None,
        environment: str | None = None, provider: str | None = None,
        venue_id: str | None = None, before_id: str | None = None,
        outcome: str | None = None,
    ) -> list[dict]:
        """Read bounded metadata only; absent storage remains absent."""
        try:
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ValueError("Invalid launch-journal read limit")
            for timestamp in (before, after):
                if timestamp is not None and (not math.isfinite(timestamp) or timestamp < 0):
                    raise ValueError("Invalid launch-journal time filter")
            if before_id is not None:
                _identity(before_id)
                if before is None:
                    raise ValueError("Launch cursor requires its timestamp")
            if action is not None and action not in _ACTIONS:
                raise ValueError("Invalid launch-journal action filter")
            if outcome is not None and outcome not in MANAGER_OUTCOMES:
                raise ValueError("Invalid Manager outcome filter")
            if not self._exists():
                return []
            clauses, values = [], []
            if before_id is not None:
                clauses.append("(submitted_at<? OR (submitted_at=? AND attempt_id<?))")
                values.extend((before, before, before_id))
            for column, value, operator in (
                ("submitted_at", before if before_id is None else None, "<"),
                ("project", project, "="), ("action", action, "="),
                ("submitted_at", after, ">="), ("machine", machine, "="),
                ("environment", environment, "="), ("provider", provider, "="),
                ("venue_id", venue_id, "="), ("manager_outcome", outcome, "="),
            ):
                if value is not None:
                    clauses.append(f"{column}{operator}?")
                    values.append(value)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            with self._connect() as connection:
                rows = connection.execute(
                    f"{_OBSERVED_METADATA}{where} "
                    "ORDER BY submitted_at DESC, attempt_id DESC LIMIT ?",
                    (_timestamp() - RETENTION_SECONDS, *values, limit),
                ).fetchall()
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                return [_observed_metadata(row, version=version) for row in rows]
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise LaunchAttemptError("Could not read launch-attempt metadata.") from exc

    def detail(
        self, attempt_id: str, *, include_input: bool = False,
        event_limit: int = 1000, after_sequence: int = 0,
    ) -> dict:
        """Input is opt-in and never returned after its original expiry deadline."""
        try:
            _identity(attempt_id)
            if type(include_input) is not bool:
                raise ValueError("Invalid private-input selection")
            if (
                type(event_limit) is not int or not 1 <= event_limit <= 1000
                or type(after_sequence) is not int or after_sequence < 0
            ):
                raise ValueError("Invalid launch-event page")
            if not self._exists():
                raise LaunchAttemptError("Launch attempt was not found.")
            with self._connect() as connection:
                row = connection.execute(
                    f"{_OBSERVED_METADATA} WHERE attempt_id=?",
                    (_timestamp() - RETENTION_SECONDS, attempt_id),
                ).fetchone()
                if row is None:
                    raise LaunchAttemptError("Launch attempt was not found.")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                result = _observed_metadata(row, version=version)
                events = []
                for event in connection.execute(
                    "SELECT sequence, observed_at, owner, phase, details FROM events "
                    "WHERE attempt_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (attempt_id, after_sequence, event_limit + 1),
                ):
                    details = json.loads(event["details"])
                    _validate_observation(event["owner"], event["phase"], details)
                    if (
                        type(event["sequence"]) is not int or event["sequence"] <= 0
                        or not isinstance(event["observed_at"], (int, float))
                        or not math.isfinite(event["observed_at"]) or event["observed_at"] < 0
                    ):
                        raise ValueError("Invalid stored launch observation")
                    events.append({**dict(event), "details": details})
                result["events"] = events[:event_limit]
                result["events_has_more"] = len(events) > event_limit
                result["next_event_sequence"] = (
                    result["events"][-1]["sequence"] if result["events_has_more"] else None
                )
                if include_input:
                    if not row["inputs_available"]:
                        raise LaunchAttemptError("Private launch input has expired or was erased.")
                    result["input"] = json.loads(connection.execute(
                        "SELECT inputs FROM attempts WHERE attempt_id=?", (attempt_id,),
                    ).fetchone()[0])
                    private_input = result["input"]
                    if (
                        not isinstance(private_input, dict) or set(private_input) != {"prompt", "options"}
                        or not isinstance(private_input["prompt"], str)
                        or "\0" in private_input["prompt"]
                        or not isinstance(private_input["options"], dict)
                    ):
                        raise ValueError("Invalid stored launch input")
                return result
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise LaunchAttemptError("Could not read launch-attempt detail.") from exc

    def expire(self) -> int:
        """Erase expired private input, retaining metadata and pending engine intent."""
        try:
            if not self._exists():
                return 0
            with self._connect(write=True) as connection:
                return connection.execute(
                    "UPDATE attempts SET inputs=NULL WHERE submitted_at<=? AND inputs IS NOT NULL",
                    (_timestamp() - RETENTION_SECONDS,),
                ).rowcount
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError) as exc:
            raise LaunchAttemptError("Could not erase expired private launch input.") from exc
