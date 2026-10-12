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


def request(target="node-1", **changes):
    value = {"schema": "fleet-manager.operation-intent", "version": 1,
             "operation": f"op-{target}", "authority": "fleet-1", "principal": "actor-1",
             "account": "account-1", "target": target, "generation": 1,
             "command": "task.submit", "parameters": {"objective": "objective-1"}}
    value.update(changes)
    return OperationIntent.from_dict(value)


def gateway(path):
    return OperationLedger(path, authority="fleet-1", installation="gateway-1")


def node(path, target="node-1"):
    return NodeJournal(path, authority="fleet-1", installation=f"install-{target}", target=target)


def page(store, entries, after=0, target="node-1"):
    return store.reconcile_results(target=target, installation=f"install-{target}",
                                   after=after, entries=entries)


def cursor(store, target="node-1"):
    return store.result_cursor(target=target, installation=f"install-{target}")


def test_receiver_receipt_audit_outbox_and_cursor_commit_together(tmp_path):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    store.admit(intent)
    owner.accept(intent)
    owner.commit_cooperative(intent, lambda _: "effect-1")
    assert page(store, owner.results()) == 2
    reopened = gateway(store.path)
    assert cursor(reopened) == 2
    assert reopened.inspect(intent)["state"] == "outcome"
    assert page(reopened, owner.results(after=cursor(reopened)), after=2) == 2
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT event FROM audit ORDER BY id").fetchall() == [
            ("admitted",), ("accepted",), ("succeeded",),
        ]
    with pytest.raises(PlanError, match="cursor changed"):
        page(reopened, owner.results())
    assert cursor(reopened) == 2


def test_receiver_transaction_rolls_back_whole_page_on_second_audit_failure(tmp_path, monkeypatch):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    store.admit(intent)
    owner.accept(intent)
    owner.commit_cooperative(intent, lambda _: "effect-1")
    original = store._event
    def fail(connection, operation_id, event):
        if event == "succeeded":
            raise RuntimeError("injected receiver crash")
        original(connection, operation_id, event)
    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError):
        page(store, owner.results())
    assert store.inspect(intent)["state"] == "pending"
    assert cursor(store) == 0
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 1
    monkeypatch.setattr(store, "_event", original)
    assert page(store, owner.results()) == 2


def test_unadmitted_result_does_not_skip_or_partially_commit(tmp_path):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    unknown = request(operation="unknown")
    store.admit(intent)
    owner.accept(intent)
    owner.accept(unknown)
    with pytest.raises(PlanError, match="not admitted"):
        page(store, owner.results())
    assert store.inspect(intent)["state"] == "pending"
    assert cursor(store) == 0


def test_cross_scope_digest_and_generation_denials_keep_cursor_unchanged(tmp_path):
    store, intent = gateway(tmp_path / "gateway.db"), request()
    store.admit(intent)
    for index, changed in enumerate((
        request(authority="other"), request(generation=2),
        request(parameters={"objective": "other"}), request(account="other"),
    )):
        owner = NodeJournal(tmp_path / f"node-{index}.db", authority=changed.to_dict()["authority"],
                            installation="test", target="node-1")
        owner.accept(changed)
        with pytest.raises(PlanError):
            page(store, owner.results())
    other = node(tmp_path / "other.db", target="node-2")
    other.accept(request("node-2"))
    with pytest.raises(PlanError, match="target mismatch"):
        page(store, other.results())
    assert store.inspect(intent)["state"] == "pending"
    assert cursor(store) == 0


def test_two_independent_node_streams_reconcile_unknown_and_outcomes(tmp_path):
    store = gateway(tmp_path / "gateway.db")
    first, second = node(tmp_path / "first.db"), node(tmp_path / "second.db", target="node-2")
    r1, r2 = request(), request("node-2")
    store.admit(r1)
    store.admit(r2)
    first.accept(r1)
    first.begin_external(r1)
    second.accept(r2)
    second.commit_cooperative(r2, lambda _: "effect-2")
    assert page(store, first.results()) == 2
    assert page(store, second.results(), target="node-2") == 2
    assert cursor(store) == cursor(store, target="node-2") == 2
    assert store.inspect(r1)["state"] == "uncertain"
    assert store.inspect(r2)["state"] == "outcome"
    assert store.result_cursor(target="node-1", installation="new-installation") == 0
    with pytest.raises(PlanError, match="sequence"):
        store.reconcile_results(target="node-2", installation="new-installation",
                                after=0, entries=second.results())


def test_replay_page_bounds_and_contiguous_cursor_requirement(tmp_path):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    store.admit(intent)
    owner.accept(intent)
    entry = owner.results()[0]
    for entries in (
        [{**entry, "cursor": 2}], [{**entry, "cursor": True}],
        [{"cursor": 1, "receipt": entry["receipt"].to_dict()}],
        [{**entry, "extra": 1}], [entry] * 257, (entry, entry), None,
    ):
        with pytest.raises(PlanError):
            page(store, entries)
    for after in (True, -1, 2**63):
        with pytest.raises(PlanError):
            page(store, [], after=after)
    assert cursor(store) == 0
    assert page(store, [entry]) == 1


def test_concurrent_receivers_cannot_advance_over_each_other(tmp_path):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    store.admit(intent)
    owner.accept(intent)
    entries = owner.results()
    def receive(_):
        try:
            return page(store, entries)
        except PlanError as exc:
            assert "cursor changed" in str(exc)
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(receive, range(16)))
    assert results.count(1) == 1
    assert cursor(store) == 1
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 2


def test_schema_one_transactional_upgrade_preserves_admissions_and_receipts(tmp_path):
    path, intent = tmp_path / "gateway.db", request()
    store = gateway(path)
    store.admit(intent)
    accepted = node(tmp_path / "node.db").accept(intent)
    store.record_receipt(intent, accepted)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE result_streams")
        connection.execute("PRAGMA user_version=1")
    with pytest.raises(PlanError, match="schema"):
        store.inspect(intent)
    upgraded = gateway(path)
    assert upgraded.inspect(intent)["receipt"] == accepted.to_dict()
    assert cursor(upgraded) == 0
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_schema_upgrade_refuses_foreign_node_state_without_mutation(tmp_path):
    path = tmp_path / "node.db"
    NodeJournal(path, authority="fleet-1", installation="gateway-1", target="node-1")
    with closing(sqlite3.connect(path)) as connection:
        before = connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    with pytest.raises(PlanError, match="unrecognized"):
        gateway(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("sql", [
    "CREATE VIEW sqlitexforeign AS SELECT 1",
    "ALTER TABLE receipts ADD COLUMN foreign_value TEXT",
    "ALTER TABLE receipts ADD COLUMN generated_value TEXT GENERATED ALWAYS AS (status) VIRTUAL",
    "ALTER TABLE receipts ADD COLUMN generated_value TEXT GENERATED ALWAYS AS (status) STORED",
])
def test_schema_upgrade_preserves_unknown_inventory_or_columns(tmp_path, sql, version):
    path = tmp_path / "gateway.db"
    gateway(path)
    with closing(sqlite3.connect(path)) as connection:
        if version == 1:
            connection.execute("DROP TABLE result_streams")
            connection.execute("PRAGMA user_version=1")
        connection.execute(sql)
        before = connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    with pytest.raises(PlanError, match="unrecognized"):
        gateway(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version


PROCESS = """
import json,os,sys
from fleet_manager.ledger import OperationLedger
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent
g=OperationLedger(sys.argv[1],authority='fleet-1',installation='gateway-1')
n=NodeJournal(sys.argv[2],authority='fleet-1',installation='install-node-1',target='node-1')
r=OperationIntent.from_dict(json.loads(sys.argv[3]))
after=g.result_cursor(target='node-1',installation='install-node-1')
"""


def run_process(store, owner, intent, body):
    return subprocess.run(
        [sys.executable, "-I", "-c", PROCESS + body, str(store.path), str(owner.path),
         json.dumps(intent.to_dict())],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def test_real_receiver_process_commit_then_lost_reply_resumes_from_durable_cursor(tmp_path):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    store.admit(intent)
    owner.accept(intent)
    owner.commit_cooperative(intent, lambda _: "effect-1")
    crashed = run_process(store, owner, intent, """
g.reconcile_results(target='node-1',installation='install-node-1',after=after,
                    entries=n.results(after=after))
os._exit(27)
""")
    assert crashed.returncode == 27, crashed.stderr
    reopened = gateway(store.path)
    assert cursor(reopened) == 2
    assert reopened.inspect(intent)["state"] == "outcome"
    resumed = run_process(store, owner, intent, """
assert after==2
assert n.results(after=after)==[]
g.reconcile_results(target='node-1',installation='install-node-1',after=after,
                    entries=n.results(after=after))
""")
    assert resumed.returncode == 0, resumed.stderr


def test_real_receiver_process_exit_before_cursor_commit_rolls_back_entire_page(tmp_path):
    store, owner, intent = gateway(tmp_path / "gateway.db"), node(tmp_path / "node.db"), request()
    store.admit(intent)
    owner.accept(intent)
    owner.commit_cooperative(intent, lambda _: "effect-1")
    crashed = run_process(store, owner, intent, """
original=g._event
def event(c,operation,event):
    original(c,operation,event)
    if event=='succeeded':
        os._exit(28)
g._event=event
g.reconcile_results(target='node-1',installation='install-node-1',after=after,
                    entries=n.results(after=after))
""")
    assert crashed.returncode == 28, crashed.stderr
    assert store.inspect(intent)["state"] == "pending"
    assert cursor(store) == 0
    assert page(store, owner.results()) == 2
