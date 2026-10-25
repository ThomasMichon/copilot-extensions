"""Original keyed-creation DB admission, not allocation or native qualification."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import sqlite3

import pytest

from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.creation_custody import (
    CreationJournal, CreationKind, CreationRequest, creation_table, legacy_creation,
)
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_runtime_custody import close, enrolled


def creation_request(kind: CreationKind = "workspace") -> CreationRequest:
    target = {"type": "local", "project": "fixture"} if kind == "workspace" else {
        "type": "command", "spawn_command": ["provider", "exec", "original"],
        "venue": {"instance_id": "original", "transport": "provider-exec"},
    }
    key = ["fixture", "original-key"] if kind == "workspace" else "original-key"
    digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    facts = {"target": target, "options": {"model": "fixture-model"}}
    if kind == "workspace":
        facts["prompt"] = "Original work"
    return CreationRequest.capture(kind, digest, facts)


@pytest.fixture(params=["workspace", "provider"])
def creation(tmp_db: Database, request: pytest.FixtureRequest):
    original = creation_request(request.param)
    owner, producer, admission = enrolled(tmp_db)
    journal = CreationJournal(tmp_db)
    row = journal.claim(original, owner, admission)
    return tmp_db, owner, producer, admission, journal, original, row


def test_creation_retains_original_inputs_and_association_atomically(creation) -> None:
    db, owner, producer, admission, journal, original, row = creation
    assert row["runtime_custody_json"] == admission.document()
    assert row["creation_facts_json"] == original.facts_json
    assert row["facts_digest"] == original.facts_digest
    assert row["state"] == ("allocating" if original.kind == "workspace" else "prepared")
    assert row["native_session_id"] is None
    assert not db.execute_read("SELECT * FROM sessions")
    observed = db.runtime_epoch(owner)
    assert journal.claim(original, owner, admission) == row
    assert db.runtime_epoch(owner) == observed
    close(db, owner, producer)
    assert journal.read(original) == journal.claim(original, owner, admission) == row
    other = replace(original, key_digest="f" * 64)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        journal.claim(other, owner, admission)
    assert len(db.execute_read(f"SELECT * FROM {creation_table(original.kind)}")) == 1


@pytest.mark.parametrize("change", ["key", "target", "prompt", "options"])
def test_changed_creation_inputs_cannot_recover_original(creation, change: str) -> None:
    _db, owner, _producer, admission, journal, original, row = creation
    facts = json.loads(original.facts_json)
    if change == "key":
        changed = replace(original, key_digest="f" * 64)
    else:
        if change == "prompt":
            if original.kind == "workspace":
                facts["prompt"] = "Replacement work"
            else:
                facts["target"]["venue"]["instance_id"] = "replacement"
        else:
            facts[change]["changed"] = True
        changed = CreationRequest.capture(original.kind, original.key_digest, facts)
    with pytest.raises(RuntimeCustodyError):
        journal.read(changed)
    if change != "key":
        with pytest.raises(RuntimeCustodyError):
            journal.claim(changed, owner, admission)
    assert journal.read(original) == row


@pytest.mark.parametrize("changes", [
    {"producer": "replacement"}, {"epoch": 2}, {"installation": "foreign"},
])
def test_existing_creation_cannot_replace_producer_or_epoch(creation, changes: dict) -> None:
    _db, owner, _producer, admission, journal, original, row = creation
    with pytest.raises(RuntimeCustodyError):
        journal.claim(original, owner, replace(admission, **changes))
    assert journal.read(original) == row


def test_existing_creation_cannot_claim_another_installation(creation) -> None:
    _db, owner, _producer, admission, journal, original, row = creation
    with pytest.raises(RuntimeCustodyError, match="another installation"):
        journal.claim(original, replace(owner, source_fingerprint="foreign"), admission)
    assert journal.read(original) == row


@pytest.mark.parametrize("kind", ["workspace", "provider"])
@pytest.mark.parametrize("closure_first", [False, True])
def test_original_creation_and_closure_have_one_serial_order(
    tmp_db: Database, kind: CreationKind, closure_first: bool,
) -> None:
    owner, producer, admission = enrolled(tmp_db)
    original = creation_request(kind)
    journal = CreationJournal(tmp_db)
    observed = tmp_db.runtime_epoch(owner)
    if closure_first:
        close(tmp_db, owner, producer, expected=observed)
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            journal.claim(original, owner, admission)
        assert not tmp_db.execute_read(f"SELECT * FROM {creation_table(kind)}")
    else:
        row = journal.claim(original, owner, admission)
        with pytest.raises(RuntimeCustodyError, match="changed before closure"):
            close(tmp_db, owner, producer, expected=observed)
        close(tmp_db, owner, producer)
        assert journal.read(original) == row
    assert tmp_db._get_conn().execute("PRAGMA synchronous").fetchone()[0] == 1


@pytest.mark.parametrize("kind", ["workspace", "provider"])
def test_legacy_attempt_cannot_acquire_prospective_custody(tmp_db: Database, kind: CreationKind) -> None:
    original = creation_request(kind)
    owner, _producer, admission = enrolled(tmp_db)
    row = legacy_creation(tmp_db, kind, original.key_digest, original.facts_digest)
    with pytest.raises(RuntimeCustodyError, match="original admission"):
        CreationJournal(tmp_db).claim(original, owner, admission)
    assert legacy_creation(tmp_db, kind, original.key_digest, original.facts_digest) == row
    assert row["runtime_custody_json"] is row["creation_facts_json"] is None


def test_associated_creation_cannot_use_legacy_effect_path(creation) -> None:
    db, _owner, _producer, _admission, journal, original, row = creation
    with pytest.raises(AcquisitionRefused) as refused:
        legacy_creation(db, original.kind, original.key_digest, original.facts_digest)
    assert refused.value.code == "creation_custody_required"
    assert refused.value.effect == "not_started"
    assert journal.read(original) == row


def test_replace_cannot_erase_associated_creation_history(creation) -> None:
    db, _owner, _producer, _admission, journal, original, row = creation
    with pytest.raises(sqlite3.IntegrityError, match="row_retained"):
        db.execute_write(
            f"INSERT OR REPLACE INTO {creation_table(original.kind)} "
            "(key_digest,facts_digest,state,session_id) VALUES (?,?,'allocating','replacement')",
            (original.key_digest, original.facts_digest),
        )
    assert journal.read(original) == row


@pytest.mark.parametrize("recursive", [False, True])
def test_replacement_update_cannot_erase_original_destination(creation, recursive: bool) -> None:
    db, _owner, _producer, _admission, journal, original, row = creation
    table = creation_table(original.kind)
    legacy = legacy_creation(db, original.kind, "f" * 64, original.facts_digest)
    db._get_conn().execute(f"PRAGMA recursive_triggers={'ON' if recursive else 'OFF'}")
    with pytest.raises(sqlite3.IntegrityError, match="row_retained"):
        db.execute_write(
            f"UPDATE OR REPLACE {table} SET key_digest=? WHERE key_digest=?",
            (original.key_digest, legacy["key_digest"]),
        )
    assert journal.read(original) == row
    assert dict(db.execute_read(
        f"SELECT * FROM {table} WHERE key_digest=?", (legacy["key_digest"],),
    )[0]) == legacy


@pytest.mark.parametrize("column,value", [
    ("key_digest", "replacement"), ("facts_digest", "replacement"),
    ("session_id", "replacement"), ("runtime_custody_json", None), ("creation_facts_json", None),
    ("creation_facts_json", "{}"),
])
def test_original_creation_input_cannot_be_replaced(creation, column: str, value: str | None) -> None:
    db, _owner, _producer, _admission, journal, original, row = creation
    with pytest.raises(sqlite3.IntegrityError):
        db.execute_write(f"UPDATE {creation_table(original.kind)} SET {column}=?", (value,))
    with pytest.raises(sqlite3.IntegrityError, match="custody_retained"):
        db.execute_write(f"DELETE FROM {creation_table(original.kind)}")
    assert journal.read(original) == row


@pytest.mark.parametrize("state", ["preparing", "launching", "complete", "terminal"])
def test_inventory_keeps_all_creation_states_and_revisions(creation, state: str) -> None:
    db, owner, _producer, _admission, journal, original, row = creation
    before = db.runtime_epoch(owner)
    db.execute_write(f"UPDATE {creation_table(original.kind)} SET state=?", (state,))
    assert db.runtime_epoch(owner).revision > before.revision
    with pytest.raises(RuntimeCustodyError, match="revision or epoch"):
        db.runtime_custody_inventory(owner, expected=before)
    report = db.runtime_custody_inventory(owner)
    observed, = report["rows"][creation_table(original.kind)]
    assert observed["state"] == state
    assert observed["creation_facts_json"] == original.facts_json
    assert observed["session_id"] == row["session_id"]
    assert any("native-outcome-unqualified" in blocker for blocker in report["blockers"])
    assert report["restore_qualified"] is report["acknowledged"] is False
    assert "acquisition/creation final effects" in report["unmediated_cohorts"]
    assert journal.read(original)["state"] == state


@pytest.mark.parametrize("kind", ["workspace", "provider"])
def test_closed_epoch_refuses_new_legacy_creation_but_preserves_replay(
    tmp_db: Database, kind: CreationKind,
) -> None:
    original = creation_request(kind)
    owner, producer, _admission = enrolled(tmp_db)
    row = legacy_creation(tmp_db, kind, original.key_digest, original.facts_digest)
    close(tmp_db, owner, producer)
    assert legacy_creation(tmp_db, kind, original.key_digest, original.facts_digest) == row
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        legacy_creation(tmp_db, kind, "f" * 64, original.facts_digest)


@pytest.mark.parametrize("bad", [
    "null", "[]", '{"target":{},"target":{},"options":{},"prompt":null}',
    '{"target":{},"options":{},"prompt":NaN}',
    '{"target":{},"options":{},"prompt":false}',
    '{"target":{},"options":{},"prompt":null,"extra":true}',
])
def test_malformed_original_creation_facts_refuse(bad: str) -> None:
    with pytest.raises(RuntimeCustodyError):
        CreationRequest("workspace", "a" * 64, bad)


def test_creation_inventory_refuses_corrupt_original_facts(creation) -> None:
    db, owner, _producer, _admission, _journal, original, _row = creation
    table = creation_table(original.kind)
    db.execute_write(f"DROP TRIGGER {table}_original_input_retained")
    db.execute_write(f"UPDATE {table} SET facts_digest='corrupt'")
    with pytest.raises(RuntimeCustodyError, match="original digest"):
        db.runtime_custody_inventory(owner)
