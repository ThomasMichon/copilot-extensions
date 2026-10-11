"""Strict policy and durable fencing without claiming native integration coverage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_bridge.acquisition_contract import (
    AcquisitionRefused, AcquisitionRequest, AcquisitionResult, validate_result,
)
from agent_bridge.acquisition_journal import AcquisitionJournal
from agent_bridge.acquisition_policy import (
    AcquisitionEvidence, ConversationEvidence, select_acquisition,
)
from agent_bridge.db import Database

pytestmark = pytest.mark.contract("agent_bridge.acquisition.foundation")


def request(**changes: object) -> AcquisitionRequest:
    values: dict[str, object] = {
        "target": "worker", "mode": "acp", "task_id": "task-a",
        "task_generation": 1, "worktree_id": "worktree-a",
        "allocation": "reused", "conversation_retired": False,
        "prior_session_id": "bridge-a", "prior_native_session_id": "native-a",
        "delivery_id": "delivery-a", "prompt": "Continue the assigned task",
        "reservation_key": "task-a:1",
    }
    return AcquisitionRequest.model_validate(values | changes)


def conversation(**changes: object) -> ConversationEvidence:
    return replace(
        ConversationEvidence(
            target="worker", mode="acp", task_id="task-a", task_generation=1,
            worktree_id="worktree-a", session_id="bridge-a", native_session_id="native-a",
            liveness="stopped", retired=False,
        ),
        **changes,
    )


def result(req: AcquisitionRequest, **changes: object) -> AcquisitionResult:
    values: dict[str, object] = {
        field: getattr(req, field)
        for field in (
            "target", "mode", "task_id", "task_generation", "worktree_id", "delivery_id",
        )
    }
    values.update(session_id="bridge-a", native_session_id="native-a", acquired="resumed")
    return AcquisitionResult.model_validate(values | changes)


@pytest.mark.parametrize("mode", ["cli", "acp"])
@pytest.mark.parametrize("liveness,expected", [("live", "reused"), ("stopped", "resumed")])
def test_exact_native_identity_selects_same_conversation(
    mode: str, liveness: str, expected: str,
) -> None:
    req = request(mode=mode)
    item = conversation(mode=mode, liveness=liveness)
    selected = select_acquisition(req, AcquisitionEvidence(True, "native-a", (item,)))
    assert selected.acquired == expected
    assert selected.conversation is item
    assert selected.conversation.native_session_id == req.prior_native_session_id
    encoded = result(req, acquired=expected).model_dump_json()
    decoded = AcquisitionResult.model_validate_json(encoded)
    validate_result(req, decoded)
    assert decoded.session_id != decoded.native_session_id


@pytest.mark.parametrize("mode", ["cli", "acp"])
@pytest.mark.parametrize(
    "changed,head,code",
    [
        ({"task_id": "task-b"}, "native-a", "continuity_ambiguous"),
        ({"task_generation": 2}, "native-a", "continuity_ambiguous"),
        ({"mode": "other"}, "native-a", "continuity_ambiguous"),
        ({"session_id": "bridge-b"}, "native-a", "continuity_ambiguous"),
        ({"native_session_id": "native-b"}, "native-a", "head_mismatch"),
        ({"native_session_id": None}, None, "native_identity_missing"),
        ({"retired": True}, "native-a", "conversation_retired"),
        ({"retired": None}, "native-a", "conversation_retired"),
        ({"liveness": "unknown"}, "native-a", "liveness_unknown"),
        ({"busy": True}, "native-a", "session_busy"),
        ({}, "native-b", "head_mismatch"),
        ({"target": "other"}, "native-a", "evidence_scope_mismatch"),
        ({"worktree_id": "other"}, "native-a", "evidence_scope_mismatch"),
    ],
)
def test_continuity_refusals(
    mode: str, changed: dict[str, object], head: str | None, code: str,
) -> None:
    req = request(mode=mode, prior_native_session_id=None)
    if "mode" in changed:
        changed = {"mode": "cli" if mode == "acp" else "acp"}
    item = conversation(mode=mode, **{k: v for k, v in changed.items() if k != "mode"})
    if "mode" in changed:
        item = replace(item, **changed)
    with pytest.raises(AcquisitionRefused) as exc:
        select_acquisition(req, AcquisitionEvidence(True, head, (item,)))
    assert exc.value.code == code


@pytest.mark.parametrize("mode", ["cli", "acp"])
def test_native_id_is_not_replaced_by_matching_worktree(mode: str) -> None:
    req = request(mode=mode)
    evidence = AcquisitionEvidence(
        True, "native-b", (conversation(mode=mode, native_session_id="native-b"),),
    )
    with pytest.raises(AcquisitionRefused, match="no single task-bound"):
        select_acquisition(req, evidence)


@pytest.mark.parametrize("retired", [None, True])
def test_missing_retirement_never_becomes_automatic_resume(retired: bool | None) -> None:
    req = request(conversation_retired=retired)
    evidence = AcquisitionEvidence(True, "native-a", (conversation(),))
    if retired:
        assert select_acquisition(req, evidence).acquired == "created"
    else:
        with pytest.raises(AcquisitionRefused) as exc:
            select_acquisition(req, evidence)
        assert exc.value.code == "retirement_unknown"


def test_new_fresh_and_targeted_are_distinct() -> None:
    new = request(
        allocation="created", prior_session_id=None, prior_native_session_id=None,
    )
    assert select_acquisition(new, AcquisitionEvidence(True, None)).acquired == "created"
    with pytest.raises(AcquisitionRefused) as exc:
        select_acquisition(new, AcquisitionEvidence(True, "foreign", (conversation(),)))
    assert exc.value.code == "new_allocation_conflict"
    targeted = request(
        allocation="targeted", prior_session_id=None, prior_native_session_id=None,
    )
    with pytest.raises(AcquisitionRefused) as exc:
        select_acquisition(targeted, AcquisitionEvidence(True, None))
    assert exc.value.code == "continuity_ambiguous"
    fresh = request(
        allocation="targeted", intent="fresh", prior_session_id=None,
        prior_native_session_id=None,
    )
    assert select_acquisition(fresh, AcquisitionEvidence(True, "old", (conversation(),))).acquired == "created"
    with pytest.raises(AcquisitionRefused) as exc:
        select_acquisition(fresh, AcquisitionEvidence(True, "native-a", (conversation(liveness="live"),)))
    assert exc.value.code == "live_holder"


@pytest.mark.parametrize(
    "evidence,code",
    [
        (AcquisitionEvidence(False, None), "authority_unknown"),
        (AcquisitionEvidence(True, None, pending_transition=True), "transition_pending"),
        (AcquisitionEvidence(True, "native-a", (
            conversation(liveness="live"), conversation(session_id="bridge-b", liveness="live"),
        )), "multiple_live_holders"),
        (AcquisitionEvidence(True, "native-a", (
            conversation(), conversation(task_id="task-b", session_id="bridge-b", liveness="live"),
        )), "foreign_live_holder"),
    ],
)
def test_unknown_and_competing_authority(
    evidence: AcquisitionEvidence, code: str,
) -> None:
    with pytest.raises(AcquisitionRefused) as exc:
        select_acquisition(request(), evidence)
    assert exc.value.code == code


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2}, {"mode": "headless"}, {"task_generation": "1"},
        {"conversation_retired": "false"},
        {"intent": "continue", "conversation_retired": True},
        {"intent": "fresh"},
    ],
)
def test_request_rejects_unsupported_or_conflicting_facts(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        request(**changes)


def test_additive_fields_do_not_override_acquisition_authority() -> None:
    req = request(force=True, task={"id": "other"}, native_id="native-b")
    assert req.model_dump() == request().model_dump()
    decoded = result(req, future_observation={"status": "ready"})
    validate_result(req, decoded)
    assert decoded.model_dump() == result(req).model_dump()
    with pytest.raises(AcquisitionRefused) as error:
        select_acquisition(req, AcquisitionEvidence(
            True, "native-a", (conversation(task_id="other"),),
        ))
    assert error.value.code == "continuity_ambiguous"
    with pytest.raises(AcquisitionRefused) as error:
        validate_result(req, result(req, task_id="other", force=True))
    assert error.value.code == "result_mismatch"


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"native_session_id": "native-b"}, "native_identity_changed"),
        ({"session_id": "bridge-b"}, "handle_changed"),
        ({"acquired": "created"}, "continuation_recreated"),
        ({"mode": "cli"}, "result_mismatch"),
        ({"task_id": "other"}, "result_mismatch"),
        ({"delivery_id": "other"}, "result_mismatch"),
    ],
)
def test_output_validation_is_not_native_identity_guessing(
    changes: dict[str, object], code: str,
) -> None:
    req = request()
    with pytest.raises(AcquisitionRefused) as exc:
        validate_result(req, result(req, **changes))
    assert exc.value.code == code
    with pytest.raises(ValidationError):
        result(req, native_session_id=None)


def test_retirement_result_cannot_revive_carried_id() -> None:
    req = request(conversation_retired=True)
    with pytest.raises(AcquisitionRefused) as exc:
        validate_result(req, result(req, acquired="created"))
    assert exc.value.code == "retired_identity_reused"
    validate_result(req, result(req, acquired="created", native_session_id="native-new"))


@pytest.mark.parametrize("field", ["charter", "model", "effort"])
def test_no_override_preserves_configured_options_but_explicit_mismatch_refuses(field: str) -> None:
    evidence = AcquisitionEvidence(True, "native-a", (conversation(**{field: "configured"}),))
    assert select_acquisition(request(), evidence).acquired == "resumed"
    with pytest.raises(AcquisitionRefused) as error:
        select_acquisition(request(**{field: "different"}), evidence)
    assert error.value.code == "options_mismatch"


def test_current_generation_is_not_immutable_conversation_lineage() -> None:
    evidence = AcquisitionEvidence(True, "native-a", (conversation(task_generation=1),))
    assert select_acquisition(request(task_generation=2), evidence).acquired == "resumed"


def test_created_allocation_cannot_claim_fabricated_reuse() -> None:
    req = request(allocation="created", prior_session_id=None, prior_native_session_id=None)
    with pytest.raises(AcquisitionRefused) as error:
        validate_result(req, result(req, acquired="reused"))
    assert error.value.code == "new_allocation_reused"


def test_claim_rolls_back_if_prepared_facts_cannot_be_persisted(tmp_db: Database) -> None:
    journal = AcquisitionJournal(tmp_db)
    tmp_db.execute_write(
        "CREATE TRIGGER reject_prepared BEFORE INSERT ON acquisition_effects "
        "BEGIN SELECT RAISE(ABORT, 'test persistence failure'); END"
    )
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="test persistence failure"):
        journal.claim(request())
    assert not tmp_db.execute_read("SELECT * FROM acquisition_attempts")
    assert not tmp_db.execute_read("SELECT * FROM acquisition_effects")


def test_completed_retry_returns_exact_result_without_delivery(tmp_db: Database) -> None:
    journal = AcquisitionJournal(tmp_db)
    req = request()
    claim = journal.claim(req)
    assert claim.token is not None
    expected = result(req)
    journal.complete(req, claim.token, expected)
    replay = journal.claim(AcquisitionRequest.model_validate_json(req.model_dump_json()))
    assert replay.token is None
    assert replay.result == expected
    assert len(tmp_db.execute_read("SELECT * FROM acquisition_attempts")) == 1


@pytest.mark.parametrize("changed", [
    {"prompt": "Another prompt"}, {"task_id": "other"}, {"mode": "cli"},
    {"task_generation": 2}, {"intent": "continue"}, {"charter": "other"},
])
def test_delivery_identity_cannot_be_rebound(
    tmp_db: Database, changed: dict[str, object],
) -> None:
    journal = AcquisitionJournal(tmp_db)
    journal.claim(request())
    with pytest.raises(AcquisitionRefused) as exc:
        journal.claim(request(**changed))
    assert exc.value.code == "delivery_conflict"


def test_pending_survives_database_restart_and_does_not_expire(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    db = Database(path)
    AcquisitionJournal(db).claim(request())
    db.execute_write("UPDATE acquisition_attempts SET created_at=0, updated_at=0")
    db.close()
    reopened = Database(path)
    try:
        journal = AcquisitionJournal(reopened)
        for req in (request(), request(delivery_id="delivery-b")):
            with pytest.raises(AcquisitionRefused) as exc:
                journal.claim(req)
            assert exc.value.code == "acquisition_pending"
    finally:
        reopened.close()


def test_two_database_connections_admit_one_target(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.db"

    def claim(delivery: str) -> str:
        db = Database(path)
        try:
            acquired = AcquisitionJournal(db).claim(request(delivery_id=delivery))
            assert acquired.token
            return "claimed"
        except AcquisitionRefused as exc:
            return exc.code
        finally:
            db.close()

    # Bootstrap outside concurrent initialization; the contract tested here is
    # atomic claim over distinct SQLite connections, not database provisioning.
    db = Database(path)
    AcquisitionJournal(db)
    db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ["delivery-a", "delivery-b"]))
    assert sorted(outcomes) == ["acquisition_pending", "claimed"]


def test_completion_and_refusal_require_exact_pending_fence(tmp_db: Database) -> None:
    journal = AcquisitionJournal(tmp_db)
    req = request()
    token = journal.claim(req).token
    assert token
    for wrong in ("wrong-token",):
        with pytest.raises(AcquisitionRefused) as exc:
            journal.complete(req, wrong, result(req))
        assert exc.value.code == "fence_lost"
    journal.refuse(req, token, "authority_unknown")
    with pytest.raises(AcquisitionRefused) as exc:
        journal.claim(req)
    assert exc.value.code == "authority_unknown"
    with pytest.raises(AcquisitionRefused) as exc:
        journal.complete(req, token, result(req))
    assert exc.value.code == "fence_lost"
    assert journal.claim(request(delivery_id="delivery-b")).token
