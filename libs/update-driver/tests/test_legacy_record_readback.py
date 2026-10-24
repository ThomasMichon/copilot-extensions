"""Older request history remains inspectable without inventing recovery evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time

import agent_procutil
import pytest
from update_driver import (
    DriverError, RequestSpec, TrustedRunner, drain, enqueue, ensure_worker,
    kernel_boot_identity, recovery_status, status,
)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _legacy_rows(database):
    with sqlite3.connect(database) as db:
        return db.execute(
            "SELECT id,identity,spec,runner,state,exit_code,error,created_at,"
            "started_at,finished_at,boot_id,settled_boot_id,settled_at "
            "FROM requests ORDER BY id",
        ).fetchall()


def _legacy_state(area, *, state_name="legacy-state", runner_argv=None):
    root = area / state_name
    root.mkdir()
    database = root / "queue.sqlite3"
    boot = kernel_boot_identity()
    now = time.time()
    runner = {
        "argv": runner_argv if runner_argv is not None else [sys.executable, "-c", "pass"],
        "cwd": str(area),
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
            if runner_argv is not None:
                runner["argv"] = [*runner_argv, identifier]
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
    return root, database, boot, failure


def test_schema_two_failure_and_pending_history_remain_readonly(area):
    root, database, boot, failure = _legacy_state(area)
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


def test_explicit_legacy_repair_preserves_failure_and_executes_only_pending_work(area):
    from update_driver import recover_for_repair
    from test_process import collect, worker

    effects = area / "effects"
    root, database, boot, failure = _legacy_state(
        area, state_name="state",
        runner_argv=[
            sys.executable, "-c",
            "from pathlib import Path; import sys; "
            "effects=Path(sys.argv[1]).open('a'); "
            "effects.write(sys.argv[2]+'\\n'); effects.close()",
            str(effects),
        ],
    )
    original, pending = status(root=root)
    history = _legacy_rows(database)
    before = database.read_bytes()
    with pytest.raises(DriverError):
        recover_for_repair(root=root)
    assert database.read_bytes() == before
    assert not effects.exists()

    released = recover_for_repair(authorized=True, root=root)
    assert [row.id for row in released] == [original.id]
    repaired = status(original.id, root=root)
    assert repaired.state == original.state == "interrupted"
    assert repaired.exit_code is original.exit_code is None
    assert repaired.error == failure
    assert _legacy_rows(database) == history
    assert repaired.spec == original.spec and repaired.runner == original.runner
    assert (repaired.created_at, repaired.started_at, repaired.finished_at, repaired.boot_id) == (
        original.created_at, original.started_at, original.finished_at, boot,
    )
    assert repaired.recovery_authority == "operator_repair"
    assert repaired.released_at is not None
    assert repaired.settlement_kind is repaired.settled_at is repaired.settled_boot_id is None
    assert status(pending.id, root=root) == pending
    assert not effects.exists(), "queue release ran an installer"
    assert recover_for_repair(authorized=True, root=root) == ()
    assert status(original.id, root=root) == repaired

    process = worker(area)
    collect(process)
    assert status(pending.id, root=root).exit_code == 0
    assert effects.read_text().splitlines() == [pending.id], "ambiguous failed work was replayed"
    assert status(original.id, root=root) == repaired
    assert kernel_boot_identity() == boot


@pytest.mark.parametrize("entry", ["enqueue", "worker", "drain"])
def test_same_boot_legacy_marker_is_not_native_settlement_evidence(area, monkeypatch, entry):
    root, database, boot, _ = _legacy_state(area)
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE requests SET settled_at=?,settled_boot_id=? WHERE id=?",
            (time.time(), boot, "1" * 32),
        )
    before = database.read_bytes()

    def refuse_execution(*args, **kwargs):
        pytest.fail("unproven legacy settlement reached execution")

    monkeypatch.setattr(agent_procutil, "spawn_daemon", refuse_execution)
    with pytest.raises(DriverError):
        if entry == "enqueue":
            enqueue(
                RequestSpec("fixture", argv=("new-request",)),
                runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
                root=root,
            )
        elif entry == "worker":
            ensure_worker(
                (sys.executable, "-m", "update_driver", "worker", "--root", str(root)),
                root=root,
            )
        else:
            drain(root=root, executor=refuse_execution)
    assert database.read_bytes() == before
    assert kernel_boot_identity() == boot
