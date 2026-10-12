from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager.ledger import OperationLedger
from fleet_manager.operations import OperationIntent, TargetReceipt
from fleet_manager.planning import PlanError


def intent(**changes):
    value = {"schema": "fleet-manager.operation-intent", "version": 1,
             "operation": "op-1", "authority": "fleet-1", "principal": "actor-1",
             "account": "account-1", "target": "node-1", "generation": 1,
             "command": "task.submit", "parameters": {"objective": "objective-1"}}
    value.update(changes)
    return OperationIntent.from_dict(value)


def receipt(request, status, sequence):
    value = request.to_dict()
    del value["parameters"]
    value.update(schema="fleet-manager.target-receipt", request_digest=request.request_digest,
                 sequence=sequence, status=status,
                 result_ref="outcome-1" if status in ("succeeded", "failed") else None)
    return TargetReceipt.from_dict(value)


def ledger(path):
    return OperationLedger(path, authority="fleet-1", installation="installation-1")


def test_admission_survives_restart_with_one_outbox_and_audit(tmp_path):
    path = tmp_path / "journal.db"
    store, request = ledger(path), intent()
    operation_id = store.admit(request)
    reopened = ledger(path)
    assert reopened.admit(request) == operation_id
    assert reopened.inspect(request)["state"] == "pending"
    with sqlite3.connect(path) as connection:
        for table in ("operations", "outbox", "audit"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_inspection_does_not_reserve_writer_slot_or_read_uncommitted_changes(tmp_path):
    store, request = ledger(tmp_path / "journal.db"), intent()
    store.admit(request)
    writer = sqlite3.connect(store.path, timeout=1, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE outbox SET state='uncertain'")
        assert store.inspect(request)["state"] == "pending"
    finally:
        writer.rollback()
        writer.close()
    assert store.inspect(request)["state"] == "pending"


def test_post_initialization_operations_do_not_recreate_removed_database(tmp_path):
    store, request = ledger(tmp_path / "journal.db"), intent()
    store.admit(request)
    store.path.unlink()
    for action in (
        lambda: store.inspect(request), lambda: store.admit(request),
        lambda: store.record_receipt(request, receipt(request, "accepted", 1)),
    ):
        with pytest.raises(sqlite3.OperationalError):
            action()
        assert not store.path.exists()


def test_verified_uri_handles_reserved_filename_characters(tmp_path):
    store, request = ledger(tmp_path / "journal # percent %.db"), intent()
    store.admit(request)
    assert store.inspect(request)["state"] == "pending"


def test_atomic_admission_rollback_on_audit_failure(tmp_path, monkeypatch):
    store, request = ledger(tmp_path / "journal.db"), intent()
    def fail(*args):
        raise RuntimeError("injected before commit")
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError):
        store.admit(request)
    assert store.inspect(request) is None
    with sqlite3.connect(store.path) as connection:
        for table in ("operations", "outbox", "audit"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_concurrent_duplicate_admission_and_content_conflict(tmp_path):
    store, request = ledger(tmp_path / "journal.db"), intent()
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: store.admit(request), range(16)))
    assert len(set(ids)) == 1
    with pytest.raises(PlanError, match="conflicting intent"):
        store.admit(intent(target="other"))
    assert store.admit(intent(account="other-account")) != ids[0]


def test_receipt_replay_ordering_and_unknown_not_success(tmp_path):
    store, request = ledger(tmp_path / "journal.db"), intent()
    with pytest.raises(PlanError, match="not admitted"):
        store.record_receipt(request, receipt(request, "accepted", 1))
    store.admit(request)
    store.record_receipt(request, receipt(request, "accepted", 1))
    store.record_receipt(request, receipt(request, "accepted", 1))
    store.record_receipt(request, receipt(request, "unknown", 2))
    assert store.inspect(request)["state"] == "uncertain"
    with pytest.raises(PlanError, match="sequence"):
        store.record_receipt(request, receipt(request, "succeeded", 2))
    with pytest.raises(PlanError, match="revert"):
        store.record_receipt(request, receipt(request, "accepted", 3))
    store.record_receipt(request, receipt(request, "succeeded", 3))
    assert store.inspect(request)["state"] == "outcome"
    with pytest.raises(PlanError, match="terminal"):
        store.record_receipt(request, receipt(request, "failed", 4))


def test_receipt_scope_failure_does_not_mutate(tmp_path):
    store, request = ledger(tmp_path / "journal.db"), intent()
    store.admit(request)
    wrong = receipt(intent(generation=2), "succeeded", 1)
    with pytest.raises(PlanError):
        store.record_receipt(request, wrong)
    assert store.inspect(request)["state"] == "pending"


def test_receipt_and_outbox_rollback_together_on_audit_failure(tmp_path, monkeypatch):
    store, request = ledger(tmp_path / "journal.db"), intent()
    store.admit(request)
    def fail(*args):
        raise RuntimeError("injected receipt commit failure")
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError):
        store.record_receipt(request, receipt(request, "succeeded", 1))
    assert store.inspect(request) == {"operation_id": 1, "state": "pending", "receipt": None}


def test_database_identity_schema_and_existing_database_refusal(tmp_path):
    path = tmp_path / "journal.db"
    store = ledger(path)
    with pytest.raises(PlanError, match="identity"):
        OperationLedger(path, authority="other", installation="installation-1")
    with pytest.raises(PlanError, match="authority"):
        store.admit(intent(authority="other"))
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(PlanError, match="schema"):
        store.admit(intent())
    with pytest.raises(PlanError, match="schema"):
        ledger(path)
    foreign = tmp_path / "foreign.db"
    with sqlite3.connect(foreign) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(PlanError, match="unrecognized"):
        ledger(foreign)


def test_view_only_foreign_database_is_preserved_not_adopted(tmp_path):
    path = tmp_path / "foreign-view.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE VIEW unrelated AS SELECT 1 AS value")
        before = connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    with pytest.raises(PlanError, match="unrecognized"):
        ledger(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_real_process_exit_leaves_uncommitted_receipt_rolled_back(tmp_path):
    path = tmp_path / "journal.db"
    store, request = ledger(path), intent()
    operation_id = store.admit(request)
    script = (
        "import os,sqlite3,sys;"
        "c=sqlite3.connect(sys.argv[1],isolation_level=None);"
        "c.execute('BEGIN IMMEDIATE');"
        "c.execute(\"UPDATE outbox SET state='uncertain' WHERE operation_id=?\",(int(sys.argv[2]),));"
        "os._exit(23)"
    )
    run = subprocess.run([sys.executable, "-I", "-c", script, str(path), str(operation_id)],
                         timeout=15, capture_output=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert run.returncode == 23
    assert ledger(path).inspect(request)["state"] == "pending"


def test_installed_journal_admission_in_separate_process(tmp_path):
    path, data = tmp_path / "journal.db", tmp_path / "intent.json"
    request = intent()
    data.write_text(json.dumps(request.to_dict()), encoding="utf-8")
    script = (
        "import json,sys;"
        "from fleet_manager.ledger import OperationLedger;"
        "from fleet_manager.operations import OperationIntent;"
        "s=OperationLedger(sys.argv[1],authority='fleet-1',installation='installation-1');"
        "s.admit(OperationIntent.from_dict(json.load(open(sys.argv[2]))))"
    )
    run = subprocess.run([sys.executable, "-I", "-c", script, str(path), str(data)],
                         timeout=15, capture_output=True, text=True,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert run.returncode == 0, run.stderr
    assert ledger(path).inspect(request)["state"] == "pending"
