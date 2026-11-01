"""Release and preparation replay preserve retirement and resolved routing."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from agent_dispatch.ordinary_native_bindings import is_native_retired
from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_pool_placement import (
    require_pool_recovery_retirement, reservation_accountable,
)
from agent_dispatch.spawn_factories import SpawnPreparationRetained
from agent_dispatch.workspace_binding import record_prepared
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, task

q = placement_controls.q


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("disposition", ["failed", "settled"])
@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("identities", [False, True])
def test_qualified_release_replay_does_not_recount_retired_capacity(
    q: TaskQueue, historical: bool, disposition: str, finished: bool, identities: bool,
) -> None:
    publish(q, pool())
    t = task(q, exclusive_key="shared")
    original, _ = place(q, t)
    record_native(q, original)
    if not finished:
        q.record_spawn(original.key, session_handle="native", startup_complete=True, startup_revision=1)
    if historical:
        q.fail_spawn(original.key)
    options = {"session_handle": "native", "worktree": "worktree"} if identities else {}
    q.request_spawn_release(
        original.key, disposition=disposition,
        finished_startup_revision=1 if finished else None, **options,
    )
    retired = q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    assignment = q.task_pool_assignment(t.id)
    task_before = q.get(t.id)
    for _ in range(2):
        replay = q.request_spawn_release(
            original.key, disposition=disposition, finished_startup_revision=1, **options,
        )
        assert replay.state == retired.state
        assert replay.release_requested and not replay.native_release_pending
        assert replay.conversation_retired == retired.conversation_retired
        assert replay.session_handle == retired.session_handle
        with q._connect() as conn:
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (original.key,),
            ).fetchone()
            assert is_native_retired(row) and not reservation_accountable(conn, row)
            require_pool_recovery_retirement(conn, q.get(t.id))
    assert q.get(t.id) == task_before
    assert q.task_pool_assignment(t.id) == assignment
    assert place(q, task(q, exclusive_key="shared"))[1]
    with pytest.raises(TaskError, match="current reservation"):
        q.start(t.id, "host-a/worktree", owner_session_id="native",
                expected_generation=q.get(t.id).generation)


@pytest.mark.parametrize("effect", ["worktree", "native"])
def test_late_identity_after_handleless_retirement_remains_accountable(
    q: TaskQueue, effect: str,
) -> None:
    publish(q, pool())
    t = task(q)
    original, _ = place(q, t)
    q.request_spawn_release(original.key, finished_startup_revision=1)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    options = {"worktree": "late-worktree"}
    if effect == "native":
        options["session_handle"] = "late-native"
    late = q.request_spawn_release(original.key, finished_startup_revision=1, **options)
    assert late.native_release_pending and late.worktree == "late-worktree"
    assert late.session_handle == options.get("session_handle")
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    q.retire_spawn(original.key, exact_absence=True)
    assert place(q, task(q))[1]


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_qualified_release_replay_keeps_own_transport_capacity(q: TaskQueue, state: str) -> None:
    publish(q, pool())
    t = task(q, source="test-producer")
    original, _ = place(q, t)
    record_native(q, original)
    q.request_spawn_release(original.key, finished_startup_revision=1)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO execution_send_custody"
            " (scope_key, send_id, repo, source, producer_generation, task_id, reservation_key,"
            " task_generation, kind, identity, state, detail, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scope", "send", t.repo, t.source, 1, t.id, original.key,
             q.get(t.id).generation, "launch", "{}", state, "unresolved", 100, 100),
        )
    replay = q.request_spawn_release(original.key, session_handle="native", worktree="worktree")
    assert not replay.native_release_pending
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q))
    with q._connect() as conn:
        conn.execute("UPDATE execution_send_custody SET state='not_sent' WHERE send_id='send'")
    assert place(q, task(q))[1]


@pytest.mark.parametrize("selector", ["excluded", "required", "allowed"])
def test_retained_retry_checks_resolved_workspace_before_claim(q: TaskQueue, selector: str) -> None:
    publish(q, pool(capacity=2), pool("alternative", priority=-1))
    t = task(q)
    original, _ = place(q, t)
    record_native(q, original)
    q.start(t.id, "host-a/worktree", owner_session_id="native",
            expected_generation=q.get(t.id).generation)
    q.fail_spawn(original.key)
    q.yield_task(
        t.id, "host-a/worktree",
        exclude="worktree:worktree" if selector == "excluded" else None,
    )
    if selector == "required":
        with q._connect() as conn:
            conn.execute("UPDATE tasks SET requires='[\"worktree:worktree\"]' WHERE id=?", (t.id,))
    q.request_spawn_release(original.key, finished_startup_revision=1)
    q.retire_spawn(original.key, exact_absence=True, conclusion_state="skipped")
    before = q.get(t.id)
    assignment = q.task_pool_assignment(t.id)
    reservations = q.list_reservations(task_id=t.id)
    if selector == "excluded":
        with pytest.raises(TaskError, match="retained pool assignment.*routing"):
            place(q, before)
        assert q.get(t.id) == before and before.status == Status.QUEUED
        assert q.task_pool_assignment(t.id) == assignment
        assert q.list_reservations(task_id=t.id) == reservations
    else:
        retry, reserved = place(q, before)
        assert reserved and retry.worktree == "worktree" and retry.worktree_ownership == "reused"
        assert q.get(t.id).status == Status.CLAIMED
        assert q.task_pool_assignment(t.id).registration_id == assignment.registration_id


@pytest.mark.parametrize("ownership", ["created", "targeted", "reused"])
@pytest.mark.parametrize("recorded_host", ["HOST-A", " host-a "])
@pytest.mark.parametrize("cached", [False, True])
def test_preparation_replay_normalizes_host_without_rewriting_identity(
    q: TaskQueue, ownership: str, recorded_host: str, cached: bool,
) -> None:
    publish(q, pool())
    t = task(q, target_worktree="wanted")
    original, _ = place(q, t)
    bound = q.record_spawn_worktree(
        original.key, "wanted", ownership=ownership, execution_machine=recorded_host,
        creating_host=recorded_host if ownership == "created" else None,
        allocation_project="example", worktree_path="/workspace", driver="cli",
    )
    replay = q.record_spawn_worktree(
        original.key, "wanted", ownership=ownership, execution_machine="host-a",
        creating_host=recorded_host if ownership == "created" else None,
        allocation_project="example", worktree_path="/workspace", driver="cli",
    )
    assert replay.execution_machine == recorded_host

    class Client:
        def record_spawn_worktree(self, key: str, worktree: str, **kw: object) -> dict:
            return asdict(q.record_spawn_worktree(key, worktree, **kw))

    prepared = {
        "worktree": "wanted", "ownership": ownership, "project": "example", "path": "/workspace",
    }
    snapshot = asdict(bound if cached else original)
    record_prepared(Client(), snapshot, prepared, driver="cli", creating_host="host-a")
    before = q.get_reservation(original.key)
    assert before.execution_machine == recorded_host
    with pytest.raises(TaskError, match="execution machine cannot change"):
        q.record_spawn_worktree(
            original.key, "wanted", ownership=ownership, execution_machine="host-b",
            allocation_project="example", worktree_path="/workspace", driver="cli",
        )
    assert q.get_reservation(original.key) == before
    q.record_spawn(original.key, session_handle="native", worktree="wanted")
    assert q.start(t.id, "host-a/wanted", owner_session_id="native",
                   expected_generation=q.get(t.id).generation).status == Status.STARTED


@pytest.mark.parametrize("field,value", [
    ("execution_machine", "host-b"), ("allocation_project", "other"),
    ("worktree_path", "/different"),
])
def test_preparation_response_still_rejects_different_identity(
    q: TaskQueue, field: str, value: str,
) -> None:
    publish(q, pool())
    original, _ = place(q, task(q, target_worktree="wanted"))

    class Client:
        def record_spawn_worktree(self, key: str, worktree: str, **kw: object) -> dict:
            recorded = asdict(q.record_spawn_worktree(key, worktree, **kw))
            return {**recorded, field: value}

    with pytest.raises(SpawnPreparationRetained, match="did not retain exact workspace"):
        record_prepared(
            Client(), asdict(original),
            {"worktree": "wanted", "ownership": "targeted", "project": "example", "path": "/workspace"},
            driver="cli", creating_host="host-a",
        )
