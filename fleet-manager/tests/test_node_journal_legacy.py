"""Read-only v5 SQLite fixtures, not live capture or repaired-writer evidence."""

import sqlite3
from contextlib import closing

import pytest
from fleet_manager.node_journal import NodeJournal
from fleet_manager.node_journal_legacy import inspect_legacy_external
from fleet_manager.planning import PlanError, canonical, decode, digest
from test_node_journal import intent, journal


@pytest.fixture
def legacy(tmp_path):
    if not callable(getattr(sqlite3.Connection, "deserialize", None)):
        pytest.skip("Optional legacy snapshot inspection needs SQLite deserialize support")
    path = tmp_path / "original.db"
    store = journal(path)
    deployment = {"schema": "example.deployment", "revision": "original"}
    request = intent(command="manager.converge", parameters={"deployment": digest(deployment)})
    store.accept_named_external(request, resource="original-resource")
    store.begin_external(request)
    legacy = request.to_dict()
    legacy["parameters"]["instance"] = "original-instance"
    request = canonical(legacy)
    plan = canonical({
        "deployment": deployment, "context": {"original": "retained"},
        "steps": [{"id": name, "action": "observe", "target": "substrate"}
                  for name in ("completed", "uncertain", "pending")],
    })
    evidence = canonical({"native_identity": "original"})
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE operations SET intent=?", (request,))
        for cursor, raw in connection.execute("SELECT cursor,receipt FROM receipts").fetchall():
            receipt = decode(bytes(raw))
            receipt["request_digest"] = digest(legacy)
            connection.execute("UPDATE receipts SET receipt=? WHERE cursor=?", (canonical(receipt), cursor))
        connection.execute(
            "CREATE TABLE external_plans("
            "operation_id INTEGER PRIMARY KEY REFERENCES operations(id),resource TEXT NOT NULL,plan BLOB NOT NULL)")
        connection.execute(
            "CREATE TABLE external_steps("
            "operation_id INTEGER NOT NULL REFERENCES external_plans(operation_id),"
            "step TEXT NOT NULL,observation BLOB,PRIMARY KEY(operation_id,step))")
        connection.execute("INSERT INTO external_plans VALUES(1,?,?)", ("original-resource", plan))
        connection.execute("INSERT INTO external_steps VALUES(1,'completed',?)", (evidence,))
        connection.execute("INSERT INTO external_steps VALUES(1,'uncertain',NULL)")
        connection.execute("PRAGMA user_version=5")
        connection.commit()
    return path, request, plan, evidence


def observe(path, request, **changes):
    options = dict(authority="fleet-1", installation="node-install-1", target="node-1",
                   request=request, resource="original-resource")
    options.update(changes)
    return inspect_legacy_external(path.read_bytes(), **options)


def test_snapshot_retains_original_unknown_and_all_step_states_without_writes(legacy):
    path, request, plan, evidence = legacy
    before = path.read_bytes()
    files = set(path.parent.iterdir())
    result = observe(path, request)
    assert result.intent == request
    assert result.plan == plan
    assert result.attempted_steps == (("completed", evidence), ("uncertain", None))
    with closing(sqlite3.connect(path)) as connection:
        assert result.receipts == tuple(row[0] for row in connection.execute(
            "SELECT receipt FROM receipts ORDER BY sequence"))
        assert connection.execute("PRAGMA user_version").fetchone() == (5,)
    assert path.read_bytes() == before and set(path.parent.iterdir()) == files
    with pytest.raises(PlanError, match="unsupported node journal schema"):
        journal(path)
    with pytest.raises(PlanError, match="unsupported node journal schema"):
        NodeJournal(path, authority="fleet-1", installation="node-install-1",
                    target="node-1", existing_only=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"installation": "other"}, {"authority": "other"}, {"target": "other"},
    {"resource": "other"},
])
def test_snapshot_refuses_foreign_identity_or_resource(legacy, changes):
    path, request, _, _ = legacy
    with pytest.raises(PlanError):
        observe(path, request, **changes)


@pytest.mark.parametrize("statement", [
    "PRAGMA user_version=4",
    "DELETE FROM identity",
    "DELETE FROM external_owners",
    "UPDATE external_plans SET resource='other'",
    "UPDATE external_steps SET step='unselected' WHERE step='uncertain'",
    "UPDATE external_steps SET observation=x'7b7d' WHERE step='completed'",
    "UPDATE receipts SET sequence=10 WHERE sequence=2",
    "DELETE FROM receipts WHERE sequence=1",
    "ALTER TABLE external_steps ADD COLUMN hidden TEXT",
    "CREATE VIEW original_view AS SELECT * FROM operations",
])
def test_snapshot_refuses_changed_or_ambiguous_evidence(legacy, statement):
    path, request, _, _ = legacy
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(statement)
        connection.commit()
    with pytest.raises(PlanError):
        observe(path, request)


def test_snapshot_refuses_changed_original_request_and_deployment(legacy):
    path, request, _, _ = legacy
    changed = decode(request)
    changed["parameters"]["deployment"] = "f" * 64
    changed = canonical(changed)
    with pytest.raises(PlanError, match="conflicting"):
        observe(path, changed)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE operations SET intent=?", (changed,))
        connection.execute("DELETE FROM receipts")
        accepted = decode(changed)
        del accepted["parameters"]
        accepted.update(schema="fleet-manager.target-receipt", request_digest=digest(decode(changed)),
                        sequence=1, status="accepted", result_ref=None)
        connection.execute("INSERT INTO receipts(operation_id,sequence,receipt) VALUES(1,1,?)",
                           (canonical(accepted),))
        connection.commit()
    with pytest.raises(PlanError, match="plan differs"):
        observe(path, changed)


def test_snapshot_capability_refusal_keeps_ordinary_journal_available(monkeypatch, tmp_path):
    import fleet_manager.node_journal_legacy as reader

    snapshot = b"SQLite format 3\0" + b"\0\0\x01\x01" + b"\0" * 80
    request = intent(command="manager.converge", parameters={"deployment": "a" * 64}).to_dict()
    request["parameters"]["instance"] = "original-instance"
    monkeypatch.setattr(reader.sqlite3, "Connection", object)
    with pytest.raises(PlanError, match="deserialize support"):
        inspect_legacy_external(
            snapshot, authority="fleet-1", installation="node-install-1", target="node-1",
            request=canonical(request), resource="original-resource")
    assert journal(tmp_path / "current.db").results() == []


def test_snapshot_refuses_wal_or_nonbytes_input_without_opening_a_path(legacy):
    path, request, _, _ = legacy
    raw = bytearray(path.read_bytes())
    raw[18:20] = b"\x02\x02"
    for snapshot in (path, bytearray(path.read_bytes()), bytes(raw), b"not-sqlite"):
        with pytest.raises(PlanError, match="snapshot bytes"):
            inspect_legacy_external(
                snapshot, authority="fleet-1", installation="node-install-1", target="node-1",
                request=request, resource="original-resource")
