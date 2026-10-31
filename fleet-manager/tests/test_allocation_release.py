from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from fleet_manager.ledger import OperationLedger
from fleet_manager.allocation import check_released_generation
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import AllocationRelease, OperationIntent
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


def gateway(path):
    return OperationLedger(path, authority="fleet-1", installation="gateway-1")


def effect(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS effects(value TEXT)")
    connection.execute("INSERT INTO effects VALUES('active')")
    return "result-1"


def retire(connection):
    connection.execute("DELETE FROM effects")


def active(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("SELECT count(*) FROM effects").fetchone()[0]


def prepared(tmp_path):
    intent = request()
    n, g = node(tmp_path / "node.db"), gateway(tmp_path / "gateway.db")
    g.admit_allocation(intent, resource="workspace-1")
    n.accept_allocation(intent, resource="workspace-1")
    n.commit_cooperative(intent, effect, resource="workspace-1")
    return n, g, intent


@pytest.fixture(scope="session")
def _active_allocation_seed(tmp_path_factory):
    root = tmp_path_factory.mktemp("active-allocation")
    prepared(root)
    original = {name: (root / name).read_bytes() for name in ("node.db", "gateway.db")}
    yield root
    assert all((root / name).read_bytes() == content for name, content in original.items())


def _copy_prepared_allocation(seed, destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("node.db", "gateway.db"):
        shutil.copyfile(seed / name, destination / name)
    return node(destination / "node.db"), gateway(destination / "gateway.db"), request()


@pytest.fixture
def prepared_allocation(tmp_path, _active_allocation_seed):
    return _copy_prepared_allocation(_active_allocation_seed, tmp_path)


def release(g, intent, proof, **changes):
    arguments = {"resource": "workspace-1", "installation": "node-install-1"}
    arguments.update(changes)
    return g.release_allocation(intent, proof, **arguments)


def forbidden(_):
    pytest.fail("stale or duplicate callback must not run")


def test_active_allocation_copies_isolate_retirement_and_gateway_release(
    tmp_path, _active_allocation_seed,
):
    left, gateway_left, intent = _copy_prepared_allocation(_active_allocation_seed, tmp_path / "left")
    right, gateway_right, _ = _copy_prepared_allocation(_active_allocation_seed, tmp_path / "right")
    proof = left.release_cooperative(intent, retire, resource="workspace-1")
    release(gateway_left, intent, proof)

    assert active(left.path) == 0 and active(right.path) == 1
    assert left.allocation_owner(resource="workspace-1") is None
    assert right.allocation_owner(resource="workspace-1") is not None
    assert gateway_left.inspect_allocation(resource="workspace-1") is None
    assert gateway_right.inspect_allocation(resource="workspace-1") is not None
    assert right.allocation_release(intent) is None
    assert gateway_right.allocation_release(intent) is None
    assert active(_active_allocation_seed / "node.db") == 1


def test_retirement_release_and_successor_preserve_original_operations(tmp_path, prepared_allocation):
    n, g, intent = prepared_allocation
    result = n.inspect(intent)
    assert n.allocation_release(intent) is None
    assert g.allocation_release(intent) is None
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    assert active(n.path) == 0
    assert node(n.path).allocation_release(intent) == proof
    assert n.allocation_owner(resource="workspace-1") is None
    assert n.inspect(intent) == result
    assert g.inspect_allocation(resource="workspace-1") is not None
    release(g, intent, proof)
    assert gateway(g.path).allocation_release(intent) == proof
    assert gateway(g.path).inspect_allocation(resource="workspace-1") is None
    successor = request(operation="op-2", generation=2)
    g.admit_allocation(successor, resource="workspace-1")
    n.accept_allocation(successor, resource="workspace-1")
    n.commit_cooperative(successor, effect, resource="workspace-1")
    assert n.release_cooperative(intent, forbidden, resource="workspace-1") == proof
    release(g, intent, proof)
    assert n.accept_allocation(intent, resource="workspace-1") == result
    assert g.admit_allocation(intent, resource="workspace-1") == 1
    assert g.admit(intent) == 1
    assert n.accept(intent) == result
    with pytest.raises(PlanError, match="does not own"):
        n.commit_cooperative(intent, forbidden, resource="workspace-1")
    assert active(n.path) == 1
    assert n.allocation_owner(resource="workspace-1") == successor
    assert g.inspect_allocation(resource="workspace-1")["intent"] == successor.to_dict()
    assert n.allocation_release(successor) is None


@pytest.mark.parametrize("changes", [
    {"operation": "op-2"}, {"operation": "op-2", "generation": 1},
    {"principal": "other"}, {"account": "other"},
])
def test_released_generation_cannot_be_reused_across_identities(tmp_path, changes, prepared_allocation):
    n, g, intent = prepared_allocation
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    release(g, intent, proof)
    candidate = request(**changes)
    for store, admit in ((n, n.accept_allocation), (g, g.admit_allocation)):
        with pytest.raises(PlanError, match="generation"):
            admit(candidate, resource="workspace-1")
        assert store.inspect(candidate) is None
    # The same number on a different actual resource is not a connector epoch.
    n.accept_allocation(candidate, resource="workspace-2")
    g.admit_allocation(candidate, resource="workspace-2")


@pytest.mark.parametrize("field,value", [
    ("operation", "other"), ("authority", "other"), ("principal", "other"),
    ("account", "other"), ("target", "other"), ("generation", 2),
    ("request_digest", "f" * 64), ("resource", "other"), ("installation", "other"),
])
def test_mismatched_proof_never_releases_gateway_claim(tmp_path, field, value, prepared_allocation):
    n, g, intent = prepared_allocation
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    data = proof.to_dict()
    data[field] = value
    with pytest.raises(PlanError):
        release(g, intent, AllocationRelease.from_dict(data))
    assert g.inspect_allocation(resource="workspace-1")["intent"] == intent.to_dict()


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"command": "task.submit"}, {"generation": True},
    {"request_digest": "F" * 64}, {"resource": "../bad"},
    {"installation": ""}, {"unknown": "field"}, {"resource": True},
])
def test_release_shape_is_strict_and_immutable(tmp_path, changes, prepared_allocation):
    n, _, intent = prepared_allocation
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    data = proof.to_dict()
    data.update(changes)
    with pytest.raises(PlanError):
        AllocationRelease.from_dict(data)
    assert AllocationRelease(proof.encoded) == proof
    with pytest.raises(PlanError):
        AllocationRelease(bytearray(proof.encoded))
    with pytest.raises(PlanError):
        AllocationRelease(json.dumps(proof.to_dict(), indent=1).encode())


def test_release_binding_and_legacy_acceptance_do_not_retire(tmp_path, prepared_allocation):
    n, g, intent = prepared_allocation
    for candidate, resource in ((intent, "other"), (request(generation=2), "workspace-1")):
        with pytest.raises(PlanError):
            n.release_cooperative(candidate, forbidden, resource=resource)
    unbound = request(operation="legacy")
    n.accept(unbound)
    g.admit(unbound)
    with pytest.raises(PlanError):
        n.release_cooperative(unbound, forbidden, resource="workspace-1")
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    with pytest.raises(PlanError):
        release(g, intent, proof, installation="other")
    with pytest.raises(PlanError):
        n.accept_allocation(intent, resource="other")
    release(g, intent, proof)
    with pytest.raises(PlanError):
        g.admit_allocation(intent, resource="other")
    data = proof.to_dict()
    data["installation"] = "another-install"
    with pytest.raises(PlanError, match="conflicting"):
        release(g, intent, AllocationRelease.from_dict(data), installation="another-install")


@pytest.mark.parametrize("failure", ["callback", "return", "owner", "storage", "transaction"])
def test_retirement_or_tombstone_failure_rolls_back_everything(tmp_path, failure, prepared_allocation):
    n, _, intent = prepared_allocation
    if failure == "storage":
        with closing(sqlite3.connect(n.path)) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_release BEFORE INSERT ON allocation_releases"
                " BEGIN SELECT RAISE(ABORT,'injected storage failure'); END")
    def bad(connection):
        retire(connection)
        if failure == "callback":
            raise RuntimeError("injected failure")
        if failure == "return":
            return "unexpected"
        if failure == "owner":
            connection.execute("DELETE FROM allocation_owners")
        if failure == "transaction":
            connection.commit()
    with pytest.raises((PlanError, RuntimeError, sqlite3.DatabaseError)):
        n.release_cooperative(intent, bad, resource="workspace-1")
    assert active(n.path) == 1
    assert n.allocation_owner(resource="workspace-1") == intent
    assert n.allocation_release(intent) is None


def test_gateway_release_audit_failure_is_atomic(tmp_path, monkeypatch, prepared_allocation):
    n, g, intent = prepared_allocation
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    original = g._event
    def fail(*args):
        original(*args)
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(g, "_event", fail)
    with pytest.raises(RuntimeError):
        release(g, intent, proof)
    assert g.inspect_allocation(resource="workspace-1") is not None
    with closing(sqlite3.connect(g.path)) as connection:
        assert connection.execute("SELECT count(*) FROM allocation_releases").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM audit").fetchone()[0] == 2
    release(gateway(g.path), intent, proof)


@pytest.mark.parametrize("kind,version", [("node", 2), ("gateway", 3)])
def test_upgrade_preserves_held_ownership_and_old_writer_refuses(
    tmp_path, kind, version, prepared_allocation,
):
    n, g, intent = prepared_allocation
    store = n if kind == "node" else g
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DROP TABLE allocation_releases")
        if kind == "node":
            connection.execute("DROP TABLE external_owners")
        connection.execute(f"PRAGMA user_version={version}")
    with pytest.raises(PlanError, match="schema"):
        store.inspect(intent)
    reopened = node(store.path) if kind == "node" else gateway(store.path)
    assert reopened.inspect(intent) is not None
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT count(*) FROM allocation_releases").fetchone()[0] == 0
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    if kind == "node":
        assert reopened.allocation_owner(resource="workspace-1") == intent
    else:
        assert reopened.inspect_allocation(resource="workspace-1") is not None


PROCESS = """
import json,os,sys
from fleet_manager.node_journal import NodeJournal
from fleet_manager.ledger import OperationLedger
from fleet_manager.operations import OperationIntent,AllocationRelease
n=NodeJournal(sys.argv[1],authority='fleet-1',installation='node-install-1',target='node-1')
g=OperationLedger(sys.argv[2],authority='fleet-1',installation='gateway-1')
r=OperationIntent.from_dict(json.loads(sys.argv[3]))
def retire(c):
    c.execute("DELETE FROM effects")
"""


def run(n, g, intent, body):
    return subprocess.run(
        [sys.executable, "-I", "-c", PROCESS + body, str(n.path), str(g.path),
         json.dumps(intent.to_dict())], capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def test_installed_retirement_and_gateway_crashes_and_lost_replies(tmp_path, prepared_allocation):
    n, g, intent = prepared_allocation
    crashed = run(n, g, intent, """
def crash(c):
    retire(c)
    os._exit(23)
n.release_cooperative(r,crash,resource='workspace-1')
""")
    assert crashed.returncode == 23, crashed.stderr
    assert active(n.path) == 1
    assert n.allocation_release(intent) is None
    lost = run(n, g, intent, """
n.release_cooperative(r,retire,resource='workspace-1')
os._exit(24)
""")
    assert lost.returncode == 24, lost.stderr
    proof = n.release_cooperative(intent, forbidden, resource="workspace-1")
    assert active(n.path) == 0
    crashed = run(n, g, intent, """
def crash(*args):
    os._exit(25)
g._event=crash
g.release_allocation(r,n.allocation_release(r),resource='workspace-1',installation='node-install-1')
""")
    assert crashed.returncode == 25, crashed.stderr
    assert g.inspect_allocation(resource="workspace-1") is not None
    lost = run(n, g, intent, """
g.release_allocation(r,n.allocation_release(r),resource='workspace-1',installation='node-install-1')
os._exit(26)
""")
    assert lost.returncode == 26, lost.stderr
    release(g, intent, proof)
    assert g.inspect_allocation(resource="workspace-1") is None


def test_four_installed_release_clients_retire_once_and_replay_same_proof(tmp_path, prepared_allocation):
    n, g, intent = prepared_allocation
    with closing(sqlite3.connect(n.path)) as connection:
        connection.execute("CREATE TABLE retirements(value TEXT)")
    body = """
def once(c):
    retire(c)
    c.execute("INSERT INTO retirements VALUES('retired')")
p=n.release_cooperative(r,once,resource='workspace-1')
g.release_allocation(r,p,resource='workspace-1',installation='node-install-1')
print(p.encoded.decode())
"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(n, g, intent, body), range(4)))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert len({result.stdout for result in results}) == 1
    with closing(sqlite3.connect(n.path)) as connection:
        assert connection.execute("SELECT count(*) FROM retirements").fetchone()[0] == 1
    with closing(sqlite3.connect(g.path)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM audit WHERE event='allocation-released'").fetchone()[0] == 1


def test_installed_successor_race_and_stale_effect_after_reassignment(tmp_path, prepared_allocation):
    n, g, intent = prepared_allocation
    proof = n.release_cooperative(intent, retire, resource="workspace-1")
    release(g, intent, proof)
    body = """
from fleet_manager.planning import PlanError
try:
    g.admit_allocation(r,resource='workspace-1')
except PlanError:
    sys.exit(2)
n.accept_allocation(r,resource='workspace-1')
def effect(c):
    c.execute("INSERT INTO effects VALUES('successor')")
    return 'result-2'
n.commit_cooperative(r,effect,resource='workspace-1')
"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(
            lambda i: run(n, g, request(operation=f"successor-{i}", generation=2), body), range(4)))
    assert sorted(result.returncode for result in runs) == [0, 2, 2, 2], [r.stderr for r in runs]
    successor = n.allocation_owner(resource="workspace-1")
    stale = run(n, g, intent, """
from fleet_manager.planning import PlanError
def forbidden(c):
    c.execute("INSERT INTO effects VALUES('stale')")
    return 'invalid'
try:
    n.commit_cooperative(r,forbidden,resource='workspace-1')
except PlanError:
    sys.exit(2)
""")
    assert stale.returncode == 2, stale.stderr
    release(g, intent, proof)
    assert active(n.path) == 1
    assert g.inspect_allocation(resource="workspace-1")["intent"] == successor.to_dict()


def test_generation_high_water_survives_multiple_retirements(tmp_path, prepared_allocation):
    n, g, first = prepared_allocation
    release(g, first, n.release_cooperative(first, retire, resource="workspace-1"))
    second = request(operation="second", generation=7)
    n.accept_allocation(second, resource="workspace-1")
    g.admit_allocation(second, resource="workspace-1")
    # Retirement before an allocation effect also needs an explicit trusted callback.
    release(g, second, n.release_cooperative(second, retire, resource="workspace-1"))
    for generation in (1, 2, 7):
        candidate = request(operation=f"stale-{generation}", generation=generation)
        for admit in (node(n.path).accept_allocation, gateway(g.path).admit_allocation):
            with pytest.raises(PlanError, match="generation"):
                admit(candidate, resource="workspace-1")
    last = request(operation="last", generation=8)
    n.accept_allocation(last, resource="workspace-1")
    g.admit_allocation(last, resource="workspace-1")


@pytest.mark.parametrize("kind", ["node", "gateway"])
def test_release_upgrade_refuses_reserved_table_collision_without_mutation(
    tmp_path, kind, prepared_allocation,
):
    n, g, _ = prepared_allocation
    store, old = (n, 2) if kind == "node" else (g, 3)
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DROP TABLE allocation_releases")
        connection.execute("CREATE VIEW allocation_releases AS SELECT 1 AS foreign_value")
        connection.execute(f"PRAGMA user_version={old}")
        before = connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
    with pytest.raises(PlanError, match="unrecognized"):
        (node if kind == "node" else gateway)(store.path)
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT type,name,sql FROM sqlite_master").fetchall() == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == old


@pytest.mark.parametrize("action", ["effect", "retire", "generic-effect"])
@pytest.mark.parametrize("sql", [
    "DELETE FROM allocation_releases",
    "UPDATE allocation_releases SET generation=0",
    "INSERT INTO allocation_releases SELECT * FROM allocation_releases",
    "DROP TABLE allocation_releases",
    "ALTER TABLE allocation_releases RENAME TO discarded",
    "DROP INDEX allocation_release_generations",
    "CREATE INDEX extra ON allocation_releases(resource)",
    "CREATE TRIGGER destructive AFTER INSERT ON allocation_releases BEGIN SELECT 1; END",
    "CREATE TEMP VIEW allocation_releases AS SELECT 1 AS proof",
    "UPDATE identity SET installation='other'",
    "DELETE FROM operations",
    "DELETE FROM receipts",
    "DELETE FROM allocation_owners",
])
def test_callbacks_cannot_mutate_older_tombstones_or_core_state(
    tmp_path, action, sql, prepared_allocation,
):
    n, g, first = prepared_allocation
    proof = n.release_cooperative(first, retire, resource="workspace-1")
    release(g, first, proof)
    successor = request(operation="second", generation=7)
    n.accept_allocation(successor, resource="workspace-1")
    current = successor
    if action == "generic-effect":
        current = request(operation="generic", command="task.submit",
                          parameters={"objective": "objective-1"})
        n.accept(current)
    before = n.inspect(current)
    def invalid(connection):
        effect(connection)
        connection.execute(sql)
        return None if action == "retire" else "result-2"
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        if action == "retire":
            n.release_cooperative(current, invalid, resource="workspace-1")
        else:
            n.commit_cooperative(current, invalid,
                                 **({} if action == "generic-effect"
                                    else {"resource": "workspace-1"}))
    assert n.allocation_release(first) == proof
    assert n.allocation_owner(resource="workspace-1") == successor
    assert n.allocation_release(successor) is None
    assert n.inspect(current) == before
    assert active(n.path) == 0


@pytest.mark.parametrize("kind", ["node", "gateway"])
def test_indexed_generation_lookup_is_bounded_with_large_permanent_history(
    tmp_path, kind, prepared_allocation,
):
    n, g, _ = prepared_allocation
    store = n if kind == "node" else g
    with closing(sqlite3.connect(store.path)) as connection:
        # Only the generation lookup is exercised here; proof parsing is deliberately absent.
        connection.executemany(
            "INSERT INTO allocation_releases VALUES(?,?,?,?)",
            ((i + 100, f"resource-{i % 20}", i + 1, b"unused") for i in range(5000)))
        connection.commit()
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT generation FROM allocation_releases WHERE resource=? "
            "ORDER BY generation DESC LIMIT 1", ("resource-3",),
        ).fetchall()
        assert any("COVERING INDEX allocation_release_generations" in row[3] for row in plan)
        # A historical scan/decoding loop cannot fit this VM instruction budget.
        connection.set_progress_handler(lambda: 1, 100)
        with pytest.raises(PlanError, match="generation"):
            check_released_generation(connection, 4000, "resource-3")
        check_released_generation(connection, 5000, "resource-3")
        check_released_generation(connection, 1, "fresh")
        connection.set_progress_handler(None, 0)


@pytest.mark.parametrize("kind", ["node", "gateway"])
@pytest.mark.parametrize("index", [
    "", "CREATE INDEX allocation_release_generations ON allocation_releases(resource)",
    "CREATE INDEX allocation_release_generations ON allocation_releases(resource,generation)",
    "CREATE INDEX allocation_release_generations ON allocation_releases(resource,generation DESC)"
    " WHERE generation>1",
])
def test_open_refuses_missing_or_incompatible_generation_index(
    tmp_path, kind, index, prepared_allocation,
):
    n, g, _ = prepared_allocation
    store = n if kind == "node" else g
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("DROP INDEX allocation_release_generations")
        if index:
            connection.execute(index)
    with pytest.raises(PlanError, match="release index"):
        (node if kind == "node" else gateway)(store.path)
    with pytest.raises(PlanError, match="release index"):
        store.inspect(request())
