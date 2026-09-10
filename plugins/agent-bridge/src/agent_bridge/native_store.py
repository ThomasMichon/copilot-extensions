"""Durable native executions, separate from ACP SessionManager records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class NativeError(RuntimeError):
    def __init__(self, code: str, detail: str, status: int = 409) -> None:
        self.code, self.detail, self.status = code, detail, status
        super().__init__(detail)


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise NativeError("invalid_identity", "Expected a bounded portable identity", 400)
    return value


def signature(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class NativeStore:
    """SQLite serializes idempotent launch admission and active-venue exclusion."""

    def __init__(self, root: Path, name: str = "native-controller") -> None:
        self.root = Path(root)
        self.path = self.root / f"{name}.sqlite"
        marker = self.root / f".{name}.initialized"
        if self.root.is_symlink() or self.path.is_symlink():
            raise NativeError("state_unavailable", "Native state cannot be a symbolic link")
        if marker.exists() and not self.path.exists():
            raise NativeError("state_unavailable", "Native state was lost; refusing to assume no incumbent")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY, generation TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE, codespace TEXT NOT NULL,
                owner TEXT NOT NULL, signature TEXT NOT NULL,
                state TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL
            )""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS active_native_venue
                ON executions(codespace) WHERE state NOT IN ('stopped', 'rejected')""")
        if os.name != "nt":
            self.path.chmod(0o600)
        marker.touch(exist_ok=True)

    @contextmanager
    def _connection(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=10)
            with db:
                db.row_factory = sqlite3.Row
                yield db
        except sqlite3.DatabaseError as exc:
            raise NativeError("state_unavailable", "Native ownership state is unavailable") from exc
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _decode(row) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        try:
            value["data"] = json.loads(value["data"])
        except (ValueError, TypeError) as exc:
            raise NativeError("state_unavailable", "Native execution data is unreadable", 503) from exc
        if not isinstance(value["data"], dict):
            raise NativeError("state_unavailable", "Native execution data is not an object", 503)
        return value

    def get(self, execution_id: str, generation: str | None = None) -> dict:
        identifier(execution_id)
        with self._connection() as db:
            row = self._decode(db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone())
        if row is None:
            raise NativeError("not_found", "Native execution is not recorded", 404)
        if generation is not None and row["generation"] != generation:
            raise NativeError("identity_mismatch", "Native execution generation does not match")
        return row

    def active(self, codespace: str | None = None) -> list[dict]:
        query = "SELECT * FROM executions WHERE state NOT IN ('stopped','rejected')"
        params = ()
        if codespace is not None:
            query += " AND codespace=?"
            params = (codespace,)
        with self._connection() as db:
            return [self._decode(row) for row in db.execute(query, params).fetchall()]

    def reserve(
        self, execution_id: str, generation: str, request_id: str, codespace: str,
        owner: str, spec_hash: str, data: dict, *, strict_identity: bool = False,
    ) -> tuple[dict, bool]:
        for value in (execution_id, generation, request_id):
            identifier(value)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._decode(db.execute("SELECT * FROM executions WHERE request_id=?", (request_id,)).fetchone())
            if old:
                if (old["signature"], old["codespace"], old["owner"]) != (spec_hash, codespace, owner):
                    raise NativeError("request_conflict", "Request identity was reused for a different launch")
                if strict_identity and (old["id"], old["generation"]) != (execution_id, generation):
                    raise NativeError("identity_mismatch", "Recorded launch identity differs")
                return old, False
            active = db.execute(
                "SELECT id FROM executions WHERE codespace=? AND state NOT IN ('stopped','rejected')",
                (codespace,),
            ).fetchone()
            if active:
                raise NativeError("venue_busy", "A native execution already owns this venue")
            db.execute(
                "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?)",
                (execution_id, generation, request_id, codespace, owner, spec_hash,
                 "starting", json.dumps(data), time.time()),
            )
        return self.get(execution_id, generation), True

    def update(
        self, execution_id: str, generation: str, *, state: str | None = None,
        allowed_states: tuple[str, ...] | None = None, **changes,
    ) -> dict:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._decode(db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone())
            if row is None or row["generation"] != generation:
                raise NativeError("identity_mismatch", "Native execution changed before update")
            if allowed_states is not None and row["state"] not in allowed_states:
                raise NativeError("state_changed", "Native execution lifecycle changed")
            if row["state"] == "stopping" and state not in {"stopped", "rejected"}:
                state = "stopping"
            row["data"].update(changes)
            db.execute(
                "UPDATE executions SET state=?,data=?,updated=? WHERE id=? AND generation=?",
                (state or row["state"], json.dumps(row["data"]), time.time(), execution_id, generation),
            )
        return self.get(execution_id, generation)

    def tombstone(self, execution_id: str, generation: str, codespace: str, owner: str) -> tuple[dict, bool]:
        for value in (execution_id, generation):
            identifier(value)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
            if row:
                decoded = self._decode(row)
                if decoded["generation"] != generation:
                    raise NativeError("identity_mismatch", "Native generation changed")
                return decoded, False
            db.execute(
                "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?)",
                (execution_id, generation, execution_id, codespace, owner, "retired-before-launch",
                 "stopped", json.dumps({"retired": True, "represented": False, "noLaunch": True}), time.time()),
            )
        return self.get(execution_id, generation), True


def receipt(row: dict) -> dict:
    data = row["data"]
    return {
        "schema": "copilot-extensions.native-execution", "version": 1,
        "executionId": row["id"], "generation": row["generation"], "mode": "native",
        "codespace": row["codespace"], "owner": row["owner"], "state": row["state"],
        "sessionId": data.get("sessionId"), "represented": bool(data.get("represented")),
        "ready": row["state"] == "ready" and bool(data.get("represented")),
        "exitCode": data.get("exitCode"), "phase": data.get("phase"),
        "error": data.get("error"), "ports": data.get("ports", []),
        "recovery": data.get("recovery"),
    }
