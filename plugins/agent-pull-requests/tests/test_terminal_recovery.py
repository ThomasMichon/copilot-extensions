"""Actual same-envelope terminal compaction and protected readback, isolated only."""

from __future__ import annotations

import copy
import json
import time

import pytest

from test_workspace_terminal_retention import (
    environment as environment, integration as integration, workspace as workspace,
    load, terminal, drain,
)
from test_review_owner_store import prepared_intent as prepared_intent
from test_workspace_comment_preparation import comment_preparer as comment_preparer
from agent_pull_requests.attention import Cursor
from agent_pull_requests.review_workspace import _digest
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed
from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
from agent_pull_requests.terminal_recovery_executor import reconcile_terminal
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def expires() -> int:
    return int(time.time()) + 600


def prepare(
    workspace: tuple, *, selected: dict | None = None, attempt: bool = True, capture: bool = False,
    materialize: bool = False,
) -> tuple:
    operations, _, backend, original, initial, _ = workspace
    owner = operations.store
    owner.terminal_recovery = True
    recovery = TerminalRecoveryStore(owner)
    recovery.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    load(operations, initial["id"])
    current = operations.cache.lookup(initial["scope"])["generation"]
    if materialize:
        operations.execute(
            "lookup", {"number": current["scope"]["number"], "generation_id": current["id"]},
            expires_at=expires(), checkpoint=lambda: None,
        )
    intent = copy.deepcopy(selected if selected is not None else original)
    intent.update(scope=current["scope"], generation_id=current["id"])
    if intent["version"] == 2:
        owner.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires())
    admitted = owner.admit_review(intent, expires_at=expires())["operation"]
    if capture:
        from agent_pull_requests.attention import Interest
        from agent_pull_requests.attention_acquisition import AttentionInputs
        from agent_pull_requests.attention_store import AttentionStore
        from agent_pull_requests.conversation_store import ConversationStore
        from agent_pull_requests.feedback_capture_store import FeedbackCaptureStore
        from agent_pull_requests.review_feedback import FeedbackInputs, ReviewFeedback
        from agent_pull_requests.workspace_conversation import observation_interval

        owner.clock = lambda: observation_interval(current["sections"])[1] + 1
        AttentionStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
        FeedbackCaptureStore(owner).adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
        FeedbackCaptureStore(owner).adopt_readback(lifecycle_checkpoint=lambda: None, expires_at=expires())
        interests = (Interest("maintainer", True, "consumer-a"),)
        observation = ConversationStore(owner).observe_workspace(
            current, expires_at=expires(), terminal_target=operations.retention_target(current["scope"]),
            attention=AttentionInputs(current["id"], interests, True, False, False, False, False),
        )
        ReviewFeedback(
            owner, current, FeedbackInputs(current["id"], interests, 60), expires_at=expires(),
            checkpoint=lambda: None, position=Cursor(**observation["cursor"]),
        ).prepare(admitted)
    if attempt:
        owner.begin_review_action(intent["operation_id"], intent["scope"], intent["actions"][0]["id"], expires_at=expires())
    decision, _ = terminal(operations, current)
    backend.pr["status"] = "completed"
    policies = {
        intent["operation_id"]: {
            "owner": "original-protected-owner", "deadline_at": expires() - 10,
            "escalation_ref": "original-recovery-escalation",
        },
    }
    return operations, recovery, intent, admitted, decision, policies, backend


def compact(prepared: tuple) -> dict:
    operations, _, intent, _, decision, policies, _ = prepared
    return operations.retention.compact_recovery(
        intent["scope"], Cursor(**decision["cursor"]), policies,
        expires_at=expires(), checkpoint=lambda: None, deadline=time.monotonic() + 2,
        lifecycle_checkpoint=lambda: None,
    )


def matching_thread(intent: dict, operation: dict) -> dict:
    from agent_worktrees.providers.azure_owned_review import AdoReviewContext
    action = intent["actions"][0]
    return {
        "id": 100, "status": 1,
        "properties": {"agent-pull-requests.action": {"$value": AdoReviewContext.marker(operation, action)}},
        "comments": [{
            "id": 1, "content": action["body"], "author": {"id": intent["scope"]["principal_id"]},
        }],
    }


def reconcile(prepared: tuple) -> dict:
    _, recovery, intent, _, _, _, backend = prepared
    return reconcile_terminal(
        recovery, intent["scope"], intent["operation_id"], context_factory=backend.context,
        checkpoint=backend.checkpoint, expires_at=expires(), budget_seconds=10,
    )


def test_actual_compaction_restart_keeps_unknown_and_retired_ids_without_bodies(workspace: tuple) -> None:
    prepared = prepare(workspace)
    operations, recovery, intent, admitted, _, _, _ = prepared
    before = json.loads(operations.store.path.read_bytes())
    compact(prepared)
    raw = operations.store.path.read_bytes()
    document = json.loads(raw)
    assert not document["reviews"]
    assert document["replay"] == before["replay"]
    retained = recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())
    assert retained["intent_id"] == admitted["intent_id"]
    assert [action["state"] for action in retained["actions"]] == ["unknown", "retired"]
    assert retained["actions"][0]["attempt_id"] == before["reviews"][0]["actions"][0]["attempt_id"]
    assert intent["actions"][0]["body"].encode() not in raw
    assert "body" not in retained["actions"][0]
    restarted = ReplayStore(
        operations.store.root, operations.store.namespace, service_identity=operations.store.service_identity,
        terminal_retention=True, terminal_recovery=True,
    )
    assert TerminalRecoveryStore(restarted).inspect(
        intent["scope"], intent["operation_id"], expires_at=expires(),
    ) == retained
    assert compact(prepared)["status"] == "compacted"
    with pytest.raises(TerminalAdmissionClosed):
        restarted.admit_review(intent, expires_at=expires())
    with pytest.raises(ValueError):
        restarted.record_review_receipt(
            intent["operation_id"], intent["scope"], intent["actions"][0]["id"],
            retained["actions"][0]["attempt_id"],
            {"origin": "response", "platform_ids": {"thread_id": 100, "comment_id": 1}, "proof": None},
            expires_at=expires(),
        )


def test_old_reader_refuses_adoption_without_rewriting_and_original_policy_cannot_change(workspace: tuple) -> None:
    prepared = prepare(workspace)
    operations, _, intent, _, _, policies, _ = prepared
    compact(prepared)
    before = operations.store.path.read_bytes()
    prior = ReplayStore(
        operations.store.root, operations.store.namespace,
        service_identity=operations.store.service_identity, terminal_retention=True,
    )
    with pytest.raises(ReplayStoreInvalid, match="recovery reader"):
        prior.inspect_review(intent["operation_id"], intent["scope"], expires_at=expires())
    assert operations.store.path.read_bytes() == before
    policies[intent["operation_id"]]["deadline_at"] += 1
    with pytest.raises(ValueError, match="custody"):
        compact(prepared)
    assert operations.store.path.read_bytes() == before


@pytest.mark.parametrize("boundary", [1, 2])
def test_recovery_adoption_rechecks_reader_exclusion_after_both_validations(
    workspace: tuple, monkeypatch: pytest.MonkeyPatch, boundary: int,
) -> None:
    operations, _, _, _, _, _ = workspace
    owner = operations.store
    owner.terminal_recovery = True
    recovery = TerminalRecoveryStore(owner)
    before = owner.path.read_bytes()
    validate = owner._validate_owner
    owned = True
    validations = 0

    def lifecycle() -> None:
        if not owned:
            raise PermissionError("exclusive reader custody withdrawn")

    def interrupted(document: dict) -> None:
        nonlocal owned, validations
        validate(document)
        if "terminal_recovery" in document:
            validations += 1
            if validations == boundary:
                owned = False

    monkeypatch.setattr(owner, "_validate_owner", interrupted)
    with pytest.raises(PermissionError, match="custody withdrawn"):
        recovery.adopt(lifecycle_checkpoint=lifecycle, expires_at=expires())
    assert validations == boundary
    assert owner.path.read_bytes() == before


def test_full_review_budget_refuses_compaction_without_eviction_or_other_reservation(workspace: tuple) -> None:
    prepared = prepare(workspace)
    operations, _, intent, _, decision, _, _ = prepared
    drain(operations, intent["scope"], decision["cursor"], time.monotonic() + 1)

    def limit(document: dict) -> None:
        document["capacities"]["review_bytes"] = 1024 + sum(
            operation["reserved_bytes"] for operation in document["reviews"]
        )

    operations.store._review_transaction(limit, expires_at=expires())
    before = operations.store.path.read_bytes()
    with pytest.raises(ValueError, match="capacity"):
        compact(prepared)
    assert operations.store.path.read_bytes() == before


def test_original_feedback_capture_remains_bound_without_claiming_handled_progress(workspace: tuple) -> None:
    from agent_pull_requests.attention import Interest
    from agent_pull_requests.attention_acquisition import AttentionInputs
    from agent_pull_requests.attention_read import AttentionReader
    from agent_pull_requests.conversation_store import ConversationStore
    from agent_pull_requests.review_feedback import FeedbackInputs
    from agent_pull_requests.workspace_conversation import observation_interval

    prepared = prepare(workspace, capture=True)
    operations, recovery, intent, _, _, _, _ = prepared
    interests = (Interest("maintainer", True, "consumer-a"),)
    operations.attention_owner = lambda generation: AttentionInputs(
        generation["id"], interests, True, False, False, False, False,
    )
    operations.feedback_owner = lambda generation: FeedbackInputs(generation["id"], interests, 60)
    current = operations.cache.lookup(intent["scope"])["generation"]
    _, terminal_value = terminal(operations, current, second=22)

    def observe_attention(value: dict) -> None:
        operations.store.clock = lambda: observation_interval(value["sections"])[1] + 1
        ConversationStore(operations.store).observe_workspace(
            value, expires_at=expires(), terminal_target=operations.retention_target(value["scope"]),
            attention=operations.attention_owner(value),
        )

    observe_attention(terminal_value)
    before = json.loads(operations.store.path.read_bytes())
    compact(prepared)
    after = json.loads(operations.store.path.read_bytes())
    assert after["feedback_captures"] == before["feedback_captures"]
    assert after["attention"] == before["attention"]
    assert after["feedback_captures"]["records"][0]["readback"] is None
    assert recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())["actions"][0]["state"] == "unknown"
    reader = AttentionReader(operations, consumer="consumer-a", max_age_seconds=60, aging_seconds=30, historical=True)
    before_read = operations.store.path.read_bytes()
    result = reader.execute("read", {"limit": 16, "after": None}, expires_at=expires(), checkpoint=lambda: None)
    assert result["items"][0]["status"] == "terminal"
    assert operations.store.path.read_bytes() == before_read
    _, reopened = terminal(operations, current, state="active", second=30)
    observe_attention(reopened)
    result = reader.execute("read", {"limit": 16, "after": None}, expires_at=expires(), checkpoint=lambda: None)
    assert result["items"][0]["status"] == "unavailable"
    assert result["items"][0]["reasons"] == ("feedback-reconciliation-pending",)


def test_compaction_rejoins_actual_admission_even_after_a_prior_persisted_drain(workspace: tuple) -> None:
    prepared = prepare(workspace)
    operations, _, intent, _, decision, policies, _ = prepared
    drain(operations, intent["scope"], decision["cursor"], time.monotonic() + 1)
    before = operations.store.path.read_bytes()
    with operations.retention.request("load", {"number": 42}, expires_at=expires(), checkpoint=lambda: None):
        with pytest.raises(TimeoutError, match="drain incomplete"):
            operations.retention.compact_recovery(
                intent["scope"], Cursor(**decision["cursor"]), policies,
                expires_at=expires(), checkpoint=lambda: None, deadline=time.monotonic() + 0.02,
                lifecycle_checkpoint=lambda: None,
            )
    assert operations.store.path.read_bytes() == before
    assert compact(prepared)["status"] == "compacted"


@pytest.mark.parametrize("cause", ["deadline", "custody"])
@pytest.mark.parametrize("boundary", [1, 2])
def test_compaction_rechecks_lifecycle_after_both_complete_envelope_validations(
    workspace: tuple, monkeypatch: pytest.MonkeyPatch, cause: str, boundary: int,
) -> None:
    from types import SimpleNamespace
    from agent_pull_requests import workspace_retention

    prepared = prepare(workspace)
    operations, _, intent, _, decision, policies, _ = prepared
    drain(operations, intent["scope"], decision["cursor"], time.monotonic() + 1)
    before = operations.store.path.read_bytes()
    now = [time.monotonic()]
    deadline = now[0] + 30
    owned = True
    validations = 0
    validate = operations.store._validate_owner

    def lifecycle() -> None:
        if not owned:
            raise PermissionError("original workspace custody withdrawn")

    def interrupted(document: dict) -> None:
        nonlocal owned, validations
        validate(document)
        if not document["reviews"] and document["terminal_recovery"]["operations"]:
            validations += 1
            if validations == boundary:
                if cause == "deadline":
                    now[0] = deadline
                else:
                    owned = False

    monkeypatch.setattr(workspace_retention, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(operations.store, "_validate_owner", interrupted)
    with pytest.raises(TimeoutError if cause == "deadline" else PermissionError):
        operations.retention.compact_recovery(
            intent["scope"], Cursor(**decision["cursor"]), policies,
            expires_at=expires(), checkpoint=lambda: None, deadline=deadline,
            lifecycle_checkpoint=lifecycle,
        )
    assert validations == boundary
    assert operations.store.path.read_bytes() == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_compaction_interruption_keeps_complete_original_or_same_recovery(
    workspace: tuple, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    from agent_pull_requests import watch_replay_store as storage
    prepared = prepare(workspace)
    operations, recovery, intent, _, decision, _, _ = prepared
    drain(operations, intent["scope"], decision["cursor"], time.monotonic() + 1)
    before = operations.store.path.read_bytes()
    write = storage._atomic_write_json

    def interrupt(path, document):
        if not document["terminal_recovery"]["operations"]:
            return write(path, document)
        if uncertain:
            write(path, document)
            raise StateCommitUncertain("synthetic post-compaction interruption")
        raise OSError("synthetic pre-compaction interruption")

    monkeypatch.setattr(storage, "_atomic_write_json", interrupt)
    with pytest.raises(StateCommitUncertain if uncertain else OSError):
        compact(prepared)
    document = json.loads(operations.store.path.read_bytes())
    assert bool(document["terminal_recovery"]["operations"]) is uncertain
    assert bool(document["reviews"]) is not uncertain
    if not uncertain:
        assert operations.store.path.read_bytes() == before
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    compact(prepared)
    assert recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())["actions"][0]["state"] == "unknown"


@pytest.mark.parametrize("failure", [None, "marker", "body", "actor", "duplicate", "incomplete", "binding", "expired"])
def test_actual_protected_readback_uses_original_digest_marker_actor_and_no_write(
    workspace: tuple, failure: str | None,
) -> None:
    prepared = prepare(workspace)
    _, recovery, intent, admitted, _, policies, backend = prepared
    thread = matching_thread(intent, admitted)
    if failure == "marker":
        thread["properties"]["agent-pull-requests.action"]["$value"] = "copied-unrelated-marker"
    elif failure == "body":
        thread["comments"][0]["content"] = "Same actor, different text"
    elif failure == "actor":
        thread["comments"][0]["author"]["id"] = "other-actor"
    backend.threads = [thread, copy.deepcopy(thread)] if failure == "duplicate" else [thread]
    if failure == "incomplete":
        backend.iterations = []
    elif failure == "binding":
        backend.observer = object()
    elif failure == "expired":
        policies[intent["operation_id"]]["deadline_at"] = 1
    compact(prepared)
    calls = len(backend.calls)
    result = reconcile(prepared)
    expected = "confirmed" if failure is None else "unknown"
    retained = recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())
    assert retained["actions"][0]["state"] == expected
    assert retained["actions"][1]["state"] == "retired"
    assert all(call[0] == "GET" for call in backend.calls[calls:])
    assert result["entered_writes"] == 0 and result["effects"] == "none"
    if failure == "expired":
        assert result["status"] == "escalation_required" and len(backend.calls) == calls
    if failure is None:
        receipt = retained["actions"][0]["receipt"]
        assert receipt["proof"]["intent_id"] == admitted["intent_id"]
        assert receipt["platform_ids"]["thread_id"] == 100
        assert receipt["platform_ids"]["comment_id"] == 1
        assert receipt["origin"] == "attributable_readback"
        assert reconcile(prepared)["actions"] == retained["actions"]


@pytest.mark.parametrize("kind", ["reply", "edit_owned_comment", "verdict"])
def test_unsupported_unknown_native_actions_never_confirm_from_current_value(workspace: tuple, kind: str) -> None:
    selected = copy.deepcopy(workspace[3])
    selected["actions"] = [{"id": "unsupported", "kind": kind}]
    if kind == "verdict":
        selected["actions"][0]["verdict"] = "APPROVED"
    else:
        selected["actions"][0].update(body="Current text", thread_id=10)
        if kind == "edit_owned_comment":
            selected["actions"][0].update(comment_id=1, expected_content_id=_digest("Previous"))
    prepared = prepare(workspace, selected=selected)
    compact(prepared)
    assert reconcile(prepared)["actions"][0]["state"] == "unknown"


def test_prepared_guids_and_confirmation_survive_without_source_or_body(
    workspace: tuple, prepared_intent: dict,
) -> None:
    prepared = prepare(workspace, selected=prepared_intent)
    operations, recovery, intent, _, _, _, _ = prepared
    state = json.loads(operations.store.path.read_bytes())["reviews"][0]["actions"][0]
    operations.store.record_review_receipt(
        intent["operation_id"], intent["scope"], state["id"], state["attempt_id"],
        {"origin": "response", "platform_ids": {"thread_id": 100, "comment_id": 1}, "proof": None},
        expires_at=expires(),
    )
    compact(prepared)
    retained = recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())
    original = intent["actions"][0]["preparation"]
    assert retained["actions"][0]["prepared"] == {
        "comment_id": original["identity"]["comment_id"], "write_id": original["identity"]["write_id"],
        "body_id": original["body_id"],
    }
    assert retained["actions"][0]["state"] == "confirmed"
    assert intent["actions"][0]["body"].encode() not in operations.store.path.read_bytes()
    assert "source" not in retained["actions"][0]["prepared"]


def test_original_partial_native_receipt_cannot_be_replaced_by_a_different_matching_thread(workspace: tuple) -> None:
    prepared = prepare(workspace)
    operations, recovery, intent, admitted, _, _, backend = prepared
    before = json.loads(operations.store.path.read_bytes())["reviews"][0]["actions"][0]
    operations.store.record_review_receipt(
        intent["operation_id"], intent["scope"], before["id"], before["attempt_id"],
        {"origin": "response", "platform_ids": {"thread_id": 99}, "proof": None},
        expires_at=expires(), confirm=False,
    )
    backend.threads = [matching_thread(intent, admitted)]
    compact(prepared)
    result = reconcile(prepared)
    assert result["status"] == "unknown"
    state = recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())["actions"][0]
    assert state["receipt"]["platform_ids"] == {"thread_id": 99}
    assert state["attempt_id"] == before["attempt_id"]


@pytest.mark.parametrize("target_thread", [100, 200])
def test_compacted_unknown_native_identity_still_guards_actual_reopened_edit_allocation(
    workspace: tuple, comment_preparer: tuple, target_thread: int,
) -> None:
    from agent_pull_requests.review_selection_store import ReviewSelectionStore
    from agent_pull_requests.review_workspace import _record
    from agent_pull_requests.terminal_retention import bind_generation

    prepared = prepare(workspace)
    operations, _, intent, _, _, _, backend = prepared
    state = json.loads(operations.store.path.read_bytes())["reviews"][0]["actions"][0]
    operations.store.record_review_receipt(
        intent["operation_id"], intent["scope"], state["id"], state["attempt_id"],
        {"origin": "response", "platform_ids": {"thread_id": target_thread}, "proof": None},
        expires_at=expires(), confirm=False,
    )
    compact(prepared)
    thread = {
        "id": 100, "status": 1, "comments": [{
            "id": 1, "parentCommentId": 0, "commentType": 1, "content": "Current legacy text.",
            "author": {"id": intent["scope"]["principal_id"]},
        }],
    }
    backend.threads = [thread]
    current = operations.cache.lookup(intent["scope"])["generation"]
    current["sections"]["discussions"]["data"] = [_record("discussions", thread)]
    observation, reopened = terminal(operations, current, state="active", second=30)
    operations.store._review_transaction(
        lambda document: bind_generation(document, reopened, observation["cursor"]), expires_at=expires(),
    )
    operations.store.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=expires())
    selections = ReviewSelectionStore(operations.store)
    selections.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires())
    chosen = copy.deepcopy(intent)
    chosen.update(operation_id="new-reopened-edit", generation_id=reopened["id"])
    chosen["actions"] = [{
        "id": "edit", "kind": "edit_owned_comment", "body": "Selected new edit.", "thread_id": 100,
        "comment_id": 1, "expected_content_id": _digest("Current legacy text."),
    }]
    attachment = copy.deepcopy(workspace[5])
    attachment["generation_id"] = reopened["id"]
    compose, composed = comment_preparer
    before = operations.store.path.read_bytes()

    def allocate() -> dict:
        return selections.allocate(
            chosen, compose=compose, expires_at=expires(), generation=reopened, source_attachment=attachment,
        )

    if target_thread == 100:
        with pytest.raises(ValueError, match="unresolved native effect"):
            allocate()
        assert operations.store.path.read_bytes() == before and not composed["results"]
    else:
        assert allocate()["status"] == "prepared"


@pytest.mark.parametrize("boundary", ["read", "record", "validation", "commit-validation"])
def test_original_deadline_crossed_during_protected_readback_escalates_immediately(
    workspace: tuple, monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    prepared = prepare(workspace)
    operations, recovery, intent, admitted, _, policies, backend = prepared
    backend.threads = [matching_thread(intent, admitted)]
    compact(prepared)
    before = operations.store.path.read_bytes()
    deadline = policies[intent["operation_id"]]["deadline_at"]
    if boundary == "read":
        request = backend.request

        def delayed(*args, **kwargs):
            result = request(*args, **kwargs)
            if "/threads?" in args[1]:
                operations.store.clock = lambda: deadline
            return result

        monkeypatch.setattr(backend, "request", delayed)
    elif boundary == "record":
        record = recovery.record

        def delayed_record(*args, **kwargs):
            operations.store.clock = lambda: deadline
            return record(*args, **kwargs)

        monkeypatch.setattr(recovery, "record", delayed_record)
    else:
        validate = operations.store._validate_owner
        confirmations = 0

        def delayed_validation(document: dict) -> None:
            nonlocal confirmations
            validate(document)
            if document["terminal_recovery"]["operations"][0]["actions"][0]["state"] == "confirmed":
                confirmations += 1
                if confirmations == (1 if boundary == "validation" else 2):
                    operations.store.clock = lambda: deadline

        monkeypatch.setattr(operations.store, "_validate_owner", delayed_validation)
    result = reconcile(prepared)
    assert result["status"] == "escalation_required"
    assert result["actions"][0]["state"] == "unknown" and result["actions"][0]["receipt"] is None
    assert operations.store.path.read_bytes() == before


def test_reopen_preserves_historical_recovery_but_cannot_resume_old_compactor(workspace: tuple) -> None:
    prepared = prepare(workspace)
    operations, recovery, intent, admitted, _, _, backend = prepared
    backend.threads = [matching_thread(intent, admitted)]
    compact(prepared)
    current = operations.cache.lookup(intent["scope"])["generation"]
    terminal(operations, current, state="active", second=30)
    assert reconcile(prepared)["actions"][0]["state"] == "confirmed"
    with pytest.raises(TerminalAdmissionClosed):
        operations.store.admit_review(intent, expires_at=expires())
    with pytest.raises(TerminalAdmissionClosed):
        compact(prepared)
    assert recovery.inspect(intent["scope"], intent["operation_id"], expires_at=expires())["actions"][0]["state"] == "confirmed"
