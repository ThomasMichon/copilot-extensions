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
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import PlanError


def request(**changes):
    value = {"schema": "fleet-manager.operation-intent", "version": 1,
             "operation": "op-1", "authority": "fleet-1", "principal": "actor-1",
             "account": "account-1", "target": "node-1", "generation": 1,
             "command": "workspace.allocate",
             "parameters": {"repository": "repo-1", "objective": "objective-1"}}
    value.update(changes)
    return OperationIntent.from_dict(value)


def node(path):
    return NodeJournal(path, authority="fleet-1", installation="node-install-1", target="node-1")


def effect(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS effects(value TEXT)")
    connection.execute("INSERT INTO effects VALUES('result-1')")
    return "result-1"


def effect_count(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT count(*) FROM effects").fetchone()[0]


def test_owned_allocation_effect_is_atomic_durable_and_receipt_replays_to_gateway(tmp_path):
    store, intent = node(tmp_path / "node.db"), request()
    gateway = OperationLedger(tmp_path / "gateway.db", authority="fleet-1", installation="gateway-1")
    gateway.admit_allocation(intent, resource="workspace-1")
    accepted = store.accept_allocation(intent, resource="workspace-1")
    assert accepted.to_dict()["status"] == "accepted"
    assert node(store.path).accept_allocation(intent, resource="workspace-1") == accepted
    result = store.commit_cooperative(intent, effect, resource="workspace-1")
    assert result.to_dict()["status"] == "succeeded"
    assert node(store.path).commit_cooperative(intent, effect, resource="workspace-1") == result
    assert store.accept_allocation(intent, resource="workspace-1") == result
    assert effect_count(store.path) == 1
    assert store.allocation_owner(resource="workspace-1") == intent
    assert gateway.reconcile_results(
        target="node-1", installation="node-install-1", after=0, entries=store.results()) == 2
    assert gateway.inspect(intent)["state"] == "outcome"
    assert gateway.inspect_allocation(resource="workspace-1")["intent"] == intent.to_dict()


@pytest.mark.parametrize("changes", [
    {"operation": "op-2", "generation": 2}, {"operation": "op-2"},
    {"principal": "actor-2"}, {"account": "account-2"},
])
def test_competing_owner_or_generation_is_refused_without_acceptance(tmp_path, changes):
    store, intent = node(tmp_path / "node.db"), request()
    store.accept_allocation(intent, resource="workspace-1")
    candidate = request(**changes)
    with pytest.raises(PlanError, match="already owned"):
        store.accept_allocation(candidate, resource="workspace-1")
    assert store.inspect(candidate) is None
    assert store.allocation_owner(resource="workspace-1") == intent
    assert len(store.results()) == 1
    store.commit_cooperative(intent, effect, resource="workspace-1")
    with pytest.raises(PlanError, match="already owned"):
        node(store.path).accept_allocation(candidate, resource="workspace-1")
    assert effect_count(store.path) == 1


def test_wrong_resource_content_or_unbound_operation_never_invokes_callback(tmp_path):
    store, intent = node(tmp_path / "node.db"), request()
    store.accept_allocation(intent, resource="workspace-1")
    other = request(operation="op-2")
    store.accept(other)
    def forbidden(_):
        pytest.fail("refused operation must not execute")
    for candidate, resource in (
        (intent, "workspace-2"), (request(generation=2), "workspace-1"),
        (other, "workspace-1"), (other, "workspace-2"),
    ):
        with pytest.raises(PlanError):
            store.commit_cooperative(candidate, forbidden, resource=resource)
    with pytest.raises(PlanError, match="does not own"):
        store.accept_allocation(other, resource="workspace-2")
    with pytest.raises(PlanError, match="does not own"):
        store.accept_allocation(intent, resource="workspace-2")
    with pytest.raises(PlanError):
        store.commit_cooperative(intent, forbidden)
    assert len(store.results()) == 2
    assert store.allocation_owner(resource="workspace-2") is None


@pytest.mark.parametrize("resource", ["", "../workspace", True, "x" * 129])
def test_invalid_resource_refuses_acceptance_and_inspection(tmp_path, resource):
    store = node(tmp_path / "node.db")
    with pytest.raises(PlanError):
        store.accept_allocation(request(), resource=resource)
    with pytest.raises(PlanError):
        store.allocation_owner(resource=resource)
    assert store.results() == []


def test_scope_command_and_external_allocation_paths_fail_explicitly(tmp_path):
    store, intent = node(tmp_path / "node.db"), request()
    for changes in ({"authority": "other"}, {"target": "other"},
                    {"command": "task.submit", "parameters": {"objective": "objective-1"}}):
        with pytest.raises(PlanError):
            store.accept_allocation(request(**changes), resource="workspace-1")
    store.accept_allocation(intent, resource="workspace-1")
    with pytest.raises(PlanError, match="effect-owner fence"):
        store.begin_external(intent)
    unbound = request(operation="op-2")
    store.accept(unbound)
    with pytest.raises(PlanError, match="effect-owner fence"):
        store.begin_external(unbound)
    task = request(command="task.submit", parameters={"objective": "objective-1"},
                   operation="task-1")
    store.accept(task)
    with pytest.raises(PlanError, match="resource binding"):
        store.commit_cooperative(task, effect, resource="workspace-1")
    assert store.inspect(intent).to_dict()["status"] == "accepted"
    assert store.inspect(unbound).to_dict()["status"] == "accepted"


def test_acceptance_and_owner_rollback_on_spool_failure(tmp_path, monkeypatch):
    store = node(tmp_path / "node.db")
    original = store._append
    def fail(*args):
        original(*args)
        raise RuntimeError("injected spool failure")
    monkeypatch.setattr(store, "_append", fail)
    with pytest.raises(RuntimeError):
        store.accept_allocation(request(), resource="workspace-1")
    assert store.inspect(request()) is None
    assert store.allocation_owner(resource="workspace-1") is None
    assert store.results() == []


def test_owner_storage_failure_rolls_back_acceptance_and_spool(tmp_path):
    store = node(tmp_path / "node.db")
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute(
            "CREATE TRIGGER refuse_owner BEFORE INSERT ON allocation_owners"
            " BEGIN SELECT RAISE(ABORT,'injected storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected storage"):
        store.accept_allocation(request(), resource="workspace-1")
    assert store.inspect(request()) is None
    assert store.allocation_owner(resource="workspace-1") is None
    assert store.results() == []


def test_owned_effect_rollback_on_receipt_failure_retains_acceptance_and_owner(tmp_path, monkeypatch):
    store, intent = node(tmp_path / "node.db"), request()
    accepted = store.accept_allocation(intent, resource="workspace-1")
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TABLE effects(value TEXT)")
    def fail(*_):
        raise RuntimeError("injected receipt failure")
    monkeypatch.setattr(store, "_append", fail)
    with pytest.raises(RuntimeError):
        store.commit_cooperative(intent, effect, resource="workspace-1")
    assert effect_count(store.path) == 0
    assert store.inspect(intent) == accepted
    assert store.allocation_owner(resource="workspace-1") == intent


def test_callback_owner_mutation_is_rolled_back_with_effect(tmp_path):
    store, intent = node(tmp_path / "node.db"), request()
    store.accept_allocation(intent, resource="workspace-1")
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TABLE effects(value TEXT)")
    def invalid(connection):
        effect(connection)
        connection.execute("DELETE FROM allocation_owners")
        return "result-1"
    with pytest.raises(PlanError, match="does not own"):
        store.commit_cooperative(intent, invalid, resource="workspace-1")
    assert effect_count(store.path) == 0
    assert store.allocation_owner(resource="workspace-1") == intent


def test_schema_one_upgrade_preserves_acceptance_and_effect_tables_without_claims(tmp_path):
    path, intent = tmp_path / "node.db", request()
    store = node(path)
    accepted = store.accept(intent)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE allocation_owners")
        connection.execute("PRAGMA user_version=1")
        connection.execute("CREATE TABLE effects(value TEXT)")
        connection.execute("INSERT INTO effects VALUES('legacy')")
        connection.commit()
    with pytest.raises(PlanError, match="schema"):
        store.inspect(intent)
    upgraded = node(path)
    assert upgraded.inspect(intent) == accepted
    assert effect_count(path) == 1
    assert upgraded.allocation_owner(resource="workspace-1") is None
    with pytest.raises(PlanError, match="does not own"):
        upgraded.accept_allocation(intent, resource="workspace-1")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("sql", [
    "CREATE TABLE allocation_owners(resource TEXT)",
    "CREATE VIEW allocation_owners AS SELECT 1 AS resource",
    "ALTER TABLE operations ADD COLUMN unexpected TEXT",
])
def test_schema_upgrade_refuses_reserved_collision_or_changed_core_without_mutation(tmp_path, sql):
    path = tmp_path / "node.db"
    node(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE allocation_owners")
        connection.execute("PRAGMA user_version=1")
        connection.execute(sql)
        before = connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    with pytest.raises(PlanError, match="unrecognized"):
        node(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


PROCESS = """
import json,os,sys
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
from fleet_manager.planning import PlanError
s=NodeJournal(sys.argv[1],authority='fleet-1',installation='node-install-1',target='node-1')
r=OperationIntent.from_dict(json.loads(sys.argv[2]))
def effect(c):
    c.execute('CREATE TABLE IF NOT EXISTS effects(value TEXT)')
    c.execute("INSERT INTO effects VALUES('result-1')")
    return 'result-1'
"""


def run_process(path, intent, body):
    return subprocess.run(
        [sys.executable, "-I", "-c", PROCESS + body, str(path), json.dumps(intent.to_dict())],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def test_real_acceptance_exit_before_commit_rolls_back_owner_and_spool(tmp_path):
    path, intent = tmp_path / "node.db", request()
    store = node(path)
    crash = run_process(path, intent, """
from contextlib import contextmanager
original=s._transaction
@contextmanager
def crash_transaction():
    with original() as c:
        yield c
        assert c.execute('SELECT count(*) FROM allocation_owners').fetchone()[0]==1
        os._exit(23)
s._transaction=crash_transaction
s.accept_allocation(r,resource='workspace-1')
""")
    assert crash.returncode == 23, crash.stderr
    assert store.allocation_owner(resource="workspace-1") is None
    assert store.inspect(intent) is None
    assert store.results() == []


def test_real_effect_crash_retains_owner_and_lost_reply_retry_has_one_effect(tmp_path):
    store, intent = node(tmp_path / "node.db"), request()
    store.accept_allocation(intent, resource="workspace-1")
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TABLE effects(value TEXT)")
    crash = run_process(store.path, intent, """
def crash(c):
    effect(c)
    os._exit(23)
s.commit_cooperative(r,crash,resource='workspace-1')
""")
    assert crash.returncode == 23, crash.stderr
    assert effect_count(store.path) == 0
    assert node(store.path).allocation_owner(resource="workspace-1") == intent
    assert store.inspect(intent).to_dict()["status"] == "accepted"
    lost = run_process(store.path, intent, """
s.commit_cooperative(r,effect,resource='workspace-1')
os._exit(24)
""")
    assert lost.returncode == 24, lost.stderr
    result = node(store.path).commit_cooperative(intent, effect, resource="workspace-1")
    assert result.to_dict()["status"] == "succeeded"
    assert effect_count(store.path) == 1
    with pytest.raises(PlanError, match="already owned"):
        store.accept_allocation(request(operation="op-2", generation=2), resource="workspace-1")


@pytest.mark.parametrize("duplicate", [False, True])
def test_four_real_node_clients_cannot_duplicate_owned_effect(tmp_path, duplicate):
    path = tmp_path / "node.db"
    node(path)
    body = """
try:
    s.accept_allocation(r,resource='workspace-1')
except PlanError as error:
    if str(error)!='node allocation resource is already owned':
        raise
    sys.exit(2)
s.commit_cooperative(r,effect,resource='workspace-1')
"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda i: run_process(
            path, request(operation="op-1" if duplicate else f"op-{i}", generation=i + 1
                          if not duplicate else 1), body), range(4)))
    assert sorted(run.returncode for run in runs) == (
        [0, 0, 0, 0] if duplicate else [0, 2, 2, 2]), [run.stderr for run in runs]
    assert effect_count(path) == 1
    assert len(node(path).results()) == 2
