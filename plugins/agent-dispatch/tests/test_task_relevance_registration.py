"""Registered-source decisions share the existing ownership transaction."""

from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from agent_dispatch.queue import Status, TaskError
from agent_dispatch.queue_relevance_registration import RelevanceRegistration
from agent_dispatch.registrations import RegistrationRecord
from tests._helpers import RepoDefaultingQueue as TaskQueue

SPEC = {
    "id": "review-source", "command": ["source-reader", "tick"],
    "interval_seconds": 60, "task_output": "json",
}


@pytest.fixture
def q(tmp_path) -> TaskQueue:
    return TaskQueue(tmp_path / "tasks.db")


def emitter(q: TaskQueue, **changes) -> RegistrationRecord:
    return q.register_registration(
        "emitter", {**SPEC, **changes}, reg_id="source-a", machine="host-a",
    )


def emitted(q: TaskQueue, **changes):
    return q.create(
        "Review backing object", **{
            "source": "emitter", "origin_ref": SPEC["id"], **changes,
        },
    )


def selector(q: TaskQueue) -> RelevanceRegistration:
    return RelevanceRegistration("source-a", "host-a", "default", q.pool_registration_revision())


def report(q: TaskQueue, task_id: str, **changes) -> dict:
    snapshot = q.relevance_snapshot(task_id, registration=selector(q))
    return {
        "request_id": "closed-object", "scope": selector(q),
        "expected_generation": snapshot["generation"], "expected_revision": snapshot["revision"],
        "verdict": "obsolete", "reason": "Authoritative object is closed",
        "observation_ref": "provider:revision-2", "reporter": "registration:source-a",
        **changes,
    }


def test_registered_source_abandons_once_and_recovers_after_registration_removal(q) -> None:
    emitter(q)
    t = emitted(q)
    values = report(q, t.id)
    accepted = q.reconcile_task_relevance(t.id, **values)
    assert accepted["decision"] == "abandoned"
    q.remove_registration("source-a")
    assert q.reconcile_task_relevance(t.id, **values) == accepted
    assert q.relevance_receipt(
        t.id, values["request_id"], registration_id="source-a",
    )["acceptance"] == accepted
    with pytest.raises(TaskError, match="different registration"):
        q.relevance_receipt(t.id, values["request_id"], registration_id="source-b")
    assert q.steer_log(t.id) == q.list_wakes(t.id) == []


@pytest.mark.parametrize("change", ["spec", "pause", "remove", "remove_recreate"])
def test_registration_movement_invalidates_observation_before_effects(q, change) -> None:
    emitter(q)
    t = emitted(q)
    values = report(q, t.id)
    if change == "spec":
        emitter(q, command=["different-reader", "tick"])
    elif change == "pause":
        q.set_registration_status("source-a", "paused")
    else:
        q.remove_registration("source-a")
        if change == "remove_recreate":
            emitter(q)
    with pytest.raises(TaskError, match="registration revision changed"):
        q.reconcile_task_relevance(t.id, **values)
    assert q.get(t.id).status == Status.QUEUED
    assert q.relevance_receipt(t.id, "closed-object") is None


def test_unchanged_registration_upsert_does_not_invalidate_source_check(q) -> None:
    emitter(q)
    t = emitted(q)
    values = report(q, t.id)
    emitter(q)
    assert q.reconcile_task_relevance(t.id, **values)["decision"] == "abandoned"


@pytest.mark.parametrize("changes", [
    {"registration_id": "foreign"}, {"registration_id": "declared:plugin:source"},
    {"machine": "host-b"}, {"env": "other"},
])
def test_unknown_foreign_or_unqualified_declared_scope_never_mutates(q, changes) -> None:
    emitter(q)
    t = emitted(q)
    values = report(q, t.id, scope=replace(selector(q), **changes))
    with pytest.raises(TaskError):
        q.reconcile_task_relevance(t.id, **values)
    assert q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("changes", [
    {"source": "manual"}, {"origin_ref": "foreign-source"},
])
def test_registration_cannot_assert_another_task_source(q, changes) -> None:
    emitter(q)
    t = emitted(q, **changes)
    with pytest.raises(TaskError, match="does not match"):
        q.relevance_snapshot(t.id, registration=selector(q))
    assert q.get(t.id).status == Status.QUEUED


def test_ambiguous_emitter_provenance_is_not_guessed(q) -> None:
    emitter(q)
    q.register_registration("emitter", SPEC, reg_id="duplicate", machine="host-b")
    t = emitted(q)
    with pytest.raises(TaskError, match="ambiguous"):
        q.relevance_snapshot(t.id, registration=selector(q))


@pytest.mark.parametrize("override", ["source-a", "logical:local:review-source"])
def test_registered_decision_preserves_concrete_and_logical_overrides(q, override) -> None:
    emitter(q)
    t = emitted(q)
    values = report(q, t.id)
    with pytest.raises(TaskError, match="operator override"):
        q.reconcile_task_relevance(t.id, **values, disabled_ids=frozenset({override}))
    assert q.get(t.id).status == Status.QUEUED


def test_registration_resolution_is_inside_the_ownership_write_lock(q, monkeypatch) -> None:
    from agent_dispatch import queue_relevance

    emitter(q)
    t = emitted(q)
    values = report(q, t.id)
    original = queue_relevance.resolve_registration

    def resolve(conn, *args, **kwargs):
        with sqlite3.connect(q.db_path, timeout=0) as writer:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.execute("BEGIN IMMEDIATE")
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(queue_relevance, "resolve_registration", resolve)
    assert q.reconcile_task_relevance(t.id, **values)["decision"] == "abandoned"


def test_registered_closeout_reuses_original_agent_not_a_new_assignment(q) -> None:
    emitter(q)
    t = emitted(q)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    result = q.reconcile_task_relevance(t.id, **report(q, t.id))
    assert result["decision"] == "closeout_pending"
    assert q.get(t.id).status == Status.STARTED
    [wake] = q.list_wakes(t.id)
    assert (wake.owner, wake.owner_session_id) == ("host/worker", "original-native")
    assert q.claim_one("host/replacement", task_id=t.id) is None
