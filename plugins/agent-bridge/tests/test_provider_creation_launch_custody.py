"""Provider native-handoff DB controls, not provider execution qualification."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3

import pytest

from agent_bridge.creation_custody import CreationJournal, CreationRequest
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_creation_custody import creation_request
from test_runtime_custody import close, enrolled


@pytest.fixture
def native_provider(tmp_db: Database) -> tuple:
    owner, producer, admission = enrolled(tmp_db)
    original = creation_request("provider")
    facts = json.loads(original.facts_json)
    facts["target"]["caller_owner_ref"] = "unshortened:original:owner:reference"
    facts["target"]["venue"]["instance_id"] = "exact-original-instance"
    facts["options"]["env"] = {"ORIGINAL_OPTION": "retained"}
    request = CreationRequest.capture("provider", original.key_digest, facts)
    journal = CreationJournal(tmp_db)
    journal.claim(request, owner, admission)
    handoff = journal.prepare_launch(
        request, owner, admission, consumer_incarnation="provider-consumer-a",
    )
    return tmp_db, owner, producer, admission, journal, request, handoff


def test_provider_handoff_retains_full_original_placement_without_workspace(native_provider: tuple) -> None:
    db, owner, producer, admission, journal, request, handoff = native_provider
    assert set(handoff) == {
        "version", "id", "key_digest", "facts_digest", "admission", "consumer", "provider",
    }
    assert handoff["provider"] == json.loads(request.facts_json)["target"]
    assert handoff["admission"] == admission.document()
    assert handoff["consumer"] == {
        "session_id": journal.read(request)["session_id"], "native_session_id": None,
        "consumer_incarnation": "provider-consumer-a",
    }
    assert journal.read(request)["creation_facts_json"] == request.facts_json
    close(db, owner, producer)
    before = db.runtime_epoch(owner)
    assert journal.prepare_launch(
        request, owner, admission, consumer_incarnation="provider-consumer-a",
    ) == handoff
    assert db.runtime_epoch(owner) == before
    with pytest.raises(RuntimeCustodyError, match="cannot be replaced"):
        journal.prepare_launch(request, owner, admission, consumer_incarnation="replacement")
    with pytest.raises(RuntimeCustodyError):
        journal.prepare_launch(
            request, replace(owner, source_fingerprint="foreign"), admission,
            consumer_incarnation="provider-consumer-a",
        )
    for table in ("workspace_embodiments", "acquisition_attempts", "sessions", "turns"):
        assert not db.execute_read(f"SELECT * FROM {table}")


def test_provider_native_one_shot_unknown_and_late_acceptance_survive_closure(native_provider: tuple) -> None:
    db, owner, producer, admission, journal, request, handoff = native_provider
    assert journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    assert not journal.begin_launch(request, owner, admission, handoff["id"], now=11)
    close(db, owner, producer)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        journal.begin_launch(request, owner, admission, handoff["id"], now=12)
    passive = Database(db.db_path, read_only=True)
    try:
        report = CreationJournal(passive).read_launch(request)
        assert report["handoff"] == handoff and report["started_at"] == 10
        assert report["result"] is None
        with pytest.raises(PermissionError):
            CreationJournal(passive).report_launch(
                request, handoff["id"], native_session_id="native-a", now=12,
            )
    finally:
        passive.close()
    report = journal.report_launch(request, handoff["id"], native_session_id="native-a", now=12)
    assert report["result"] == {"native_session_id": "native-a", "accepted_at": 12}
    assert journal.read(request)["state"] == "complete"
    before = db.runtime_epoch(owner)
    assert journal.report_launch(
        request, handoff["id"], native_session_id="native-a", now=13,
    ) == report
    assert db.runtime_epoch(owner) == before
    with pytest.raises(RuntimeCustodyError, match="cannot be replaced"):
        journal.report_launch(request, handoff["id"], native_session_id="native-b", now=14)
    with pytest.raises(RuntimeCustodyError, match="handoff changed"):
        journal.read_launch(request, "another-handoff")


def test_provider_cannot_acquire_workspace_binding_or_prompt(native_provider: tuple) -> None:
    _db, owner, _producer, admission, journal, request, handoff = native_provider
    journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
    for operation in (
        lambda: journal.begin_allocation(request, owner, admission),
        lambda: journal.begin_binding(request, owner, admission, handoff["id"], now=12),
        lambda: journal.report_binding(request, handoff["id"], now=13),
        lambda: journal.begin_delivery(request, owner, admission, handoff["id"], turn_index=0),
        lambda: journal.complete_launch(request, handoff["id"]),
    ):
        with pytest.raises(RuntimeCustodyError, match="workspaces only"):
            operation()


@pytest.mark.parametrize("field", [
    "launch_handoff_json", "launch_started_at", "launch_result_json", "native_session_id",
])
def test_provider_native_observations_are_immutable(native_provider: tuple, field: str) -> None:
    db, owner, _producer, admission, journal, request, handoff = native_provider
    journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    journal.report_launch(request, handoff["id"], native_session_id="native-a", now=11)
    before = journal.read(request)
    for value in (None, "replacement"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute_write(
                f"UPDATE provider_embodiments SET {field}=? WHERE key_digest=?",
                (value, request.key_digest),
            )
    assert journal.read(request) == before


def test_provider_two_sqlite_owners_admit_only_original_once(native_provider: tuple) -> None:
    db, owner, _producer, admission, _journal, request, handoff = native_provider

    def begin(_index: int) -> bool:
        other = Database(db.db_path)
        try:
            return CreationJournal(other).begin_launch(request, owner, admission, handoff["id"], now=10)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(begin, range(2))) == [False, True]


def test_provider_inventory_refuses_corrupt_terminal_launch(native_provider: tuple) -> None:
    db, owner, _producer, _admission, _journal, request, _handoff = native_provider
    db.execute_write(
        "UPDATE provider_embodiments SET state='terminal' WHERE key_digest=?", (request.key_digest,),
    )
    with pytest.raises(RuntimeCustodyError, match="state disagrees"):
        db.runtime_custody_inventory(owner)


@pytest.mark.parametrize("result", [
    '{"native_session_id":"native-a","accepted_at":9}',
    '{"native_session_id":"native-b","accepted_at":11}',
    '{"native_session_id":"native-a","accepted_at":true}',
    '{"native_session_id":"native-a","accepted_at":11,"extra":true}',
    '{"native_session_id":"native-a","accepted_at":11,"accepted_at":12}',
])
def test_provider_inventory_refuses_malformed_native_acceptance(
    native_provider: tuple, result: str,
) -> None:
    db, owner, _producer, admission, journal, request, handoff = native_provider
    journal.begin_launch(request, owner, admission, handoff["id"], now=10)
    db.execute_write(
        "UPDATE provider_embodiments SET state='complete',native_session_id='native-a',"
        "launch_result_json=? WHERE key_digest=?", (result, request.key_digest),
    )
    with pytest.raises(RuntimeCustodyError):
        db.runtime_custody_inventory(owner)
    with pytest.raises(RuntimeCustodyError):
        journal.read_launch(request)


@pytest.mark.parametrize("change", ["placement", "consumer", "admission", "kind"])
def test_provider_full_handoff_cannot_be_projected_or_reassociated(
    native_provider: tuple, change: str,
) -> None:
    from agent_bridge.creation_launch_custody import launch_observation

    _db, _owner, _producer, _admission, journal, request, handoff = native_provider
    row = journal.read(request)
    changed = json.loads(json.dumps(handoff))
    if change == "placement":
        changed["provider"].pop("caller_owner_ref")
    elif change == "consumer":
        changed["consumer"]["native_session_id"] = "premature-native"
    elif change == "admission":
        changed["admission"] = "{}"
    else:
        changed["workspace"] = changed.pop("provider")
    row["launch_handoff_json"] = json.dumps(changed)
    with pytest.raises(RuntimeCustodyError):
        launch_observation(row, kind="provider")


def test_provider_fresh_handoff_refuses_after_closure_without_changing_row(tmp_db: Database) -> None:
    owner, producer, admission = enrolled(tmp_db)
    request = creation_request("provider")
    journal = CreationJournal(tmp_db)
    row = journal.claim(request, owner, admission)
    close(tmp_db, owner, producer)
    before = tmp_db.runtime_epoch(owner)
    with pytest.raises(RuntimeCustodyError, match="open epoch"):
        journal.prepare_launch(request, owner, admission, consumer_incarnation="consumer-a")
    assert journal.read(request) == row
    assert tmp_db.runtime_epoch(owner) == before
