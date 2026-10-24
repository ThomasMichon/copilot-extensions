"""Older request history remains inspectable without inventing recovery evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time

from update_driver import kernel_boot_identity, recovery_status, status


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_schema_two_failure_and_pending_history_remain_readonly(area):
    root = area / "legacy-state"
    root.mkdir()
    database = root / "queue.sqlite3"
    boot = kernel_boot_identity()
    now = time.time()
    runner = {
        "argv": [sys.executable, "-c", "pass"], "cwd": str(area),
        "timeout": 7200, "env": [], "context_keys": [],
    }
    failure = "executor interrupted: RuntimeError; settlement required"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE requests (
            id TEXT PRIMARY KEY, identity TEXT NOT NULL, spec TEXT NOT NULL,
            runner TEXT NOT NULL, state TEXT NOT NULL
              CHECK(state IN ('pending','running','completed','interrupted','blocked')),
            exit_code INTEGER, error TEXT, created_at REAL NOT NULL,
            started_at REAL, finished_at REAL, boot_id TEXT,
            settled_boot_id TEXT, settled_at REAL)"""
        )
        db.execute(
            "CREATE UNIQUE INDEX pending_identity ON requests(identity) WHERE state='pending'",
        )
        db.execute("PRAGMA user_version=2")
        for identifier, pending in (("1" * 32, False), ("2" * 32, True)):
            spec = {
                "backend": "fixture", "argv": ["later"] if pending else [],
                "project": "", "cwd": "", "source": "", "context": [],
            }
            identity = hashlib.sha256(
                (_encoded(spec) + "\n" + _encoded(runner)).encode(),
            ).hexdigest()
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier, identity, _encoded(spec), _encoded(runner),
                    "pending" if pending else "interrupted", None,
                    None if pending else failure, now - .2,
                    None if pending else now - .1, None if pending else now,
                    None if pending else boot, None, None,
                ),
            )
    before = database.read_bytes()
    records = status(root=root)
    assert [record.id for record in records] == ["1" * 32, "2" * 32]
    original, pending = records
    assert original.state == "interrupted" and original.exit_code is None
    assert original.error == failure
    assert original.boot_id == boot and original.settled_at is None
    assert pending.state == "pending" and pending.started_at is None
    assert recovery_status(root=root).current_boot_id == boot
    assert database.read_bytes() == before
    assert kernel_boot_identity() == boot
