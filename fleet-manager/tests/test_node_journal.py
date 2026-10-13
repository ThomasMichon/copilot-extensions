from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager.ledger import OperationLedger
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent, TargetReceipt
from fleet_manager.planning import PlanError


def intent(**changes):
    value = {"schema": "fleet-manager.operation-intent", "version": 1,
             "operation": "op-1", "authority": "fleet-1", "principal": "actor-1",
             "account": "account-1", "target": "node-1", "generation": 1,
             "command": "task.submit", "parameters": {"objective": "objective-1"}}
    value.update(changes)
    return OperationIntent.from_dict(value)


def journal(path):
    return NodeJournal(path, authority="fleet-1", installation="node-install-1", target="node-1")


def outcome(request, status="succeeded", sequence=3):
    value = request.to_dict()
    del value["parameters"]
    value.update(schema="fleet-manager.target-receipt", request_digest=request.request_digest,
                 sequence=sequence, status=status, result_ref="effect-1")
    return TargetReceipt.from_dict(value)


def cooperative(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS effects(value TEXT)")
    connection.execute("INSERT INTO effects VALUES('effect-1')")
    return "effect-1"


def effect_count(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT count(*) FROM effects").fetchone()[0]


def test_acceptance_restart_and_conflicting_reuse(tmp_path):
    path, request = tmp_path / "node.db", intent()
    store = journal(path)
    accepted = store.accept(request)
    assert accepted.to_dict()["status"] == "accepted"
    assert journal(path).accept(request) == accepted
    assert len(store.results()) == 1
    for changed in (intent(generation=2), intent(parameters={"objective": "other"})):
        with pytest.raises(PlanError, match="conflicting"):
            store.accept(changed)
    assert store.inspect(intent(operation="other")) is None
    assert store.accept(intent(account="other")) != accepted


def test_atomic_acceptance_rolls_back_on_spool_failure(tmp_path, monkeypatch):
    store, request = journal(tmp_path / "node.db"), intent()
    def fail(*args):
        raise RuntimeError("injected spool failure")
    monkeypatch.setattr(store, "_append", fail)
    with pytest.raises(RuntimeError):
        store.accept(request)
    assert store.inspect(request) is None
    assert store.results() == []


def test_cooperative_effect_and_receipt_commit_together_and_retry_skips_effect(tmp_path):
    store, request = journal(tmp_path / "node.db"), intent()
    with pytest.raises(PlanError, match="not accepted"):
        store.commit_cooperative(request, cooperative)
    store.accept(request)
    result = store.commit_cooperative(request, cooperative)
    assert result.to_dict()["status"] == "succeeded"
    assert result.to_dict()["sequence"] == 2
    assert journal(store.path).commit_cooperative(request, cooperative) == result
    assert store.accept(request) == result
    assert effect_count(store.path) == 1
    result.check_intent(request)


def test_concurrent_duplicate_effect_is_applied_once(tmp_path):
    store, request = journal(tmp_path / "node.db"), intent()
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(lambda _: store.accept(request), range(16)))
    assert len(set(accepted)) == 1
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.commit_cooperative(request, cooperative), range(16)))
    assert len(set(results)) == 1
    assert effect_count(store.path) == 1


def test_cooperative_effect_rollback_on_receipt_failure(tmp_path, monkeypatch):
    store, request = journal(tmp_path / "node.db"), intent()
    store.accept(request)
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TABLE effects(value TEXT)")
    def fail(*args):
        raise RuntimeError("injected receipt failure")
    monkeypatch.setattr(store, "_append", fail)
    with pytest.raises(RuntimeError):
        store.commit_cooperative(request, cooperative)
    assert effect_count(store.path) == 0
    assert store.inspect(request).to_dict()["status"] == "accepted"


@pytest.mark.parametrize("sql", [
    "COMMIT", "ROLLBACK", "SAVEPOINT escape", "PRAGMA synchronous=OFF",
    "ATTACH DATABASE ':memory:' AS other",
])
def test_cooperative_callback_cannot_end_or_escape_transaction(tmp_path, sql):
    store, request = journal(tmp_path / "node.db"), intent()
    store.accept(request)
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TABLE effects(value TEXT)")
    def bad(connection):
        cooperative(connection)
        connection.execute(sql)
        return "effect-1"
    with pytest.raises(sqlite3.DatabaseError):
        store.commit_cooperative(request, bad)
    assert effect_count(store.path) == 0
    assert len(store.results()) == 1


def test_invalid_callback_result_rolls_back_effect(tmp_path):
    store, request = journal(tmp_path / "node.db"), intent()
    store.accept(request)
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TABLE effects(value TEXT)")
    def invalid(connection):
        cooperative(connection)
        return None
    with pytest.raises(PlanError, match="outcome reference"):
        store.commit_cooperative(request, invalid)
    assert effect_count(store.path) == 0


def test_external_attempt_is_unknown_before_effect_and_cannot_be_retried(tmp_path):
    store, request = journal(tmp_path / "node.db"), intent()
    store.accept(request)
    with ThreadPoolExecutor(max_workers=8) as pool:
        permissions = list(pool.map(lambda _: store.begin_external(request), range(16)))
    assert sum(permissions) == 1
    reopened = journal(store.path)
    assert reopened.inspect(request).to_dict()["status"] == "unknown"
    assert not reopened.begin_external(request)
    assert reopened.accept(request).to_dict()["status"] == "unknown"
    with pytest.raises(PlanError, match="uncertain"):
        reopened.commit_cooperative(request, cooperative)
    result = reopened.reconcile(request, outcome(request))
    assert reopened.reconcile(request, result) == result
    assert not reopened.begin_external(request)
    with pytest.raises(PlanError, match="uncertain"):
        reopened.reconcile(request, outcome(request, status="failed", sequence=4))


def test_reconciliation_correlation_and_sequence_refusals_preserve_unknown(tmp_path):
    store, request = journal(tmp_path / "node.db"), intent()
    store.accept(request)
    with pytest.raises(PlanError, match="uncertain"):
        store.reconcile(request, outcome(request))
    store.begin_external(request)
    for wrong in (outcome(intent(generation=2)), outcome(request, sequence=2),
                  outcome(request, sequence=4)):
        with pytest.raises(PlanError):
            store.reconcile(request, wrong)
    assert store.inspect(request).to_dict()["status"] == "unknown"


def test_spool_is_bounded_ordered_and_nondestructive(tmp_path):
    store, request = journal(tmp_path / "node # %.db"), intent()
    store.accept(request)
    store.commit_cooperative(request, cooperative)
    first = store.results(limit=1)
    second = store.results(after=first[0]["cursor"], limit=1)
    assert first[0]["receipt"].to_dict()["status"] == "accepted"
    assert second[0]["receipt"].to_dict()["status"] == "succeeded"
    assert store.results(after=second[0]["cursor"]) == []
    assert journal(store.path).results() == first + second
    for arguments in ({"after": True}, {"after": -1}, {"limit": 0}, {"limit": 257}):
        with pytest.raises(PlanError):
            store.results(**arguments)


def test_scope_identity_schema_and_lost_database_refusals(tmp_path):
    path, request = tmp_path / "node.db", intent()
    store = journal(path)
    store.accept(request)
    for changes in ({"authority": "other"}, {"target": "other"}):
        with pytest.raises(PlanError, match="differs"):
            store.accept(intent(**changes))
    with pytest.raises(PlanError, match="identity"):
        NodeJournal(path, authority="fleet-1", installation="other", target="node-1")
    with pytest.raises(PlanError, match="identity"):
        NodeJournal(path, authority="fleet-1", installation="node-install-1", target="other")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(PlanError, match="schema"):
        store.results()
    path.unlink()
    for call in (lambda: store.accept(request), lambda: store.inspect(request),
                 lambda: store.begin_external(request), lambda: store.results()):
        with pytest.raises(sqlite3.OperationalError):
            call()
        assert not path.exists()
    foreign = tmp_path / "foreign.db"
    with closing(sqlite3.connect(foreign)) as connection:
        connection.execute("CREATE VIEW unrelated AS SELECT 1")
    with pytest.raises(PlanError, match="unrecognized"):
        journal(foreign)
    gateway = tmp_path / "gateway.db"
    OperationLedger(gateway, authority="fleet-1", installation="node-install-1")
    with pytest.raises(PlanError, match="schema"):
        journal(gateway)


def test_queries_see_committed_snapshot_without_writer_reservation(tmp_path):
    store, request = journal(tmp_path / "node.db"), intent()
    accepted = store.accept(request)
    with closing(sqlite3.connect(store.path, isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("DELETE FROM receipts")
        assert store.inspect(request) == accepted
        assert len(store.results()) == 1
        writer.rollback()


PROCESS_PREFIX = """
import json,os,sys
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
s=NodeJournal(sys.argv[1],authority='fleet-1',installation='node-install-1',target='node-1')
r=OperationIntent.from_dict(json.loads(sys.argv[2]))
s.accept(r)
def effect(c):
    c.execute('CREATE TABLE IF NOT EXISTS effects(value TEXT)')
    c.execute("INSERT INTO effects VALUES('effect-1')")
"""


def run_process(path, request, body):
    return subprocess.run(
        [sys.executable, "-I", "-c", PROCESS_PREFIX + body, str(path),
         json.dumps(request.to_dict())],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def test_real_process_lost_reply_replays_original_effect_without_reexecution(tmp_path):
    path, request = tmp_path / "node.db", intent()
    gateway = OperationLedger(tmp_path / "gateway.db", authority="fleet-1", installation="gateway-1")
    gateway.admit(request)
    crashed = run_process(path, request, """
    return 'effect-1'
s.commit_cooperative(r,effect)
os._exit(23)
""")
    assert crashed.returncode == 23, crashed.stderr
    assert gateway.inspect(request)["state"] == "pending"
    store = journal(path)
    for row in store.results():
        gateway.record_receipt(request, row["receipt"])
    latest = store.inspect(request)
    gateway.record_receipt(request, latest)
    assert gateway.inspect(request)["state"] == "outcome"
    retried = run_process(path, request, """
    return 'effect-1'
s.commit_cooperative(r,effect)
""")
    assert retried.returncode == 0, retried.stderr
    assert store.inspect(request) == latest
    assert effect_count(path) == 1


def test_real_process_crash_before_effect_receipt_rolls_back_and_can_retry(tmp_path):
    path, request = tmp_path / "node.db", intent()
    crashed = run_process(path, request, """
    os._exit(24)
s.commit_cooperative(r,effect)
""")
    assert crashed.returncode == 24, crashed.stderr
    store = journal(path)
    assert store.inspect(request).to_dict()["status"] == "accepted"
    store.commit_cooperative(request, cooperative)
    assert effect_count(path) == 1


def test_real_process_external_crash_preserves_unknown_not_retry_permission(tmp_path):
    path, request = tmp_path / "node.db", intent()
    crashed = run_process(path, request, """
    return 'effect-1'
assert s.begin_external(r)
os._exit(25)
""")
    assert crashed.returncode == 25, crashed.stderr
    store = journal(path)
    assert not store.begin_external(request)
    assert store.inspect(request).to_dict()["status"] == "unknown"


def test_real_process_external_effect_lost_reply_blocks_duplicate_attempt(tmp_path):
    path, request = tmp_path / "node.db", intent()
    crashed = run_process(path, request, """
    return 'effect-1'
from pathlib import Path
assert s.begin_external(r)
Path(sys.argv[1]+'.effect').write_text('performed-once',encoding='utf-8')
os._exit(26)
""")
    assert crashed.returncode == 26, crashed.stderr
    retried = run_process(path, request, """
    return 'effect-1'
assert not s.begin_external(r)
assert s.inspect(r).to_dict()['status']=='unknown'
""")
    assert retried.returncode == 0, retried.stderr
    assert path.with_suffix(".db.effect").read_text(encoding="utf-8") == "performed-once"
    assert journal(path).inspect(request).to_dict()["status"] == "unknown"


def test_separate_processes_cooperatively_commit_one_effect(tmp_path):
    path, request = tmp_path / "node.db", intent()
    journal(path)
    body = """
    return 'effect-1'
s.commit_cooperative(r,effect)
"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: run_process(path, request, body), range(4)))
    assert all(run.returncode == 0 for run in runs), [run.stderr for run in runs]
    assert effect_count(path) == 1
    assert len(journal(path).results()) == 2
