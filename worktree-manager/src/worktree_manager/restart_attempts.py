"""Bounded frozen recovery identities; no launch or task authority lives here."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from .private_state import protect, reject_links
from .restart_evidence import PositiveEvidence, decode_recovery_json
from .restart_observer import HostEpoch


class RestartAttemptError(RuntimeError):
    """Attempt evidence is unavailable; never reset it or assume no prior launch."""


_TABLE = (
    "CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, "
    "candidate_key TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)"
)
_APPLICATION_ID = 0x57524131
_MAX_ATTEMPTS = 4096
_MAX_PAYLOAD_BYTES = 32768
_SOURCE_STATES = {"live", "shutdown-pending", "shutdown-interrupted", "unknown", "intentional"}
_FROZEN_STATES = {"live", "shutdown-pending", "shutdown-interrupted"}


def _epoch(epoch: HostEpoch) -> dict:
    value = asdict(epoch)
    if set(value) != {"platform", "environment_id", "boot_id"} or any(
        not isinstance(item, str) or not item or len(item) > 4096
        or any(ord(character) < 32 for character in item)
        for item in value.values()
    ) or epoch.platform not in {"linux", "wsl", "windows"}:
        raise ValueError("Invalid recovery attempt target epoch")
    return value


def _key(evidence: PositiveEvidence, target: dict) -> str:
    identity = {
        key: value for key, value in asdict(evidence).items()
        if key not in {"observed_at", "observation_sequence", "owner_revision", "mapping_revision"}
    }
    encoded = json.dumps(
        {"source": identity, "target": target}, sort_keys=True, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate(row: dict, target: dict) -> tuple[str, dict] | None:
    evidence = PositiveEvidence(**row["last_positive"])
    if row["disposition"] not in _SOURCE_STATES or type(row["end_consumed"]) is not bool:
        raise ValueError("Invalid recovery attempt source state")
    for field in ("detail", "notification_id"):
        value = row[field]
        if (
            not isinstance(value, str) or len(value) > 4096
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError(f"Invalid recovery attempt source {field}")
    if (
        row["disposition"] not in _FROZEN_STATES
        or not evidence.recovery_binding_qualified
        or evidence.platform != target["platform"]
        or evidence.environment_id != target["environment_id"]
        or evidence.boot_id == target["boot_id"]
        or row["detail"] in {"shutdown-cancelled", "native-end-after-cancel-unconfirmed"}
        or row["end_consumed"] and row["disposition"] != "shutdown-interrupted"
    ):
        return None
    return _key(evidence, target), {
        "state": "frozen", "target_epoch": target, "last_positive": asdict(evidence),
        **{key: row[key] for key in ("disposition", "detail", "notification_id", "end_consumed")},
    }


def _decode(attempt_id: str, key: str, payload: str) -> dict:
    if any(not isinstance(value, str) for value in (attempt_id, key, payload)):
        raise ValueError("Recovery attempt columns must contain text")
    if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Recovery attempt payload exceeds its budget")
    value = decode_recovery_json(payload)
    if (
        not isinstance(value, dict)
        or set(value) != {
            "state", "target_epoch", "last_positive", "disposition", "detail",
            "notification_id", "end_consumed",
        }
        or value["state"] != "frozen"
        or str(UUID(attempt_id)) != attempt_id
    ):
        raise ValueError("Invalid frozen recovery attempt")
    target = _epoch(HostEpoch(**value["target_epoch"]))
    candidate = _candidate(value, target)
    if candidate is None or candidate[0] != key:
        raise ValueError("Recovery attempt identity does not match its frozen evidence")
    return {"attempt_id": attempt_id, **value, "automatic_eligible": False}


class RestartAttempts:
    """Freeze once per source/target identity, retaining all unresolved attempts.

    A frozen attempt is not admitted, launched or acknowledged. Capacity refuses
    rather than evicting an unresolved attempt and permitting a duplicate.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "restart-recovery" / "attempts.sqlite3"

    def _protect(self) -> None:
        reject_links(self.path)
        protect(self.path.parent, directory=True)
        if self.path.exists():
            protect(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            try:
                protect(sidecar)
            except FileNotFoundError:
                pass

    def _exists(self) -> bool:
        reject_links(self.path)
        if self.path.exists():
            return True
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            if sidecar.exists():
                if self.path.exists():
                    return True
                raise RestartAttemptError("Recovery attempt database is missing but a sidecar remains.")
        return False

    def _initialize(self) -> None:
        if self._exists():
            return
        # Publish only a fully initialized schema; never expose an empty final file.
        with tempfile.TemporaryDirectory(prefix="attempt-init-", dir=self.path.parent) as temporary:
            staged = Path(temporary) / "attempts.sqlite3"
            with closing(sqlite3.connect(staged)) as connection, connection:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection, create=True)
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

    @staticmethod
    def _schema(connection: sqlite3.Connection, *, create: bool) -> None:
        objects = dict(connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ))
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        free = connection.execute("PRAGMA freelist_count").fetchone()[0]
        if not objects and version == application == free == 0 and pages in (0, 1):
            if not create:
                raise RestartAttemptError("Recovery attempt database is uninitialized.")
            connection.execute(_TABLE)
            connection.execute("PRAGMA user_version=1")
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
        elif objects != {"attempts": _TABLE} or version != 1 or application != _APPLICATION_ID:
            raise RestartAttemptError("Recovery attempt database has an incompatible schema.")

    @staticmethod
    def _read(connection: sqlite3.Connection) -> list[dict]:
        rows = connection.execute(
            "SELECT attempt_id, candidate_key, payload FROM attempts "
            "ORDER BY attempt_id LIMIT ?", (_MAX_ATTEMPTS + 1,),
        ).fetchall()
        if len(rows) > _MAX_ATTEMPTS:
            raise RestartAttemptError("Recovery attempt limit exceeded.")
        return [_decode(*row) for row in rows]

    def snapshot(self, *, recover_existing: bool = False) -> list[dict]:
        """Read without creating absent storage; only the lease holder recovers."""
        try:
            if not self._exists():
                return []
            self._protect()
            mode = "rw" if recover_existing else "ro"
            with closing(sqlite3.connect(
                self.path.absolute().as_uri() + f"?mode={mode}", uri=True, timeout=5,
            )) as connection:
                connection.execute("BEGIN")
                self._schema(connection, create=False)
                return self._read(connection)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            raise RestartAttemptError(f"Could not read recovery attempts: {exc}") from exc

    def freeze(self, rows: list[dict], epoch: HostEpoch) -> list[dict]:
        """Persist original candidate evidence before ordinary startup reconciliation."""
        try:
            target = _epoch(epoch)
            if not isinstance(rows, list) or len(rows) > _MAX_ATTEMPTS:
                raise ValueError("Recovery source snapshot exceeds its bounded budget")
            candidates = [
                candidate for row in rows
                if (candidate := _candidate(row, target)) is not None
            ]
            if not candidates:
                return self.snapshot(recover_existing=True)
            reject_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._protect()
            self._initialize()
            self._protect()
            with closing(sqlite3.connect(
                self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=5,
            )) as connection, connection:
                protect(self.path)
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                self._schema(connection, create=False)
                existing = self._read(connection)
                keys = {
                    _key(PositiveEvidence(**item["last_positive"]), item["target_epoch"])
                    for item in existing
                }
                for key, candidate in candidates:
                    if key in keys:
                        continue
                    if len(keys) >= _MAX_ATTEMPTS:
                        raise RestartAttemptError("Recovery attempt limit reached.")
                    payload = json.dumps(candidate, allow_nan=False, sort_keys=True)
                    if len(payload.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                        raise ValueError("Recovery attempt payload exceeds its budget")
                    connection.execute(
                        "INSERT INTO attempts(attempt_id, candidate_key, payload) VALUES (?, ?, ?)",
                        (str(uuid4()), key, payload),
                    )
                    keys.add(key)
                return self._read(connection)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            raise RestartAttemptError(f"Could not freeze recovery attempts: {exc}") from exc
