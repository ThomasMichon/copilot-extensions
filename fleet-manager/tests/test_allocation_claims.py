from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager.ledger import OperationLedger
from fleet_manager.operations import OperationIntent, TargetReceipt
from fleet_manager.planning import PlanError


def request(**changes):
    value = {"schema": "fleet-manager.operation-intent", "version": 1,
             "operation": "op-1", "authority": "fleet-1", "principal": "actor-1",
             "account": "account-1", "target": "node-1", "generation": 1,
             "command": "workspace.allocate",
             "parameters": {"repository": "repo-1", "objective": "objective-1"}}
    value.update(changes)
    return OperationIntent.from_dict(value)


def ledger(path):
    return OperationLedger(path, authority="fleet-1", installation="gateway-1")


def snapshot(path):
    with closing(sqlite3.connect(path)) as connection:
        return {table: connection.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("operations", "outbox", "audit", "allocation_claims",
                              "allocation_releases",
                              "receipts", "result_streams")}


def test_claim_admission_is_durable_and_exact_repeat_is_idempotent(tmp_path):
    path, intent = tmp_path / "gateway.db", request()
    store = ledger(path)
    assert store.inspect_allocation(resource="workspace-1") is None
    operation_id = store.admit_allocation(intent, resource="workspace-1")
    before = snapshot(path)
    reopened = ledger(path)
    assert reopened.admit_allocation(intent, resource="workspace-1") == operation_id
    assert reopened.admit(intent) == operation_id
    assert snapshot(path) == before
    assert reopened.inspect_allocation(resource="workspace-1") == {
        "resource": "workspace-1", "operation_id": operation_id, "intent": intent.to_dict()}
    assert reopened.inspect(intent)["state"] == "pending"
    assert before["allocation_claims"] == [("workspace-1", operation_id)]
    assert before["audit"] == [(1, operation_id, "admitted"),
                               (2, operation_id, "allocation-claimed")]


@pytest.mark.parametrize("changes", [
    {"target": "node-2"}, {"generation": 2},
    {"parameters": {"repository": "repo-2", "objective": "objective-1"}},
])
def test_conflicting_content_cannot_rebind_claim(tmp_path, changes):
    store, intent = ledger(tmp_path / "gateway.db"), request()
    store.admit_allocation(intent, resource="workspace-1")
    before = snapshot(store.path)
    with pytest.raises(PlanError, match="conflicting intent"):
        store.admit_allocation(request(**changes), resource="workspace-1")
    assert snapshot(store.path) == before


def test_operation_cannot_claim_another_resource(tmp_path):
    store, intent = ledger(tmp_path / "gateway.db"), request()
    store.admit_allocation(intent, resource="workspace-1")
    before = snapshot(store.path)
    with pytest.raises(PlanError, match="different resource"):
        store.admit_allocation(intent, resource="workspace-2")
    assert store.inspect_allocation(resource="workspace-2") is None
    assert snapshot(store.path) == before


@pytest.mark.parametrize("changes", [
    {"operation": "op-2"}, {"principal": "actor-2"}, {"account": "account-2"},
    {"operation": "op-2", "target": "node-2", "generation": 2},
])
def test_resource_is_exclusive_across_operation_account_principal_and_target(tmp_path, changes):
    store = ledger(tmp_path / "gateway.db")
    store.admit_allocation(request(), resource="workspace-1")
    before = snapshot(store.path)
    candidate = request(**changes)
    with pytest.raises(PlanError, match="already claimed"):
        store.admit_allocation(candidate, resource="workspace-1")
    assert store.inspect(candidate) is None
    assert snapshot(store.path) == before
    assert store.admit_allocation(candidate, resource="workspace-2") != 1


@pytest.mark.parametrize("status", ["accepted", "unknown", "succeeded", "failed"])
def test_receipt_outcome_never_releases_allocation(tmp_path, status):
    store, intent = ledger(tmp_path / "gateway.db"), request()
    store.admit_allocation(intent, resource="workspace-1")
    value = intent.to_dict()
    del value["parameters"]
    value.update(schema="fleet-manager.target-receipt", request_digest=intent.request_digest,
                 sequence=1, status=status,
                 result_ref="result-1" if status in ("succeeded", "failed") else None)
    store.record_receipt(intent, TargetReceipt.from_dict(value))
    reopened = ledger(store.path)
    assert reopened.inspect_allocation(resource="workspace-1")["intent"] == intent.to_dict()
    with pytest.raises(PlanError, match="already claimed"):
        reopened.admit_allocation(request(operation="op-2"), resource="workspace-1")


@pytest.mark.parametrize("event", ["admitted", "allocation-claimed"])
def test_audit_failure_rolls_back_claim_intent_outbox_and_audit(tmp_path, monkeypatch, event):
    store = ledger(tmp_path / "gateway.db")
    store.admit_allocation(request(operation="existing"), resource="other-workspace")
    before = snapshot(store.path)
    original = store._event
    def fail(connection, operation_id, current):
        original(connection, operation_id, current)
        if current == event:
            raise RuntimeError("injected audit failure")
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        store.admit_allocation(request(), resource="workspace-1")
    assert snapshot(store.path) == before
    assert store.inspect(request()) is None
    assert store.inspect_allocation(resource="workspace-1") is None


def test_unclaimed_legacy_admission_cannot_be_claimed_retroactively(tmp_path):
    store, intent = ledger(tmp_path / "gateway.db"), request()
    store.admit(intent)
    before = snapshot(store.path)
    with pytest.raises(PlanError, match="no allocation claim"):
        store.admit_allocation(intent, resource="workspace-1")
    assert snapshot(store.path) == before


@pytest.mark.parametrize("resource", ["", "../workspace", 1, True, "x" * 129])
def test_invalid_resource_is_refused_before_admission(tmp_path, resource):
    store = ledger(tmp_path / "gateway.db")
    with pytest.raises(PlanError):
        store.admit_allocation(request(), resource=resource)
    with pytest.raises(PlanError):
        store.inspect_allocation(resource=resource)
    assert not snapshot(store.path)["operations"]


def test_wrong_command_and_authority_do_not_admit(tmp_path):
    store = ledger(tmp_path / "gateway.db")
    with pytest.raises(PlanError, match="workspace.allocate"):
        store.admit_allocation(
            request(command="task.submit", parameters={"objective": "objective-1"}),
            resource="workspace-1")
    with pytest.raises(PlanError, match="authority"):
        store.admit_allocation(request(authority="other"), resource="workspace-1")
    assert not snapshot(store.path)["operations"]


@pytest.mark.parametrize("version", [1, 2])
def test_upgrade_retains_existing_data_without_inventing_claims(tmp_path, version):
    path, intent = tmp_path / "gateway.db", request()
    store = ledger(path)
    store.admit(intent)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE allocation_claims")
        connection.execute("DROP TABLE allocation_releases")
        if version == 1:
            connection.execute("DROP TABLE result_streams")
        else:
            connection.execute("INSERT INTO result_streams VALUES('node-1','node-install-1',7)")
        connection.execute(f"PRAGMA user_version={version}")
        connection.commit()
    with pytest.raises(PlanError, match="schema"):
        store.admit_allocation(request(operation="op-2"), resource="workspace-1")
    upgraded = ledger(path)
    assert upgraded.inspect(intent)["state"] == "pending"
    assert upgraded.inspect_allocation(resource="workspace-1") is None
    if version == 2:
        assert upgraded.result_cursor(target="node-1", installation="node-install-1") == 7
    with pytest.raises(PlanError, match="no allocation claim"):
        upgraded.admit_allocation(intent, resource="workspace-1")
    upgraded.admit_allocation(request(operation="op-2"), resource="workspace-1")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_claim_inspection_is_query_only_and_missing_storage_is_not_recreated(tmp_path):
    store, intent = ledger(tmp_path / "gateway.db"), request()
    store.admit_allocation(intent, resource="workspace-1")
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM allocation_claims")
        assert store.inspect_allocation(resource="workspace-1")["intent"] == intent.to_dict()
        connection.rollback()
    store.path.unlink()
    for action in (lambda: store.inspect_allocation(resource="workspace-1"),
                   lambda: store.admit_allocation(intent, resource="workspace-1")):
        with pytest.raises(sqlite3.OperationalError):
            action()
        assert not store.path.exists()


PROCESS = """
import json,os,sys
from fleet_manager.ledger import OperationLedger
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import PlanError
s=OperationLedger(sys.argv[1],authority='fleet-1',installation='gateway-1')
r=OperationIntent.from_dict(json.loads(sys.argv[2]))
"""


def run_process(path, intent, body):
    return subprocess.run(
        [sys.executable, "-I", "-c", PROCESS + body, str(path), json.dumps(intent.to_dict())],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def test_installed_process_crash_before_commit_and_lost_reply_after_commit(tmp_path):
    path, intent = tmp_path / "gateway.db", request()
    store = ledger(path)
    crashed = run_process(path, intent, """
original=s._event
def crash(c,op,event):
    original(c,op,event)
    if event=='allocation-claimed':
        os._exit(23)
s._event=crash
s.admit_allocation(r,resource='workspace-1')
""")
    assert crashed.returncode == 23, crashed.stderr
    assert all(not rows for rows in snapshot(path).values())
    lost = run_process(path, intent, """
s.admit_allocation(r,resource='workspace-1')
os._exit(24)
""")
    assert lost.returncode == 24, lost.stderr
    before = snapshot(path)
    assert ledger(path).admit_allocation(intent, resource="workspace-1") == 1
    assert store.inspect_allocation(resource="workspace-1")["operation_id"] == 1
    assert snapshot(path) == before


def test_four_real_processes_compete_for_one_resource_without_orphan_admissions(tmp_path):
    path = tmp_path / "gateway.db"
    ledger(path)
    body = """
try:
    s.admit_allocation(r,resource='workspace-1')
except PlanError as error:
    if str(error)!='allocation resource is already claimed':
        raise
    sys.exit(2)
"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(
            lambda i: run_process(path, request(operation=f"op-{i}"), body), range(4)))
    assert sorted(run.returncode for run in runs) == [0, 2, 2, 2], [
        run.stderr for run in runs]
    rows = snapshot(path)
    assert len(rows["operations"]) == len(rows["outbox"]) == len(rows["allocation_claims"]) == 1
    assert len(rows["audit"]) == 2
    assert ledger(path).inspect_allocation(resource="workspace-1") is not None


def test_four_real_processes_repeat_one_claim_without_duplicate_audit(tmp_path):
    path, intent = tmp_path / "gateway.db", request()
    ledger(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: run_process(
            path, intent, "s.admit_allocation(r,resource='workspace-1')"), range(4)))
    assert all(run.returncode == 0 for run in runs), [run.stderr for run in runs]
    rows = snapshot(path)
    assert len(rows["operations"]) == len(rows["outbox"]) == len(rows["allocation_claims"]) == 1
    assert len(rows["audit"]) == 2
