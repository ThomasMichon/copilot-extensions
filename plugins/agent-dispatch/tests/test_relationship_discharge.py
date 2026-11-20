"""A new generation can depend on exact discharges, never on a missing receipt."""

from pathlib import Path
import json
import sqlite3
from typing import Any

import pytest
from agent_dispatch import submission_readiness_observation
from agent_dispatch.assignment_local_ordinary import LocalOrdinaryRelationshipWriter
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.ordinary_capture import OrdinaryCapture
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnReservation, TaskError
from agent_dispatch.queue_task_relationships import require_relationship
from tests._ordinary_relationship_controls import claim
from tests.test_assignment_relationships import GroundOwner
from tests.test_pool_placement import current_task, place, pool, publish, q as q, task


def replace_after_decline(queue: TaskQueue, task_id: str, now: float) -> SpawnReservation:
    scope = queue.pool_catalog_snapshot().scopes[0]
    queue.publish_pool_catalog_scope(
        scope.machine,
        scope.env,
        scope.publisher_id,
        generation=scope.generation,
        expected_revision=scope.revision,
        registration_revision=queue.pool_registration_revision(),
        expected_cohort_revision=1,
        input_revision="second-pool",
        definitions=(pool(), pool("other")),
        now=now,
    )
    replacement, created = queue.reserve_spawn(
        task_id,
        reserved_by="allocator",
        driver="cli",
        now=now,
        pool_catalog_revision=queue.pool_catalog_snapshot().revision,
    )
    assert created and not replacement.task_relationship_required
    return replacement


@pytest.mark.parametrize(
    "history",
    [
        "bound",
        "foreign",
        "prebind",
        "prebind-foreign-ref",
        "prebind-conflicting-identity",
        "prebind-foreign-recovery",
        "proposal",
        "unknown-proposal",
        "late-proposal",
        "detached",
        "proposal-detached",
        "legacy-detached",
        "proposal-submitted-detached",
        "proposal-governed-successor",
        "proposal-governed-foreign",
    ],
)
def test_direct_unassignment_before_decline_survives_removal_of_capture(
    q: TaskQueue,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history: str,
) -> None:
    owner = GroundOwner()
    owner.queue = q
    q._relationship_writer, q._relationship_authority = owner, "local"
    t = q.create(
        "work",
        repo="example",
        require_verification=history == "proposal-submitted-detached"
        or history.startswith("proposal-governed"),
    )
    assert isinstance(t, Task)
    first = AssignmentWorkspace("host-a", "example", "manual", str(tmp_path / "manual"), "first")
    if history.startswith("prebind"):
        owner.lose_reply = "reserve"
        with pytest.raises(TaskError, match="unresolved"):
            claim(q, first, t)
    else:
        claim(q, first, t)
    proposal = None
    if "proposal" in history:
        q.start(t.id, first.worker_id, owner_session_id="manual-native")
        with pytest.raises(TaskError, match="readiness"):
            q.complete(t.id, first.worker_id, result={"prior": True})
        proposal = q.submission_preparation(t.id)
        assert proposal and proposal["blocker"] and proposal.get("advancement") is None
    q.yield_task(t.id, first.worker_id)
    direct = q.reconcile_task_relationship(t.id)
    assert direct["state"] == "closed"
    if history.startswith("prebind"):
        assert "identity" not in direct["receipt"]["producer"]
        assert direct["receipt"]["producer"]["recovery"] == direct["request"]["recovery"]
    publish(q, pool())
    held, _ = place(q, t)
    q.record_spawn_worktree(
        held.key,
        "pool-tree",
        driver="cli",
        ownership="created",
        creating_host="host-a",
        execution_machine="host-a",
        allocation_project="example",
        worktree_path=str(tmp_path / "pool"),
        creation_nonce="second",
    )
    q.record_spawn(
        held.key,
        worktree="pool-tree",
        session_handle="first-native",
        startup_complete=True,
        startup_revision=1,
    )
    binding: dict[str, Any] = dict(
        worker_id="host-a/pool-tree",
        native_id="first-native",
        generation=current_task(q, t.id).generation,
        startup_revision=1,
        reservation_key=held.key,
    )
    q.prepare_pool_decline(t.id, **binding, exclusions=["pool:worker"], note="need another tool")
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    closed = q.close_pool_decline(t.id, **binding)
    assert closed.relationship and closed.relationship["state"] == "closed"
    q.retire_spawn(held.key, exact_absence=True, conclusion_state="skipped")
    reopened = TaskQueue(q.db_path)
    due = closed.receipt["not_before"]
    if history == "prebind-foreign-ref":
        with reopened._connect() as conn:
            conn.execute(
                "UPDATE task_events SET task_relationship=json_set(task_relationship,"
                " '$.receipt.ref','foreign', '$.receipt.producer.closeout.relationship_verification.subject','foreign')"
                " WHERE id=?",
                (direct["request"]["event_id"],),
            )
        with pytest.raises(TaskError, match="original direct assignment receipt"):
            replace_after_decline(reopened, t.id, due)
        return

    def append_proposal(conn: sqlite3.Connection, *, foreign: bool) -> None:
        assert proposal is not None
        copied = dict(proposal)
        if foreign:
            copied["request"] = {**proposal["request"], "assignment_request_id": "foreign"}
        conn.execute(
            "INSERT INTO task_events(task_id,ts,from_status,to_status,worker,note,submission_preparation)"
            " VALUES (?,100,'started','started',?,'unqualified fixture',?)",
            (t.id, first.worker_id, json.dumps(copied)),
        )

    if history == "unknown-proposal":
        with reopened._connect() as conn:
            append_proposal(conn, foreign=True)
        with pytest.raises(TaskError, match="cannot drop a governed assignment"):
            replace_after_decline(reopened, t.id, due)
        return
    replacement = replace_after_decline(reopened, t.id, due)
    if "detached" not in history and not history.startswith("proposal-governed"):
        queued = reopened.exhaust_pool_startup(
            replacement.key,
            generation=current_task(reopened, t.id).generation,
            startup_revision=replacement.startup_revision,
            now=due + 600,
        )
        reopened.request_spawn_release(replacement.key, finished_startup_revision=1, now=due + 601)
        reopened.retire_spawn(
            replacement.key, exact_absence=True, conclusion_state="skipped", now=due + 602
        )
        due = queued.not_before
        retry = replace_after_decline(reopened, t.id, due)
        assert retry.key != replacement.key and retry.allocation_reservation_key == replacement.key
        replacement = retry
    reopened.record_spawn_worktree(
        replacement.key,
        "replacement",
        ownership="created",
        execution_machine="host-a",
        creating_host="host-a",
        driver="cli",
        allocation_project="example",
        worktree_path=str(tmp_path / "replacement"),
    )
    reopened.record_spawn(
        replacement.key,
        worktree="replacement",
        session_handle="other-native",
        startup_complete=True,
        startup_revision=1,
    )
    damaged_field = {
        "foreign": "$.receipt.producer.closeout.relationship_verification.subject",
        "prebind-conflicting-identity": "$.receipt.producer.identity.worker_id",
        "prebind-foreign-recovery": "$.receipt.producer.recovery.worker_id",
    }.get(history)
    if damaged_field is not None:
        # Even after admission, conflicting assignment evidence retains custody.
        with reopened._connect() as conn:
            conn.execute(
                "UPDATE task_events SET task_relationship=json_set(task_relationship,"
                " ?, 'foreign') WHERE id=?",
                (damaged_field, direct["request"]["event_id"]),
            )
            with pytest.raises(TaskError, match="unconfirmed"):
                require_relationship(conn, t.id)
            with pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
                conn.execute(
                    "UPDATE tasks SET status='started',owner='host-a/replacement' WHERE id=?",
                    (t.id,),
                )
        return
    if history.startswith("proposal-governed"):
        from agent_dispatch import pool_decline_observation

        assert proposal is not None
        binding = dict(
            worker_id="host-a/replacement",
            native_id="other-native",
            generation=current_task(reopened, t.id).generation,
            startup_revision=1,
            reservation_key=replacement.key,
        )
        reopened.prepare_pool_decline(
            t.id, **binding, exclusions=["pool:other"], note="need a third pool", now=due + 1
        )
        monkeypatch.setattr(
            pool_decline_observation, "observe", lambda *_: {"operation": "observe-finalized"}
        )
        disposition = reopened.close_pool_decline(t.id, **binding, now=due + 1)
        assert disposition.applied and disposition.relationship is None
        reopened.retire_spawn(replacement.key, exact_absence=True, conclusion_state="skipped")
        reopened._relationship_writer, reopened._relationship_authority = owner, "local"
        owner.queue = reopened
        third_due = disposition.receipt["not_before"]
        scope = reopened.pool_catalog_snapshot().scopes[0]
        reopened.publish_pool_catalog_scope(
            scope.machine,
            scope.env,
            scope.publisher_id,
            generation=scope.generation,
            expected_revision=scope.revision,
            registration_revision=reopened.pool_registration_revision(),
            expected_cohort_revision=1,
            input_revision="third-pool",
            definitions=(pool(), pool("other"), pool("third")),
            now=third_due,
        )
        third, fresh = place(reopened, t, now=third_due)
        assert fresh and third.task_relationship_required
        space = AssignmentWorkspace(
            "host-a", "example", "third", str(tmp_path / "third"), "third-nonce"
        )
        reopened.record_spawn_worktree(
            third.key,
            space.worktree,
            ownership="created",
            driver="cli",
            creating_host="host-a",
            execution_machine=space.machine,
            allocation_project=space.project,
            worktree_path=space.path,
            creation_nonce=space.creation_nonce,
        )
        reopened.record_spawn(
            third.key,
            worktree=space.worktree,
            session_handle="third-native",
            startup_complete=True,
            startup_revision=1,
        )
        started = reopened.start(
            t.id,
            space.worker_id,
            expected_generation=current_task(reopened, t.id).generation,
            owner_session_id="third-native",
            now=third_due + 1,
        )
        if history == "proposal-governed-foreign":
            with reopened._connect() as conn:
                append_proposal(conn, foreign=True)
            with pytest.raises(TaskError, match="unconfirmed assignment"):
                reopened.complete(t.id, space.worker_id, result={"third": True})
            assert current_task(reopened, t.id) == started
            return
        with pytest.raises(TaskError, match="superseded"):
            reopened.prepare_submission(
                t.id,
                space.worker_id,
                request_id=proposal["request"]["request_id"],
                expected_generation=started.generation,
                expected_owner_session_id="third-native",
                workspace=space,
            )
        assert (
            reopened.complete(t.id, space.worker_id, result={"third": True}).status == "submitted"
        )
        latest = reopened.submission_preparation(t.id)
        assert (
            latest is not None
            and latest["request"]["workspace"]["creation_nonce"] == space.creation_nonce
        )
        assert (
            latest["request"]["assignment_event_id"] != proposal["request"]["assignment_event_id"]
        )
        assert latest["request"]["request_id"] != proposal["request"]["request_id"]
        with reopened._connect() as conn:
            [prior, new] = conn.execute(
                "SELECT submission_preparation FROM task_events WHERE task_id=? AND submission_preparation IS NOT NULL ORDER BY id",
                (t.id,),
            )
        assert json.loads(prior[0]) == proposal and json.loads(new[0]) == latest
        return
    reopened.start(
        t.id,
        "host-a/replacement",
        expected_generation=current_task(reopened, t.id).generation,
        owner_session_id="other-native",
        now=due + 1,
    )
    if history == "late-proposal":
        with reopened._connect() as conn:
            append_proposal(conn, foreign=False)
            with pytest.raises(sqlite3.IntegrityError, match="readiness|unconfirmed"):
                conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (t.id,))
        with pytest.raises(TaskError):
            reopened.complete(t.id, "host-a/replacement", result={"ok": True})
        return
    reopened.complete(t.id, "host-a/replacement", result={"ok": True}, expected_status="started")
    if proposal is not None:
        assert reopened.submission_preparation(t.id) == proposal
    if "detached" in history:
        reopened.request_spawn_release(replacement.key, disposition="settled")
        reopened.retire_spawn(replacement.key, exact_absence=True)
        if history == "legacy-detached":
            from agent_dispatch import completion_origins

            monkeypatch.setattr(completion_origins, "capture_terminal_detachment", lambda *_: None)
        reopened.recover_task(
            t.id,
            operation="detach_agent",
            expected_revision=reopened.task_diagnostics(t.id)["revision"],
            request_id="remove-native",
            actor="operator",
            reason="verified retirement",
        )
        before = current_task(reopened, t.id)
        assert before.owner_session_id is None
        if history == "legacy-detached":
            with pytest.raises(TaskError, match="exact original"):
                reopened.complete(t.id, "host-a/replacement", result={"ok": True})
            assert current_task(reopened, t.id) == before
            return
        replay = reopened.complete_with_outcome(t.id, "host-a/replacement", result={"ok": True})
        assert replay.task == before and replay.event_type is None
        status_retry = reopened.complete_with_outcome(
            t.id, "host-a/replacement", result={"ok": True}, expected_status="started"
        )
        assert status_retry.task == before and status_retry.event_type is None
        with pytest.raises(TaskError, match="exact original"):
            reopened.complete(
                t.id, "host-a/replacement", result={"ok": True}, expected_status="suspended"
            )
        if history == "proposal-submitted-detached":
            assert before.status == "submitted" and before.require_verification
            with (
                reopened._connect() as conn,
                pytest.raises(sqlite3.IntegrityError, match="result is retained"),
            ):
                conn.execute("UPDATE tasks SET require_verification=0 WHERE id=?", (t.id,))
            from agent_dispatch.completion_origins import exact_detached_completion

            with reopened._connect() as conn:
                conn.execute("BEGIN")
                conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (t.id,))
                assert not exact_detached_completion(
                    conn,
                    t.id,
                    worker_id="host-a/replacement",
                    encoded_result=reopened._encode_result({"ok": True}),
                    result_ref=None,
                    expected_status="started",
                )
                conn.rollback()
            before = reopened.confirm(t.id, actor="reviewer")
            after_confirm = reopened.complete_with_outcome(
                t.id, "host-a/replacement", result={"ok": True}, expected_status="started"
            )
            assert (
                before.status == "completed"
                and after_confirm.task == before
                and after_confirm.event_type is None
            )
        for worker, data in [
            ("host-a/other", {"ok": True}),
            ("host-a/replacement", {"ok": False}),
        ]:
            with pytest.raises(TaskError, match="exact original"):
                reopened.complete(t.id, worker, result=data)
        with pytest.raises(TaskError, match="incarnation changed"):
            reopened.complete(
                t.id,
                "host-a/replacement",
                result={"ok": True},
                expected_generation=before.generation - 1,
                expected_owner_session_id="other-native",
            )
        with (
            reopened._connect() as conn,
            pytest.raises(sqlite3.IntegrityError, match="unconfirmed"),
        ):
            conn.execute("UPDATE tasks SET status='started' WHERE id=?", (t.id,))
        assert current_task(reopened, t.id) == before
        if proposal is not None:
            assert reopened.submission_preparation(t.id) == proposal
            assert "pool:worker" in before.excludes
            cleared = reopened.clear_exclude(t.id, exclude="pool:worker")
            assert (
                "pool:worker" not in cleared.excludes
                and cleared.require_verification == before.require_verification
            )
    assert current_task(reopened, t.id).status == "completed"


@pytest.mark.parametrize("successor_driver", ["ordinary-bridge", "cli"])
@pytest.mark.parametrize("predecessor", ["empty", "bound-project", "unknown-send"])
def test_only_fenced_preallocation_reemission_gets_a_new_revision(
    q: TaskQueue, predecessor: str, successor_driver: str
) -> None:
    winner = pool()
    revision = publish(q, winner)
    q._ordinary_capture = OrdinaryCapture("local-ordinary-instance", winner.identity)
    q._ordinary_relationship_writer = LocalOrdinaryRelationshipWriter(q)
    first = task(q, dedup_key="recurring")
    held, _ = q.reserve_spawn(
        first.id, reserved_by="allocator", driver="ordinary-bridge", pool_catalog_revision=revision
    )
    assert held.task_relationship_required
    if predecessor == "bound-project":
        q.record_spawn_allocation(held.key, "example")
    q.request_spawn_release(held.key, finished_startup_revision=1)
    old = q.retire_spawn(held.key, exact_absence=True, conclusion_state="skipped")
    q.abandon(first.id, permitted=True, reason="prior source closed")
    second = task(q, dedup_key="recurring")
    if predecessor == "unknown-send":
        with q._connect() as conn:
            conn.execute(
                "INSERT INTO execution_send_custody VALUES "
                "('scope','send','example','fixture',1,?,?,1,'launch','{}','unknown','outcome unconfirmed',100,100)",
                (first.id, held.key),
            )
    if predecessor != "empty":
        with pytest.raises(
            TaskError,
            match=(
                "unresolved execution send"
                if predecessor == "unknown-send"
                else "original task and driver"
            ),
        ):
            q.reserve_spawn(
                second.id,
                reserved_by="allocator",
                driver=successor_driver,
                pool_catalog_revision=revision,
            )
        assert not q.list_reservations(task_id=second.id)
    else:
        q._ordinary_capture = OrdinaryCapture("local-ordinary-drain")
        new, created = q.reserve_spawn(
            second.id,
            reserved_by="allocator",
            driver=successor_driver,
            pool_catalog_revision=revision,
        )
        assert (
            created
            and not new.task_relationship_required
            and new.allocation_reservation_key == new.key
        )
        assert (
            new.creation_key != held.creation_key
            and new.allocation_revision == held.allocation_revision + 1
        )
    assert q.get_reservation(held.key) == old
