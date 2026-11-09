"""Atomic terminal fence and original delivery custody in isolated owner files."""

from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from single_instance_lease import OperationLockedError

from test_review_owner_store import selected
from test_review_workspace import evidence as evidence
from test_workspace_conversation import generation

from agent_pull_requests.attention import Cursor, Interest
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.review_feedback import FeedbackInputs, ReviewFeedback
from agent_pull_requests.review_selection_store import ReviewSelectionStore
from agent_pull_requests.terminal_retention import (
    TerminalAdmissionClosed, TerminalRetentionStore, bind_generation,
)
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_replay import ConsumerScope, ObservationTarget
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests.watch_storage import StateCommitUncertain

CONSUMER = ConsumerScope("consumer-a", "account-a", "example/project")


@pytest.fixture
def owner(tmp_path):
    store = ReplayStore(tmp_path.resolve(), "owner", clock=lambda: 1, terminal_retention=True)
    store.provision(conversations=True)
    TerminalRetentionStore(store).adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    return store


def register(owner, request_id="request-a", *, deadline=90, until=("merged",)):
    return owner.execute(CONSUMER, "register", {
        "request_id": request_id, "issued_at": 1, "number": 42,
        "until": until, "deadline_at": deadline,
    }, expires_at=100)


def target(*receipts):
    return ObservationTarget(
        CONSUMER.account, CONSUMER.repo, 42,
        frozenset(receipt["registration_id"] for receipt in receipts),
    )


def observe(owner, evidence, *, second=0, captured=None):
    return ConversationStore(owner).observe_workspace(
        generation(evidence, second), expires_at=100,
        terminal_target=captured if captured is not None else target(),
    )


def admit(owner, evidence):
    value = generation(evidence)
    position = observe(owner, evidence)["cursor"]
    owner._review_transaction(
        lambda document: bind_generation(document, value, position), expires_at=100,
    )
    intent = selected()
    intent.update(scope=value["scope"], generation_id=value["id"])
    owner.admit_review(intent, expires_at=100)
    return intent, value


@pytest.mark.parametrize("uncertain", [False, True])
def test_terminal_replace_is_one_tombstone_fence_and_original_notification(
    owner, evidence, monkeypatch, uncertain,
):
    from agent_pull_requests import watch_replay_store as storage

    receipt = register(owner)
    observe(owner, evidence)
    original = owner.path.read_bytes()
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    write = storage._atomic_write_json

    def interrupt(path, state):
        if uncertain:
            write(path, state)
            raise StateCommitUncertain("synthetic post-replace interruption")
        raise OSError("synthetic pre-replace interruption")

    monkeypatch.setattr(storage, "_atomic_write_json", interrupt)
    with pytest.raises(StateCommitUncertain if uncertain else OSError):
        observe(owner, evidence, second=2, captured=target(receipt))
    after = json.loads(owner.path.read_bytes())
    assert bool(after["terminal_retention"]["records"]) is uncertain
    assert (after["conversations"]["records"][0]["current"]["state"] == "merged") is uncertain
    if not uncertain:
        assert owner.path.read_bytes() == original
    monkeypatch.setattr(storage, "_atomic_write_json", write)
    restarted = ReplayStore(owner.root, owner.namespace, clock=lambda: 1, terminal_retention=True)
    first = observe(restarted, evidence, second=2, captured=target(receipt))
    pending = restarted.execute(CONSUMER, "pending", {}, expires_at=100)
    repeated = observe(restarted, evidence, second=2, captured=target(receipt))
    assert first["retirement"] == repeated["retirement"]
    assert restarted.execute(CONSUMER, "pending", {}, expires_at=100) == pending
    event = pending["events"][0]
    assert event["registration_id"] == receipt["registration_id"]
    assert first["retirement"]["deliveries"][0]["event_id"] == event["event_id"]
    assert "Review this change" not in json.dumps(event)
    assert "Question" not in json.dumps(event)
    assert first["retirement"]["drain"] == "pending"


def test_frozen_pending_payload_other_registrations_and_exact_ack_survive(owner, evidence):
    frozen = register(owner, "frozen", until=("review_changed",))
    fresh = register(owner, "fresh")
    unrelated = owner.execute(
        ConsumerScope("other", CONSUMER.account, CONSUMER.repo), "register",
        {"request_id": "unrelated", "issued_at": 1, "number": 43, "until": ("merged",)},
        expires_at=100,
    )
    frozen_target = target(frozen)
    owner.observe(frozen_target, PRSnapshot(review_decision="REVIEW_REQUIRED"), expires_at=100)
    owner.observe(
        frozen_target, PRSnapshot(review_decision="APPROVED", native_snapshot={
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": CONSUMER.repo, "pr": 42, "provider": "azure-devops",
            "pr_state": "open", "merged": False, "mergeable": None,
            "head_sha": "a" * 40, "base_ref": "main", "checks_state": "",
            "title": "frozen body", "draft": False,
        }),
        expires_at=100,
    )
    old_event = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"][0]
    before_unrelated = next(
        row for row in json.loads(owner.path.read_bytes())["replay"]["records"]
        if row["registration_id"] == unrelated["registration_id"]
    )
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    result = observe(owner, evidence, captured=target(frozen, fresh))
    events = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"]
    assert next(event for event in events if event["event_id"] == old_event["event_id"]) == old_event
    deliveries = result["retirement"]["deliveries"]
    assert next(row for row in deliveries if row["registration_id"] == frozen["registration_id"])[
        "frozen_before_terminal"
    ]
    assert next(
        row for row in json.loads(owner.path.read_bytes())["replay"]["records"]
        if row["registration_id"] == unrelated["registration_id"]
    ) == before_unrelated
    owner.execute(CONSUMER, "acknowledge", {
        "request_id": "frozen", "issued_at": 1,
        "registration_id": frozen["registration_id"], "event_id": old_event["event_id"],
    }, expires_at=100)
    assert len(owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"]) == 1


def test_terminal_fences_selection_attempt_and_capture_but_keeps_unknown_custody(owner, evidence):
    intent, value = admit(owner, evidence)
    attempt = owner.begin_review_action(
        intent["operation_id"], intent["scope"], "comment-a", expires_at=100,
    )
    reviews = copy.deepcopy(json.loads(owner.path.read_bytes())["reviews"])
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    result = observe(owner, evidence, second=2)
    assert result["retirement"]["operations"] == [intent["operation_id"]]
    with pytest.raises(TerminalAdmissionClosed):
        owner.admit_review(intent, expires_at=100)
    with pytest.raises(TerminalAdmissionClosed):
        owner.begin_review_action(intent["operation_id"], intent["scope"], "verdict-a", expires_at=100)
    with pytest.raises(TerminalAdmissionClosed):
        ReviewSelectionStore(owner).allocate(intent, compose=lambda *a, **k: None, expires_at=100)
    feedback = ReviewFeedback(
        owner, value, FeedbackInputs(value["id"], (Interest("maintainer", True, "consumer-a"),), 60),
        expires_at=100, checkpoint=lambda: None,
    )
    with pytest.raises(TerminalAdmissionClosed):
        feedback.prepare(reviews[0])
    assert json.loads(owner.path.read_bytes())["reviews"] == reviews
    assert reviews[0]["actions"][0]["attempt_id"] == attempt["action"]["attempt_id"]
    assert reviews[0]["actions"][0]["state"] == "unknown"
    evidence["sections"]["pull_request"]["data"]["status"] = "active"
    assert observe(owner, evidence, second=4)["cursor"] == {"epoch": 2, "sequence": 1}
    with pytest.raises(TerminalAdmissionClosed, match="recovery-only"):
        owner.begin_review_action(intent["operation_id"], intent["scope"], "verdict-a", expires_at=100)
    with pytest.raises(TerminalAdmissionClosed):
        TerminalRetentionStore(owner).inspect(intent["scope"], Cursor(1, 2), expires_at=100)


@pytest.mark.parametrize("control", ["reader", "capacity", "deadline", "incomplete", "target"])
def test_no_implicit_adoption_eviction_timeout_policy_or_terminal_inference(owner, evidence, control):
    observe(owner, evidence)
    captured = target()
    if control == "reader":
        prior = ReplayStore(owner.root, owner.namespace, clock=lambda: 1)
        before = owner.path.read_bytes()
        with pytest.raises(ReplayStoreInvalid):
            prior.execute(CONSUMER, "pending", {}, expires_at=100)
        assert owner.path.read_bytes() == before
        with pytest.raises(ReplayStoreInvalid, match="serving contract unavailable"):
            ConversationStore(owner).capabilities(expires_at=100)
        assert owner.path.read_bytes() == before
        return
    if control == "capacity":
        owner._review_transaction(
            lambda document: document["capacities"].update(
                terminal_retention_bytes=len(json.dumps(document["terminal_retention"]).encode()) + 32,
            ), expires_at=100,
        )
    elif control == "deadline":
        captured = target(register(owner, deadline=None))
    elif control == "incomplete":
        evidence["sections"]["reviewers"]["complete"] = False
    elif control == "target":
        captured = ObservationTarget(CONSUMER.account, CONSUMER.repo, 43, frozenset())
    before = owner.path.read_bytes()
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    if control == "incomplete":
        result = observe(owner, evidence, second=2, captured=captured)
        assert "retirement" not in result
        assert not json.loads(owner.path.read_bytes())["terminal_retention"]["records"]
    else:
        with pytest.raises((ReplayStoreInvalid, TerminalAdmissionClosed, ValueError)):
            observe(owner, evidence, second=2, captured=captured)
        assert owner.path.read_bytes() == before


def test_actual_selection_allocation_finishes_before_terminal_fence_and_cannot_admit(owner, evidence):
    intent, _ = admit(owner, evidence)
    owner.adopt_prepared_reviews(lifecycle_checkpoint=lambda: None, expires_at=100)
    selections = ReviewSelectionStore(owner)
    selections.adopt(lifecycle_checkpoint=lambda: None, expires_at=100)
    intent["operation_id"] = "allocation-race"
    intent["actions"] = [intent["actions"][-1]]
    entered, release = threading.Event(), threading.Event()

    def validate(_intent):
        entered.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            selections.allocate, intent, compose=lambda *a, **k: None,
            expires_at=100, validate_intent=validate,
        )
        try:
            assert entered.wait(5)
            evidence["sections"]["pull_request"]["data"]["status"] = "completed"
            with pytest.raises(OperationLockedError):
                observe(owner, evidence, second=2)
        finally:
            release.set()
        retained = future.result(timeout=5)["operation"]
    result = observe(owner, evidence, second=2)
    assert "allocation-race" in result["retirement"]["operations"]
    assert retained["selection"]["stage"] == "prepared"
    with pytest.raises(TerminalAdmissionClosed):
        owner.admit_review(retained["intent"], expires_at=100)


def test_later_replay_poll_uses_body_free_terminal_facts_without_changing_original_events(owner, evidence):
    first = register(owner)
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    observe(owner, evidence, captured=target(first))
    original = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"][0]
    later = register(owner, "later")
    owner.observe(
        target(first, later), PRSnapshot(merged=True, native_snapshot={"body": "must not reappear"}),
        expires_at=100,
    )
    pending = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"]
    assert next(event for event in pending if event["event_id"] == original["event_id"]) == original
    new = next(event for event in pending if event["registration_id"] == later["registration_id"])
    assert new["merged"] is True and "snapshot" not in new
    assert "must not reappear" not in json.dumps(pending)


@pytest.mark.parametrize("started", ["live", "terminal", "uncaptured"])
def test_delayed_native_replay_read_cannot_reintroduce_retired_bodies_after_reopen(
    owner, evidence, started,
):
    observe(owner, evidence)
    if started == "live":
        receipt = register(owner)
        captured = owner.observation_targets(expires_at=100)[0]
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    observe(owner, evidence, second=2)
    if started != "live":
        receipt = register(owner)
        captured = owner.observation_targets(expires_at=100)[0]
    if started == "uncaptured":
        captured = target(receipt)
    evidence["sections"]["pull_request"]["data"]["status"] = "active"
    observe(owner, evidence, second=4)
    refreshed = owner.waiting_target(captured, expires_at=100)
    assert refreshed.retention_epoch == captured.retention_epoch
    before = owner.path.read_bytes()
    result = owner.observe(
        refreshed, PRSnapshot(merged=True, native_snapshot={"body": "retired content"}),
        expires_at=100,
    )
    pending = owner.execute(CONSUMER, "pending", {}, expires_at=100)
    assert "retired content" not in json.dumps(pending)
    if started == "uncaptured":
        assert result["status"] == "stale_observation"
        assert owner.path.read_bytes() == before and not pending["events"]
    else:
        assert len(pending["events"]) == 1 and "snapshot" not in pending["events"][0]


def test_missed_reopen_updates_terminal_facts_without_replacing_frozen_closed_event(owner, evidence):
    closed = register(owner, "closed", until=("closed",))
    merged = register(owner, "merged", until=("merged",))
    captured = target(closed, merged)
    evidence["sections"]["pull_request"]["data"]["status"] = "abandoned"
    first = observe(owner, evidence, captured=captured)
    frozen = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"][0]
    assert frozen["merged"] is False
    evidence["sections"]["pull_request"]["data"]["status"] = "completed"
    latest = observe(owner, evidence, second=2, captured=captured)
    assert latest["cursor"] == {"epoch": 1, "sequence": 2}
    assert latest["retirement"]["cursor"] == first["retirement"]["cursor"]
    assert latest["retirement"]["evidence"]["state"] == "merged"
    pending = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"]
    assert next(event for event in pending if event["event_id"] == frozen["event_id"]) == frozen
    notification = next(event for event in pending if event["registration_id"] == merged["registration_id"])
    assert notification["merged"] is True and "snapshot" not in notification
    assert next(
        row for row in latest["retirement"]["deliveries"]
        if row["registration_id"] == merged["registration_id"]
    )["event_id"] == notification["event_id"]
    assert observe(owner, evidence, second=2, captured=captured)["retirement"] == latest["retirement"]
    later = register(owner, "later")
    owner.observe(
        owner.observation_targets(expires_at=100)[0],
        PRSnapshot(merged=True, native_snapshot={"body": "must stay absent"}), expires_at=100,
    )
    pending = owner.execute(CONSUMER, "pending", {}, expires_at=100)["events"]
    assert next(event for event in pending if event["registration_id"] == later["registration_id"])["merged"]
    assert "must stay absent" not in json.dumps(pending)
