"""Logical recovery of an isolated proposed task, never execution or live queue repair."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any
import uuid

from .queue import TaskQueue


class _ProposalQueue(TaskQueue):
    def __init__(self, path: Path, identity: str) -> None:
        self._identity = identity
        super().__init__(path, blob_threshold=32)

    def _new_task_id(self) -> str:
        return self._identity


def _wire(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _draft_aware(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proposal_v2'").fetchone() is not None


def proposal_row(db: sqlite3.Connection) -> tuple[Any, ...]:
    if _draft_aware(db):
        [row] = db.execute("SELECT id,title,repo,payload,created,draft FROM proposal_v2")
        return tuple(row)
    [row] = db.execute("SELECT id,title,repo,payload,created FROM proposal_v1")
    return (*row, None)


class ProposalIntake:
    """Finite private intake. Each instance owns a fresh, exclusive queue for one proposal.

    The separate records store is the acknowledgement boundary. A failed
    cross-store write invalidates this generation; rebuild from the accepted
    prefix into a fresh directory. Do not mount this queue in a coordinator.
    """

    def __init__(self, *, records: Path, generation: Path, identity: str) -> None:
        self.records = records
        self.queue = _ProposalQueue(generation / "tasks.db", identity)
        self.identity = identity
        self._epoch = uuid.uuid4().hex
        self._usable = True

    @classmethod
    def create(cls, root: Path, *, title: str, repo: str, payload: str) -> ProposalIntake:
        root = root.absolute()
        root.mkdir(mode=0o700)
        records = root / "proposal.records"
        generation = root / "generation"
        generation.mkdir(mode=0o700)
        identity = uuid.uuid4().hex
        instance = cls(records=records, generation=generation, identity=identity)
        now = time.time()
        with closing(sqlite3.connect(records)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE writer (singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch TEXT NOT NULL)")
            db.execute("INSERT INTO writer VALUES(1,?)", (instance._epoch,))
            db.execute("CREATE TABLE proposal_v1 (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                       "repo TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL)")
            db.execute("CREATE TABLE steer_v1 (seq INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, "
                       "fields TEXT NOT NULL, created REAL NOT NULL, receipt TEXT NOT NULL)")
            instance.queue.create(title, repo=repo, payload_inline=payload, status="proposed", now=now)
            db.execute("INSERT INTO proposal_v1 VALUES (?,?,?,?,?)", (identity, title, repo, payload, now))
            db.commit()
        return instance

    def steer(self, key: str, fields: dict[str, Any]) -> dict[str, Any]:
        if not self._usable or not key or not fields:
            raise ValueError("unavailable proposal intake or empty request")
        encoded = _wire(fields)
        with closing(sqlite3.connect(self.records.as_uri() + "?mode=rw", uri=True)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT epoch FROM writer WHERE singleton=1").fetchone() != (self._epoch,):
                raise ValueError("superseded proposal writer")
            old = db.execute("SELECT fields,receipt FROM steer_v1 WHERE key=?", (key,)).fetchone()
            if old and old[0] != encoded:
                raise ValueError("request key already accepted with different fields")
            now = time.time()
            self._usable = False
            self.queue.submit_steer(self.identity, fields=fields, expected_status="proposed",
                                    idempotency_key=key, now=now)
            receipt = self.queue.steer_receipt(self.identity, key)
            if not receipt or receipt["wake_status"] != "not_requested" or receipt["wake"] is not None:
                raise ValueError("proposal receipt cannot carry a wake")
            if old and old[1] != _wire(receipt):
                raise ValueError("receipt differs from accepted operation")
            if not old:
                db.execute("INSERT INTO steer_v1(key,fields,created,receipt) VALUES (?,?,?,?)",
                           (key, encoded, now, _wire(receipt)))
                if _draft_aware(db):
                    db.execute("UPDATE proposal_v2 SET draft='null' WHERE id=?", (self.identity,))
            db.commit()
            self._usable = True
            return receipt

    def write_draft(self, fields: dict[str, Any] | None, *, expected_revision: str,
                    expected_draft_revision: str) -> dict[str, Any] | None:
        """Acknowledge this guarded save/clear only after the independent record commits."""
        with closing(sqlite3.connect(self.records.as_uri() + "?mode=rw", uri=True)) as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            if not self._usable or db.execute("SELECT epoch FROM writer").fetchall() != [(self._epoch,)]:
                raise ValueError("unavailable or superseded proposal writer")
            if not _draft_aware(db):
                # Legacy readers require proposal_v1. They must refuse instead
                # of returning an empty draft from a newer acknowledged history.
                db.execute("ALTER TABLE proposal_v1 RENAME TO proposal_v2")
                db.execute("ALTER TABLE proposal_v2 ADD COLUMN draft TEXT NOT NULL DEFAULT 'null'")
            self._usable = False
            if fields is None:
                task = self.queue.clear_card_draft(self.identity, expected_revision=expected_revision,
                                                   expected_draft_revision=expected_draft_revision)
            else:
                task = self.queue.save_card_draft(self.identity, fields=fields, expected_revision=expected_revision,
                                                  expected_draft_revision=expected_draft_revision)
            if db.execute("UPDATE proposal_v2 SET draft=? WHERE id=?",
                          (_wire(task.card_draft), self.identity)).rowcount != 1:
                raise ValueError("missing proposal draft record")
            db.commit()
            self._usable = True
            return task.card_draft

    @classmethod
    def recover(cls, records: Path, generation: Path, *, expected_epoch: str | None = None) -> ProposalIntake:
        """Caller supplies owner-private records and an absent offline destination."""
        records, generation = records.absolute(), generation.absolute()
        with closing(sqlite3.connect(records.as_uri() + "?mode=rw", uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            if expected_epoch is not None and db.execute("SELECT epoch FROM writer").fetchall() != [(expected_epoch,)]:
                raise ValueError("proposal owner changed; refresh the handoff offer")
            identity, title, repo, payload, created, draft = proposal_row(db)
            if not re.fullmatch(r"[a-f0-9]{32}", identity):
                raise ValueError("invalid original task identity")
            generation.mkdir(mode=0o700)
            restored = cls(records=records, generation=generation, identity=identity)
            restored.queue.create(title, repo=repo, payload_inline=payload, status="proposed", now=created)
            for key, fields, accepted, receipt in db.execute(
                "SELECT key,fields,created,receipt FROM steer_v1 ORDER BY seq"
            ):
                restored.queue.submit_steer(identity, fields=json.loads(fields), expected_status="proposed",
                                            idempotency_key=key, now=accepted)
                if _wire(restored.queue.steer_receipt(identity, key)) != receipt:
                    raise ValueError("reconstructed receipt differs; retain original records")
            if draft is not None:
                value = json.loads(draft)
                if value is not None and not (
                    isinstance(value, dict) and isinstance(value.get("fields"), dict)
                    and isinstance(value.get("revision"), str) and value["revision"]
                ):
                    raise ValueError("invalid acknowledged draft")
                with closing(sqlite3.connect(restored.queue.db_path)) as queue:
                    queue.execute("UPDATE tasks SET card_draft=? WHERE id=?",
                                  (draft if value is not None else None, identity))
                    queue.commit()
            if db.execute("UPDATE writer SET epoch=? WHERE singleton=1", (restored._epoch,)).rowcount != 1:
                raise ValueError("missing writer epoch")
            db.commit()
        return restored
