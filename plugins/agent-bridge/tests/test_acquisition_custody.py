"""Original acquisition DB custody, not native start/resume qualification."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from agent_bridge.acquisition_journal import AcquisitionJournal
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.acquisition_store import AcquisitionStore
from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_acquisition_foundation import request, result
from test_runtime_custody import close, enrolled


def test_acquisition_claim_retains_original_epoch_with_request(tmp_db: Database) -> None:
    owner, producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request()
    claim = journal.claim(original, runtime_admission=admission)
    row = tmp_db.execute_read("SELECT * FROM acquisition_attempts")[0]
    assert row["token"] == claim.token
    assert row["runtime_custody_json"] == admission.document()
    assert tmp_db.execute_read("SELECT * FROM acquisition_effects")[0]["stage"] == "prepared"
    close(tmp_db, owner, producer)
    with pytest.raises(sqlite3.IntegrityError, match="admission_closed"):
        journal.claim(request(delivery_id="another"), runtime_admission=admission)
    assert len(tmp_db.execute_read("SELECT * FROM acquisition_attempts")) == 1


@pytest.mark.parametrize("closure_first", [False, True])
def test_acquisition_launch_begin_checks_original_epoch_once(
    tmp_db: Database, closure_first: bool,
) -> None:
    owner, producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request()
    journal.claim(original, runtime_admission=admission)
    handoff = journal.prepare_launch(
        original, owner, admission, session_id="bridge-a",
        native_session_id="native-a", consumer_incarnation="executor-a",
    )
    if closure_first:
        close(tmp_db, owner, producer)
        with pytest.raises(RuntimeCustodyError, match="open epoch"):
            journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
        assert journal.read_launch(original, handoff["id"])["stage"] == "retained"
    else:
        assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
        assert journal.begin_launch(original, owner, admission, handoff["id"], now=3.0) is False
        close(tmp_db, owner, producer)
        assert journal.read_launch(original, handoff["id"])["stage"] == "starting"
        report = journal.report_launch(
            original, handoff["id"], native_session_id="native-a", now=4.0,
        )
        assert report["stage"] == "native_accepted"
        assert report["handoff"] == handoff
    assert tmp_db.runtime_custody_inventory(owner)["restore_qualified"] is False


@pytest.fixture
def launch(tmp_db: Database):
    owner, producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request()
    claim = journal.claim(original, runtime_admission=admission)
    handoff = journal.prepare_launch(
        original, owner, admission, session_id="bridge-a", native_session_id="native-a",
        consumer_incarnation="executor-a",
    )
    return tmp_db, owner, producer, admission, journal, original, claim, handoff


@pytest.mark.parametrize("field,value", [
    ("prompt", "changed prompt"), ("task_generation", 2),
    ("worktree_id", "other"), ("target", "other"),
])
def test_acquisition_launch_rejects_changed_original_facts(launch, field: str, value: object) -> None:
    _db, owner, _producer, admission, journal, original, _claim, handoff = launch
    changed = original.model_copy(update={field: value})
    with pytest.raises(RuntimeCustodyError, match="original request"):
        journal.begin_launch(changed, owner, admission, handoff["id"], now=2.0)
    assert journal.read_launch(original)["stage"] == "retained"


@pytest.mark.parametrize("changes", [
    {"producer": "unknown"}, {"epoch": 2}, {"installation": "other"},
])
def test_acquisition_launch_rejects_foreign_admission(launch, changes: dict) -> None:
    _db, owner, _producer, admission, journal, original, _claim, handoff = launch
    with pytest.raises(RuntimeCustodyError):
        journal.begin_launch(original, owner, replace(admission, **changes), handoff["id"], now=2.0)
    assert journal.read_launch(original)["stage"] == "retained"


def test_acquisition_launch_recovery_keeps_original_consumer_and_lost_prepare_reply(launch) -> None:
    _db, owner, _producer, admission, journal, original, _claim, handoff = launch
    assert journal.read_launch(original)["handoff"] == handoff
    assert journal.prepare_launch(
        original, owner, admission, session_id="bridge-a", native_session_id="native-a",
        consumer_incarnation="executor-a",
    ) == handoff
    with pytest.raises(RuntimeCustodyError, match="cannot be replaced"):
        journal.prepare_launch(
            original, owner, admission, session_id="bridge-a", native_session_id="native-a",
            consumer_incarnation="replacement",
        )


@pytest.mark.parametrize("now", [0, -1, float("nan"), float("inf"), True])
def test_acquisition_launch_invalid_time_never_grants(launch, now: float) -> None:
    _db, owner, _producer, admission, journal, original, _claim, handoff = launch
    with pytest.raises(RuntimeCustodyError, match="finite positive"):
        journal.begin_launch(original, owner, admission, handoff["id"], now=now)
    assert journal.read_launch(original)["stage"] == "retained"


def test_acquisition_launch_unknown_is_not_refusal_or_completion(launch) -> None:
    db, owner, producer, admission, journal, original, claim, handoff = launch
    assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
    close(db, owner, producer)
    with pytest.raises(AcquisitionRefused, match="fence"):
        journal.refuse(original, claim.token, "timed_out")
    with pytest.raises(AcquisitionRefused, match="fence"):
        journal.complete(original, claim.token, result(original))
    assert journal.read_launch(original)["stage"] == "starting"
    assert db.execute_read("SELECT state FROM acquisition_attempts")[0]["state"] == "pending"
    with pytest.raises(RuntimeCustodyError, match="another native"):
        journal.report_launch(original, handoff["id"], native_session_id="replacement", now=3.0)
    report = journal.report_launch(original, handoff["id"], native_session_id="native-a", now=4.0)
    journal.complete(original, claim.token, result(original))
    assert journal.claim(original, runtime_admission=admission).result == result(original)
    assert journal.report_launch(
        original, handoff["id"], native_session_id="native-a", now=5.0,
    ) == report


def test_acquisition_launch_association_cannot_be_added_to_legacy_attempt(tmp_db: Database) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request()
    journal.claim(original)
    with pytest.raises(RuntimeCustodyError, match="original admission"):
        journal.claim(original, runtime_admission=admission)
    with pytest.raises(RuntimeCustodyError, match="original pending admission"):
        journal.prepare_launch(
            original, owner, admission, session_id="bridge-a", native_session_id="native-a",
            consumer_incarnation="executor-a",
        )


@pytest.mark.parametrize("statement", [
    "UPDATE acquisition_attempts SET runtime_custody_json=NULL",
    "UPDATE acquisition_attempts SET launch_handoff_json=NULL",
    "UPDATE acquisition_attempts SET request_digest='replacement'",
    "DELETE FROM acquisition_attempts",
    "DELETE FROM acquisition_effects",
    "UPDATE acquisition_effects SET data_json='{}'",
])
def test_acquisition_original_history_cannot_be_discarded(launch, statement: str) -> None:
    db, _owner, _producer, _admission, journal, original, _claim, handoff = launch
    with pytest.raises(sqlite3.IntegrityError):
        db.execute_write(statement)
    assert journal.read_launch(original)["handoff"] == handoff


def test_acquisition_store_stage_keeps_original_custody_and_inventory(launch) -> None:
    db, owner, _producer, _admission, journal, original, _claim, handoff = launch
    before = db.runtime_epoch(owner)
    AcquisitionStore(db).stage(original, "launching", session_id="bridge-a")
    assert db.runtime_epoch(owner).revision > before.revision
    assert journal.read_launch(original)["handoff"] == handoff
    inventory = db.runtime_custody_inventory(owner)
    assert inventory["rows"]["acquisition_attempts"][0]["launch_handoff_json"] is not None
    assert "acquisition/creation final effects" in inventory["unmediated_cohorts"]
    assert inventory["acknowledged"] is False


@pytest.mark.parametrize("allocation,intent,native", [
    ("created", "automatic", None),
    ("created", "fresh", None),
    ("reused", "fresh", None),
    ("reused", "automatic", "native-a"),
    ("reused", "continue", "native-a"),
    ("targeted", "automatic", "native-a"),
    ("targeted", "continue", "native-a"),
])
def test_acquisition_launch_distinguishes_creation_from_resume(
    tmp_db: Database, allocation: str, intent: str, native: str | None,
) -> None:
    owner, producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request(
        allocation=allocation, intent=intent,
        prior_session_id=None, prior_native_session_id=native,
    )
    claim = journal.claim(original, runtime_admission=admission)
    handoff = journal.prepare_launch(
        original, owner, admission, session_id="bridge-a",
        native_session_id=native, consumer_incarnation="executor-a",
    )
    assert handoff["consumer"]["native_session_id"] == native
    with pytest.raises(RuntimeCustodyError, match="never reached"):
        journal.report_launch(original, handoff["id"], native_session_id="native-a", now=1.0)
    assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
    close(tmp_db, owner, producer)
    assert journal.report_launch(
        original, handoff["id"], native_session_id="native-a", now=3.0,
    )["stage"] == "native_accepted"
    completed = result(original, acquired="created" if native is None else "resumed")
    journal.complete(original, claim.token, completed)
    assert journal.claim(original, runtime_admission=admission).result == completed


@pytest.mark.parametrize("changes,native", [
    ({"mode": "cli"}, "native-a"),
    ({"intent": "continue", "prior_native_session_id": None}, None),
    ({"conversation_retired": True}, "native-a"),
    ({
        "allocation": "created", "intent": "automatic",
        "prior_session_id": None, "prior_native_session_id": None,
    }, "native-a"),
])
def test_acquisition_launch_refuses_unsupported_or_contradictory_intent(
    tmp_db: Database, changes: dict, native: str | None,
) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request(**changes)
    journal.claim(original, runtime_admission=admission)
    with pytest.raises(RuntimeCustodyError):
        journal.prepare_launch(
            original, owner, admission, session_id="bridge-a", native_session_id=native,
            consumer_incarnation="executor-a",
        )
    assert tmp_db.execute_read("SELECT launch_handoff_json FROM acquisition_attempts")[0][0] is None
    row = tmp_db.execute_read("SELECT * FROM acquisition_attempts")[0]
    assert row["launch_started_at"] is None
    assert row["state"] == "pending"
    assert tmp_db.execute_read("SELECT stage FROM acquisition_effects")[0][0] == "prepared"


def test_acquisition_completed_receipt_cannot_change_observed_launch_kind(launch) -> None:
    _db, owner, _producer, admission, journal, original, claim, handoff = launch
    assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
    journal.report_launch(original, handoff["id"], native_session_id="native-a", now=3.0)
    with pytest.raises(AcquisitionRefused, match="fence"):
        journal.complete(original, claim.token, result(original, acquired="reused"))
    assert journal.read_launch(original)["stage"] == "native_accepted"


def test_retired_acquisition_creation_retains_prior_ids_without_reusing_them(tmp_db: Database) -> None:
    from agent_bridge.acquisition_policy import AcquisitionEvidence, select_acquisition
    from test_acquisition_foundation import conversation

    owner, producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request(conversation_retired=True)
    decision = select_acquisition(
        original, AcquisitionEvidence(True, "native-a", (conversation(),)),
    )
    assert decision.acquired == "created"
    claim = journal.claim(original, runtime_admission=admission)
    handoff = journal.prepare_launch(
        original, owner, admission, session_id="new-bridge", native_session_id=None,
        consumer_incarnation="executor-a",
    )
    assert journal.begin_launch(original, owner, admission, handoff["id"], now=2.0)
    close(tmp_db, owner, producer)
    with pytest.raises(RuntimeCustodyError, match="retired native"):
        journal.report_launch(original, handoff["id"], native_session_id="native-a", now=3.0)
    journal.report_launch(original, handoff["id"], native_session_id="new-native", now=3.0)
    created = result(original, acquired="created", session_id="new-bridge", native_session_id="new-native")
    journal.complete(original, claim.token, created, decision=decision)
    assert journal.claim(original, runtime_admission=admission).result == created
    assert journal.read_launch(original)["handoff"] == handoff


@pytest.mark.parametrize("retired,bridge,native", [
    (False, "new-bridge", "native-a"),
    (False, "bridge-a", "new-native"),
    (True, "bridge-a", None),
])
def test_acquisition_launch_still_refuses_wrong_carried_or_retired_holder(
    tmp_db: Database, retired: bool, bridge: str, native: str | None,
) -> None:
    owner, _producer, admission = enrolled(tmp_db)
    journal = AcquisitionJournal(tmp_db)
    original = request(conversation_retired=retired)
    journal.claim(original, runtime_admission=admission)
    with pytest.raises(RuntimeCustodyError):
        journal.prepare_launch(
            original, owner, admission, session_id=bridge, native_session_id=native,
            consumer_incarnation="executor-a",
        )
