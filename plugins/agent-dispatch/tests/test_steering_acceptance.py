"""Atomic card/owner acceptance and request-specific steering receipts."""
from __future__ import annotations

from dataclasses import asdict, replace
import sqlite3
import threading

import pytest

from agent_dispatch.queue import TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _card(q, *, session="session-one"):
    task = q.create("Answer a structured card")
    q.claim_one("host/worker", task_id=task.id)
    q.start(task.id, "host/worker", owner_session_id=session)
    return q.set_card(
        task.id, "host/worker",
        card={"title": "A decision", "request_input": [{"name": "answer", "type": "text"}]},
        now=1000,
    )


def _snapshot(q, task_id):
    return (
        asdict(q.get(task_id)), q.steer_log(task_id),
        [asdict(wake) for wake in q.list_wakes(task_id)], q.events(task_id),
    )


def test_same_bytes_and_timestamp_card_replacement_invalidates_the_original_revision(q):
    original = _card(q)
    body = {key: value for key, value in original.card.items() if key not in {"revision", "ts"}}
    q.resume(original.id, original.owner)
    replacement = q.set_card(original.id, original.owner, card=body, now=1000)
    assert replacement.card["ts"] == original.card["ts"]
    assert replacement.steering_revision != original.steering_revision
    q.save_card_draft(original.id, fields={"answer": "unsubmitted"})
    before = _snapshot(q, original.id)
    with pytest.raises(TaskError, match="card or owner changed"):
        q.submit_steer(
            original.id, fields={"answer": "stale"}, wake_requested=True,
            expected_status=original.status, expected_revision=original.steering_revision,
            idempotency_key="stale-card",
        )
    assert _snapshot(q, original.id) == before


@pytest.mark.parametrize("change", [
    {"owner": "host/replacement"}, {"owner_session_id": "replacement-session"},
    {"generation": 2}, {"attempts": 2}, {"claimed_at": 123},
    {"status": "started"}, {"awaiting_steer": False},
])
def test_revision_includes_every_owner_and_answer_fence(q, change):
    original = _card(q)
    assert replace(original, **change).steering_revision != original.steering_revision


def test_heartbeat_and_operator_draft_do_not_stale_an_unchanged_card(q):
    original = _card(q)
    original = q.resume(original.id, original.owner)
    q.heartbeat(original.id, original.owner, now=1001)
    q.save_card_draft(original.id, fields={"answer": "draft"})
    assert q.get(original.id).steering_revision == original.steering_revision
    q.submit_steer(
        original.id, fields={"answer": "accepted"}, idempotency_key="answer-one",
        expected_revision=original.steering_revision,
    )
    assert q.get(original.id).card_draft is None
    assert q.steer_receipt(original.id, "answer-one")["acceptance"]["steering_revision"] == original.steering_revision


def test_owner_session_change_refuses_inside_the_acceptance_transaction(q):
    original = _card(q, session=None)
    original = q.resume(original.id, original.owner)
    q.bind_owner_session(original.id, original.owner, "new-session")
    before = _snapshot(q, original.id)
    with pytest.raises(TaskError, match="card or owner changed"):
        q.submit_steer(
            original.id, fields={"answer": "stale"}, wake_requested=True,
            expected_revision=original.steering_revision, idempotency_key="stale-owner",
        )
    assert _snapshot(q, original.id) == before


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_submissions_cannot_double_accept_an_unanswered_card(q, same_request):
    original = _card(q)
    ready = threading.Barrier(2)
    outcomes = []
    notifications = []
    q.set_wake_notifier(lambda: notifications.append("wake"))

    def submit(index):
        ready.wait(timeout=3)
        try:
            outcomes.append(q.submit_steer(
                original.id, fields={"answer": "accepted"}, wake_requested=True,
                expected_revision=original.steering_revision,
                idempotency_key="shared-key" if same_request else f"request-{index}",
            ))
        except TaskError as error:
            outcomes.append(error)

    threads = [threading.Thread(target=submit, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert sum(isinstance(result, TaskError) for result in outcomes) == (0 if same_request else 1)
    assert len(q.steer_log(original.id)) == len(q.list_wakes(original.id)) == 1
    assert notifications == ["wake"]
    assert sum(str(event["note"]).startswith("steer submitted") for event in q.events(original.id)) == 1


@pytest.mark.parametrize("change", [
    {"fields": {"answer": "changed"}}, {"sender": "different-operator"},
    {"wake_requested": False}, {"wake_message": "different wake"},
    {"expected_status": "started"}, {"expected_revision": "different-revision"},
])
def test_a_retry_key_never_acknowledges_a_different_request(q, change):
    original = _card(q)
    request = dict(
        fields={"answer": "accepted"}, sender="operator", wake_requested=True,
        wake_message="resume", expected_status=original.status,
        expected_revision=original.steering_revision, idempotency_key="answer-one",
    )
    q.submit_steer(original.id, **request, now=1001)
    before = _snapshot(q, original.id)
    with pytest.raises(TaskError, match="request key conflicts"):
        q.submit_steer(original.id, **{**request, **change}, now=1002)
    assert _snapshot(q, original.id) == before


def test_exact_retry_survives_consumption_new_card_and_later_wake(q):
    original = _card(q)
    request = dict(
        fields={"answer": "accepted", "extra": ["one", "two"]}, sender="operator",
        wake_requested=True, wake_message="resume", expected_status=original.status,
        expected_revision=original.steering_revision, idempotency_key="answer-one",
    )
    q.submit_steer(original.id, **request, now=1001)
    receipt = q.steer_receipt(original.id, "answer-one")
    accepted = receipt["acceptance"]
    assert receipt["consumed_at"] is None and receipt["wake_status"] == "pending"
    q.take_steer(original.id, original.owner, now=1002)
    replacement = q.set_card(original.id, original.owner, card={"title": "Next card"}, now=1003)
    q.submit_steer(
        original.id, fields={"answer": "next"}, wake_requested=True,
        expected_revision=replacement.steering_revision, idempotency_key="answer-two", now=1004,
    )
    before = _snapshot(q, original.id)
    q.submit_steer(
        original.id, **{**request, "fields": {"extra": ["one", "two"], "answer": "accepted"}},
        now=1005,
    )
    assert _snapshot(q, original.id) == before
    receipt = q.steer_receipt(original.id, "answer-one")
    assert receipt["acceptance"] == accepted and receipt["consumed_at"] == 1002
    assert receipt["wake"]["id"] == accepted["wake_operation_id"]
    assert receipt["wake"]["id"] != q.get(original.id).wake_operation_id
    q.claim_due_wake(now=1006)
    assert q.steer_receipt(original.id, "answer-one")["wake_status"] == "stale"


def test_retry_keeps_original_no_session_receipt_after_an_owner_session_is_bound(q):
    original = _card(q, session=None)
    request = dict(
        fields={"answer": "accepted"}, wake_requested=True,
        expected_revision=original.steering_revision, idempotency_key="answer-one",
    )
    q.submit_steer(original.id, **request)
    accepted = q.steer_receipt(original.id, "answer-one")
    q.bind_owner_session(original.id, original.owner, "new-owner-session")
    before = _snapshot(q, original.id)
    assert q.submit_steer(original.id, **request).owner_session_id == "new-owner-session"
    assert _snapshot(q, original.id) == before
    assert q.steer_receipt(original.id, "answer-one") == accepted
    assert accepted["acceptance"]["owner_session_id"] is None
    assert accepted["wake"] is None and accepted["wake_status"] == "no_owner_session"


def test_unverifiable_legacy_key_is_a_conflict_not_a_fabricated_ack(q):
    task = _card(q)
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO task_steer (task_id, ts, fields, idempotency_key) VALUES (?, ?, ?, ?)",
            (task.id, 999, '{"answer":"legacy"}', "legacy-key"),
        )
    before = _snapshot(q, task.id)
    with pytest.raises(TaskError, match="unverifiable legacy request"):
        q.submit_steer(task.id, fields={"answer": "legacy"}, idempotency_key="legacy-key")
    with pytest.raises(TaskError, match="no verifiable acceptance receipt"):
        q.steer_receipt(task.id, "legacy-key")
    assert _snapshot(q, task.id) == before


def test_existing_steer_table_migrates_without_inventing_acceptance(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE task_steer (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " task_id TEXT NOT NULL, ts REAL NOT NULL, fields TEXT NOT NULL DEFAULT '{}',"
            " sender TEXT, taken INTEGER NOT NULL DEFAULT 0, taken_at REAL, idempotency_key TEXT)"
        )
        conn.execute(
            "INSERT INTO task_steer (task_id, ts, fields, idempotency_key)"
            " VALUES ('old-task', 1, '{}', 'old-key')"
        )
    queue = TaskQueue(path)
    TaskQueue(path)
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM task_steer WHERE idempotency_key = 'old-key'").fetchone()
    assert row["fields"] == "{}"
    assert row["request_fingerprint"] is row["acceptance"] is row["wake_operation_id"] is None
