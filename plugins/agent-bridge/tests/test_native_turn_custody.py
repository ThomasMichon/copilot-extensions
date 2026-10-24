"""Original turn custody controls, not actual native transport qualification."""

from __future__ import annotations

from dataclasses import replace
import json
import sqlite3

import pytest

from agent_bridge.db import Database
from agent_bridge.runtime_custody import RuntimeCustodyError
from test_runtime_custody import close, enrolled, session


def test_native_turn_storage_requires_original_handoff_and_separate_outcomes(tmp_db: Database) -> None:
    columns = {row[1] for row in tmp_db.execute_read("PRAGMA table_info(turns)")}
    assert {
        "native_handoff_json", "native_handoff_started_at", "native_accepted_at",
        "native_consumed_at", "native_outcome",
    } <= columns


@pytest.fixture
def native_turn(tmp_db: Database):
    from agent_bridge.turn_custody import NativeTurnConsumer

    owner, producer, admission = enrolled(tmp_db)
    session(tmp_db, "s", admission)
    tmp_db.update_session_acp_id("s", "native-a")
    consumer = NativeTurnConsumer("s", "native-a", "transport-a", 102, "birth-a")
    tmp_db.create_turn("s", 0, "original prompt", 1.0, runtime_admission=admission,
                       delivery_id="original-delivery", native_consumer=consumer)
    return tmp_db, owner, producer, admission, consumer


def prepare(selected):
    db, owner, _, admission, consumer = selected
    return db.prepare_native_turn_handoff(
        owner, admission, consumer, 0, expected_prompt="original prompt",
    )


def begin(selected, handoff, *, now=2.0):
    db, owner, _, admission, consumer = selected
    return db.begin_native_turn_handoff(
        owner, admission, consumer, 0, handoff["id"], expected_prompt="original prompt", now=now,
    )


def outcome(selected, handoff, value, *, now=3.0):
    db, _, _, _, consumer = selected
    return db.record_native_turn_handoff(consumer, 0, handoff["id"], outcome=value, now=now)


def test_original_turn_handoff_is_one_shot_and_preserves_input(native_turn) -> None:
    db, _, _, admission, consumer = native_turn
    original = db.get_turn("s", 0)
    handoff = prepare(native_turn)
    assert prepare(native_turn) == handoff
    assert handoff["consumer"] == consumer.document()
    assert handoff["admission"] == admission.document()
    assert begin(native_turn, handoff) is True
    assert begin(native_turn, handoff) is False
    row = db.get_turn("s", 0)
    for name in ("prompt", "acquisition_delivery_id", "native_admission_json", "runtime_custody_json"):
        assert row[name] == original[name]
    receipt = db.read_native_turn_handoff(consumer, 0, handoff["id"])
    assert receipt["stage"] == "sending" and receipt["handoff"] == handoff
    assert "prompt" not in receipt


@pytest.mark.parametrize("stage", ["offered", "sending", "unknown", "native_accepted", "consumed"])
def test_original_native_outcome_survives_closure_replacement_and_reopen(native_turn, stage) -> None:
    db, owner, producer, _, consumer = native_turn
    handoff = prepare(native_turn)
    if stage != "offered":
        assert begin(native_turn, handoff)
    if stage not in ("offered", "sending"):
        assert outcome(native_turn, handoff, stage) == stage
    close(db, owner, producer)
    db.update_session_acp_id("s", "replacement-native")
    reopened = Database(db.db_path, read_only=True)
    try:
        assert reopened.read_native_turn_handoff(consumer, 0, handoff["id"])["stage"] == stage
        assert reopened.runtime_custody_inventory(owner)["restore_qualified"] is False
    finally:
        reopened.close()
    with pytest.raises(RuntimeCustodyError):
        begin(native_turn, handoff, now=4.0)
    if stage == "offered":
        with pytest.raises(RuntimeCustodyError, match="final admission"):
            outcome(native_turn, handoff, "native_accepted")
    else:
        assert outcome(native_turn, handoff, "consumed", now=5.0) == "consumed"
        assert outcome(native_turn, handoff, "unknown", now=6.0) == "consumed"


@pytest.mark.parametrize("field,value", [
    ("session_id", "other"), ("native_session_id", "other-native"),
    ("incarnation", "successor"), ("pid", 103), ("process_start", "reused-pid"),
])
def test_foreign_native_consumer_cannot_inherit_original_handoff(native_turn, field, value) -> None:
    db, owner, producer, admission, consumer = native_turn
    handoff = prepare(native_turn)
    foreign = replace(consumer, **{field: value})
    selected = db, owner, producer, admission, foreign
    with pytest.raises(RuntimeCustodyError):
        prepare(selected)
    with pytest.raises(RuntimeCustodyError):
        begin(selected, handoff)
    with pytest.raises(RuntimeCustodyError):
        db.read_native_turn_handoff(foreign, 0, handoff["id"])
    assert db.get_turn("s", 0)["native_handoff_started_at"] is None


def test_native_context_change_before_final_admission_refuses(native_turn) -> None:
    db, _, _, _, _ = native_turn
    handoff = prepare(native_turn)
    db.update_session_acp_id("s", "replacement-native")
    with pytest.raises(RuntimeCustodyError, match="native"):
        begin(native_turn, handoff)
    assert db.get_turn("s", 0)["native_handoff_started_at"] is None


@pytest.mark.parametrize("column,value", [
    ("native_handoff_json", None), ("native_handoff_started_at", None),
    ("native_accepted_at", None), ("native_consumed_at", None),
    ("native_outcome", None), ("prompt", "replacement"),
    ("acquisition_delivery_id", "replacement"), ("native_admission_json", "{}"),
])
def test_native_handoff_and_original_input_cannot_be_erased(native_turn, column, value) -> None:
    db, _, _, _, _ = native_turn
    handoff = prepare(native_turn)
    assert begin(native_turn, handoff)
    assert outcome(native_turn, handoff, "unknown") == "unknown"
    assert outcome(native_turn, handoff, "native_accepted", now=4.0) == "native_accepted"
    assert outcome(native_turn, handoff, "consumed", now=5.0) == "consumed"
    before = db.get_turn("s", 0)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute_write(f"UPDATE turns SET {column}=? WHERE session_id='s' AND turn_index=0", (value,))
    assert db.get_turn("s", 0) == before
    assert json.loads(before["native_handoff_json"]) == handoff


def test_legacy_and_completed_turns_cannot_acquire_retrospective_native_custody(native_turn) -> None:
    db, owner, _, admission, consumer = native_turn
    db.create_turn("s", 1, "possibly sent legacy prompt", 1.0)
    with pytest.raises(RuntimeCustodyError, match="Legacy"):
        db.prepare_native_turn_handoff(
            owner, admission, consumer, 1, expected_prompt="possibly sent legacy prompt",
        )
    db.create_turn("s", 2, "associated prior prompt", 1.0, runtime_admission=admission)
    db.update_turn("s", 2, completed_at=2.0)
    with pytest.raises(RuntimeCustodyError, match="Prior"):
        db.prepare_native_turn_handoff(
            owner, admission, consumer, 2, expected_prompt="associated prior prompt",
        )
    assert all(row["native_handoff_json"] is None for row in db.get_turns("s")[1:])


def test_changed_expected_prompt_refuses_before_any_handoff_or_grant(native_turn) -> None:
    db, owner, _, admission, consumer = native_turn
    original = db.get_turn("s", 0)["native_handoff_json"]
    with pytest.raises(RuntimeCustodyError, match="input changed"):
        db.prepare_native_turn_handoff(owner, admission, consumer, 0, expected_prompt="other input")
    assert db.get_turn("s", 0)["native_handoff_json"] == original
    handoff = prepare(native_turn)
    with pytest.raises(RuntimeCustodyError, match="input changed"):
        db.begin_native_turn_handoff(
            owner, admission, consumer, 0, handoff["id"], expected_prompt="other input", now=2.0,
        )
    assert db.get_turn("s", 0)["native_handoff_started_at"] is None


def test_associated_incomplete_history_cannot_gain_retrospective_native_handoff(native_turn) -> None:
    db, owner, _, admission, consumer = native_turn
    db.create_turn("s", 1, "possibly already sent", 1.0, runtime_admission=admission)
    with pytest.raises(RuntimeCustodyError, match="handoff is absent"):
        db.prepare_native_turn_handoff(
            owner, admission, consumer, 1, expected_prompt="possibly already sent",
        )
    assert db.get_turn("s", 1)["native_handoff_json"] is None
    retrospective = json.loads(db.get_turn("s", 0)["native_handoff_json"])
    retrospective["turn_index"] = 1
    with pytest.raises(sqlite3.IntegrityError, match="retroactive_handoff"):
        db.execute_write(
            "UPDATE turns SET native_handoff_json=? WHERE session_id='s' AND turn_index=1",
            (json.dumps(retrospective),),
        )


@pytest.mark.parametrize("now", [None, True, 0.0, -1.0, float("nan"), float("inf")])
def test_invalid_native_attempt_time_never_acquires_or_resets_permission(native_turn, now) -> None:
    db, _, _, _, _ = native_turn
    handoff = prepare(native_turn)
    with pytest.raises(RuntimeCustodyError):
        begin(native_turn, handoff, now=now)
    assert db.get_turn("s", 0)["native_handoff_started_at"] is None
    assert begin(native_turn, handoff)
    with pytest.raises(RuntimeCustodyError):
        outcome(native_turn, handoff, "native_accepted", now=now)
    assert db.get_turn("s", 0)["native_accepted_at"] is None
    assert begin(native_turn, handoff) is False


@pytest.mark.parametrize("value", ["not-a-time", 0.0, -1.0, float("inf")])
def test_sql_guards_refuse_invalid_native_outcome_time(native_turn, value) -> None:
    db, _, _, _, _ = native_turn
    handoff = prepare(native_turn)
    assert begin(native_turn, handoff)
    with pytest.raises(sqlite3.IntegrityError, match="time_invalid"):
        db.execute_write(
            "UPDATE turns SET native_accepted_at=? WHERE session_id='s' AND turn_index=0", (value,),
        )
    assert db.get_turn("s", 0)["native_accepted_at"] is None


def test_late_acceptance_and_consumption_are_separate_from_unknown_and_turn_completion(native_turn) -> None:
    db, owner, _, _, consumer = native_turn
    handoff = prepare(native_turn)
    assert begin(native_turn, handoff)
    assert outcome(native_turn, handoff, "unknown") == "unknown"
    assert outcome(native_turn, handoff, "consumed", now=4.0) == "consumed"
    assert outcome(native_turn, handoff, "native_accepted", now=5.0) == "consumed"
    db.update_turn("s", 0, completed_at=6.0)
    receipt = db.read_native_turn_handoff(consumer, 0, handoff["id"])
    assert receipt["native_consumed_at"] == 4.0 and receipt["native_accepted_at"] == 5.0
    assert db.get_turn("s", 0)["native_outcome"] == "unknown"
    report = db.runtime_custody_inventory(owner)
    assert "native-send settlement" in report["unmediated_cohorts"]
    assert report["restore_qualified"] is False and report["acknowledged"] is False
