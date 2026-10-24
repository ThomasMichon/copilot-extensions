"""Positive native settlement is distinct from owner loss and canonical success."""

import sqlite3
import sys
from contextlib import closing

import pytest

from single_instance_lease.operation import OperationLock
from update_driver import (
    InterruptedError, ParticipantError, RequestSpec, StateError, TrustedRunner,
    drain, enqueue, foundation_state, kernel_boot_identity, status,
)
from update_driver import core


def legacy_queue(area):
    root = area / "state"
    root.mkdir()
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            """CREATE TABLE requests (
            id TEXT PRIMARY KEY, identity TEXT NOT NULL, spec TEXT NOT NULL,
            runner TEXT NOT NULL, state TEXT NOT NULL
            CHECK(state IN ('pending','running','completed','interrupted','blocked')),
            exit_code INTEGER, error TEXT, created_at REAL NOT NULL,
            started_at REAL, finished_at REAL, boot_id TEXT,
            settled_boot_id TEXT, settled_at REAL)"""
        )
        db.execute("CREATE UNIQUE INDEX pending_identity ON requests(identity) WHERE state='pending'")
        db.execute("PRAGMA user_version=2")
    return root


def request(area, root):
    return enqueue(
        RequestSpec("fixture"), runner=TrustedRunner((sys.executable,), str(area)), root=root,
    )


def version(root):
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def test_schema_two_reads_and_enqueue_do_not_migrate(area):
    root = legacy_queue(area)
    queued = request(area, root)
    assert queued.settlement_kind is None and version(root) == 2
    assert status(queued.id, root=root) == queued
    assert request(area, root).id == queued.id
    assert version(root) == 2


def test_exclusive_drain_migrates_schema_two_preserving_pending_identity(area):
    root = legacy_queue(area)
    queued = request(area, root)
    observed = []
    assert drain(root=root, executor=lambda tx: observed.append(tx.request.id) or 7) == 0
    assert observed == [queued.id] and version(root) == core.SCHEMA
    result = status(queued.id, root=root)
    assert result.state == "completed" and result.exit_code == 7
    assert result.settlement_kind is None and result.spec == queued.spec


def test_legacy_interruption_is_never_migrated_into_native_proof(area):
    root = legacy_queue(area)
    queued = request(area, root)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            "UPDATE requests SET state='interrupted',boot_id=?,error='old failure' WHERE id=?",
            (kernel_boot_identity(), queued.id),
        )
    before = status(queued.id, root=root)
    with pytest.raises(InterruptedError):
        drain(root=root, executor=lambda tx: pytest.fail("legacy fence executed"))
    assert version(root) == core.SCHEMA
    assert status(queued.id, root=root) == before
    assert before.settled_at is None and before.settlement_kind is None


def test_schema_two_changed_boot_proof_migrates_without_rewriting_history(area):
    root = legacy_queue(area)
    queued = request(area, root)
    current = kernel_boot_identity()
    older = current.split(":", 1)[0] + ":11111111-1111-4111-8111-111111111111"
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            """UPDATE requests SET state='interrupted',boot_id=?,finished_at=2,
            error='preserved failure',settled_boot_id=?,settled_at=3 WHERE id=?""",
            (older, current, queued.id),
        )
    prior = status(queued.id, root=root)
    assert prior.settlement_kind == "kernel_restart"
    assert drain(root=root, executor=lambda tx: pytest.fail("old failure replayed")) == 0
    assert status(queued.id, root=root) == prior and version(root) == core.SCHEMA


def test_schema_migration_requires_actual_installation_custody(area):
    root = legacy_queue(area)
    lock = OperationLock(root / "execution.lock")
    with core._database(root, writable=True) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError):
            core._upgrade_schema(db, root, lock)
    assert version(root) == 2


def test_failed_schema_migration_rolls_back_columns_and_version(area, monkeypatch):
    root = legacy_queue(area)
    queued = request(area, root)
    validate = core._validate_db

    def reject_new_schema(db):
        validate(db)
        if db.execute("PRAGMA user_version").fetchone()[0] == core.SCHEMA:
            raise StateError("synthetic migration verification failure")

    monkeypatch.setattr(core, "_validate_db", reject_new_schema)
    with pytest.raises(StateError, match="migration verification"):
        drain(root=root, executor=lambda tx: pytest.fail("uncommitted schema executed"))
    assert version(root) == 2
    assert status(queued.id, root=root) == queued


@pytest.mark.parametrize("participants_closed", [False, True])
def test_only_native_proof_and_closed_participants_settle_failed_execution(
    area, monkeypatch, participants_closed,
):
    from agent_procutil import owned_process

    root = area / "state"
    queued = request(area, root)

    def refused(argv, *, on_settled, **kwargs):
        on_settled()
        raise OSError("preparation refused before process creation")

    monkeypatch.setattr(owned_process, "run_caller_owned", refused)
    if not participants_closed:
        original_close = core.Transaction._close

        def failed_close(transaction):
            original_close(transaction)
            raise ParticipantError("abandoned execution")

        monkeypatch.setattr(core.Transaction, "_close", failed_close)
    with pytest.raises(OSError if participants_closed else ParticipantError):
        drain(root=root)
    failed = status(queued.id, root=root)
    assert failed.state == "interrupted" and failed.exit_code is None
    assert failed.settlement_kind == ("native_scope" if participants_closed else None)
    assert (failed.settled_at is not None) is participants_closed
    if participants_closed:
        with OperationLock(root / "execution.lock") as custody:
            started = foundation_state.begin(root, "a" * 64, custody)
            foundation_state.finish(root, started, custody, exit_code=0)
        assert request(area, root).id != queued.id
    else:
        with pytest.raises(InterruptedError):
            request(area, root)


@pytest.mark.parametrize("change", [
    {"settlement_kind": None},
    {"settlement_kind": "unrecognized"},
    {"settlement_kind": "kernel_restart"},
    {"settled_boot_id": None},
    {"settled_at": None},
    {"finished_at": None},
    {"error": None},
    {"state": "blocked"},
    {"state": "running"},
])
def test_native_settlement_requires_complete_typed_evidence(area, monkeypatch, change):
    from agent_procutil import owned_process

    root = area / "state"
    queued = request(area, root)

    def refused(argv, *, on_settled, **kwargs):
        on_settled()
        raise OSError("known preparation boundary")

    monkeypatch.setattr(owned_process, "run_caller_owned", refused)
    with pytest.raises(OSError):
        drain(root=root)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute(
            "UPDATE requests SET " + ",".join(f"{key}=?" for key in change) + " WHERE id=?",
            (*change.values(), queued.id),
        )
    with pytest.raises(StateError, match="positive-settlement"):
        status(queued.id, root=root)
    before = (root / "queue.sqlite3").read_bytes()
    with pytest.raises(StateError, match="positive-settlement"):
        request(area, root)
    with pytest.raises(StateError, match="positive-settlement"):
        drain(root=root, executor=lambda tx: pytest.fail("malformed proof admitted execution"))
    assert (root / "queue.sqlite3").read_bytes() == before
