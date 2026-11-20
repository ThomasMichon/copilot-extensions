"""Prospective ordinary capture, allocation continuity and drain closeout."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import subprocess
from typing import Any

import pytest
from agent_dispatch import assignment_local_ordinary as native
from agent_dispatch import server, telemetry
from agent_dispatch.config import Config
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnReservation, TaskError
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_local_ordinary_admission import local as local
from tests.test_pool_decline_closeout import governed as governed
from tests.test_ordinary_pool_startup import api as api
from tests.test_ordinary_pool_startup import launch, ordinary as ordinary, q as q
from tests.test_pool_placement import assignment_for, current_task, task as new_task


@pytest.mark.parametrize(
    "spec",
    [
        "local-ordinary-instance=",
        'local-ordinary-instance={"registration_id":"worker","machine":"host-a"}',
        'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"*"}',
        'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"linux","env":"wsl"}',
        'local-ordinary-instance={"registration_id":"worker","machine":null,"env":"linux"}',
        'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"linux","all":true}',
    ],
)
def test_invalid_capture_refuses_before_queue_construction(tmp_path: Path, spec: str) -> None:
    database = tmp_path / "never-created.db"
    with pytest.raises(ValueError, match="exact pool instance"):
        server.build_app(
            Config(
                db_path=str(database), relationship_authority="local", relationship_admission=spec
            )
        )
    assert not database.exists()


def test_exact_winner_is_prospective_across_reconfiguration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_dispatch.assignment_admission import configured_writer
    from tests.test_pool_placement import pool, publish

    monkeypatch.setattr(telemetry, "load_sink_from_config", lambda: True)
    monkeypatch.setattr(server, "require_worker_admission", lambda: None)
    monkeypatch.setattr(
        server,
        "create_app",
        lambda queue, **_: SimpleNamespace(state=SimpleNamespace(queue=queue)),
    )
    cfg = Config(db_path=str(tmp_path / "queue.db"))
    old = server.build_app(cfg).state.queue
    revision = publish(
        old,
        pool(capacity=4, priority=1),
        pool(name="other"),
        pool(env="wsl"),
        pool(machine="host-b"),
    )

    def reserve(
        queue: TaskQueue, task: Task | None = None, driver: str = "ordinary-bridge"
    ) -> tuple[SpawnReservation, bool]:
        return queue.reserve_spawn(
            (task or new_task(queue)).id,
            reserved_by="allocator",
            driver=driver,
            pool_catalog_revision=revision,
        )

    selector = (
        'local-ordinary-instance={"registration_id":"worker","machine":"host-a","env":"linux"}'
    )
    # Coordinators predating scoped capture route both unfamiliar values to the factory parser.
    for spec in (selector, "local-ordinary-drain"):
        with pytest.raises(ValueError, match="module:factory"):
            configured_writer(spec, "local")
    cfg = replace(cfg, relationship_authority="local", relationship_admission=selector)
    with pytest.raises(ValueError, match="local queue authority"):
        server.build_app(replace(cfg, relationship_authority="central"))
    legacy, _ = reserve(old)
    queue = server.build_app(cfg).state.queue
    replay, created = reserve(queue, queue.get(legacy.task_id))
    assert not created and replay == legacy and not replay.task_relationship_required
    with pytest.raises(TaskError, match="legacy reservation"):
        queue.record_spawn_worktree(legacy.key, "late", creation_nonce="late")
    cli, _ = reserve(queue, driver="cli")
    assert not cli.task_relationship_required
    selected, _ = reserve(queue)
    assert selected.task_relationship_required
    assert queue.task_pool_assignment(selected.task_id).registration_id == "worker"
    drained = server.build_app(
        replace(cfg, relationship_admission="local-ordinary-drain")
    ).state.queue
    assert reserve(drained, drained.get(selected.task_id)) == (selected, False)
    drain_new, _ = reserve(drained)
    assert not drain_new.task_relationship_required
    for expected in [
        ("other", "host-a", "linux"),
        ("worker", "host-a", "wsl"),
        ("worker", "host-b", "linux"),
    ]:
        other, _ = reserve(queue)
        assignment = queue.task_pool_assignment(other.task_id)
        assert (assignment.registration_id, assignment.machine, assignment.env) == expected
        assert not other.task_relationship_required


@pytest.mark.parametrize("retry_stage, disposition", [
    ("initial", "source"), ("empty", "source"), ("prepared", "source"),
    ("prepared-before-start", "source"), ("empty", "complete"), ("prepared", "complete"),
    ("empty", "decline"), ("prepared", "decline"),
])
@pytest.mark.parametrize("ordinary", ["scoped"], indirect=True)
def test_scoped_original_receipt_and_disposition_survive_drain(
    local: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, retry_stage: str, disposition: str
) -> None:
    import json

    from agent_dispatch import pool_decline_observation, procutil, remote_dispatch, submission_readiness_observation
    from agent_dispatch.ordinary_capture import configured_capture
    from agent_dispatch.queue_relevance import RelevanceScope
    from tests._ordinary_relationship_controls import report

    queue = local.q
    assert local.placed["reservation"]["task_relationship_required"]
    queue._ordinary_capture = configured_capture("local-ordinary-drain", "local")
    assert local.client.health()["task_relationships"]["scope"] == "local-ordinary-drain"
    first = queue.get_reservation(local.placed["reservation"]["key"])
    if retry_stage != "initial":
        from agent_dispatch import ordinary_bridge
        from tests.test_pool_startup_policy import exhaust, renew

        if retry_stage.startswith("prepared"):
            original_run = ordinary_bridge.subprocess.run
            with monkeypatch.context() as once:

                def stop_at_create(
                    argv: list[str], **kwargs: Any
                ) -> subprocess.CompletedProcess[str]:
                    if argv[2] == "create":
                        return subprocess.CompletedProcess(argv, 1, "", "no native created")
                    return original_run(argv, **kwargs)

                once.setattr(ordinary_bridge.subprocess, "run", stop_at_create)
                with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
                    launch(local)
            assert queue.task_relationship(first.task_id)["state"] == "bound"
        queued = exhaust(queue, queue.get(first.task_id), first)
        queue.request_spawn_release(first.key, finished_startup_revision=1, now=701)
        queue.retire_spawn(first.key, exact_absence=True, conclusion_state="skipped", now=702)
        monkeypatch.setattr(
            queue, "_now", lambda now=None: queued.not_before if now is None else now
        )
        renew(queue, queued.not_before)
        retry, _ = queue.reserve_spawn(
            first.task_id,
            reserved_by="allocator",
            driver="ordinary-bridge",
            pool_catalog_revision=queue.pool_catalog_snapshot().revision,
        )
        assert retry.key != first.key and retry.task_relationship_required
        local.placed["reservation"] = asdict(retry)
        local.task.update(asdict(queue.get(first.task_id)), spawn_reservation_key=retry.key)
    launch(local)
    key = local.placed["reservation"]["key"]
    reservation = queue.get_reservation(key)
    assert reservation.worktree_writer_requirement == {
        "version": 1,
        "identity": local.writer,
        "creation_nonce": reservation.creation_nonce,
    }
    assert (
        reservation.agent_info["worktree_writer_requirement"]
        == reservation.worktree_writer_requirement
    )
    queue.record_spawn(
        key,
        session_handle=reservation.session_handle,
        worktree=reservation.worktree,
        startup_complete=True,
        startup_revision=reservation.startup_revision,
    )
    task = queue.get(local.task["id"])
    original = queue.task_relationship(task.id)
    assert original["state"] == "bound"
    worker = "host-a/worktree-a"
    def ready(request: dict[str, Any], allocation: dict[str, Any], ref: str) -> dict[str, bool]:
        assert request["owner_session_id"] == "native-a"
        assert (allocation["reservation_key"], allocation["attempt"]) == (first.key, first.attempt)
        assert ref == original["receipt"]["ref"]
        return {"ready": True}

    monkeypatch.setattr(submission_readiness_observation, "observe", ready)
    if disposition == "decline":
        binding = dict(worker_id=worker, native_id="native-a", generation=task.generation,
                       startup_revision=reservation.startup_revision, reservation_key=key)
        queue.prepare_pool_decline(task.id, **binding, exclusions=["pool:worker"], note="missing tool")
        closed = queue.close_pool_decline(task.id, **binding)
        assert closed.applied and closed.receipt["binding"] == binding
        assert closed.relationship == {"state": "closed", "event_id": original["request"]["event_id"]}
        queue.retire_spawn(key, exact_absence=True, conclusion_state="skipped")
        from tests.test_relationship_discharge import replace_after_decline
        reopened = TaskQueue(queue.db_path)
        replacement = replace_after_decline(reopened, task.id, closed.receipt["not_before"])
        assert not replacement.task_relationship_required
        return
    if retry_stage != "prepared-before-start":
        local.client.start_pool_task(task.id, worker, owner_session_id="native-a", expected_generation=task.generation)
    if disposition == "complete":
        queue.complete(task.id, worker, result={"outcome": "done"})
        assert queue.get(task.id).status == "submitted"
        assert queue.task_relationship(task.id)["state"] == "bound"
        return
    report(queue, task.id, scope=RelevanceScope(task.repo, task.source, task.origin_ref))
    result = queue.prepare_relevance_closeout(
        task.id,
        request_id="closed-source",
        worker_id=worker,
        native_id="native-a",
        expected_generation=queue.get(task.id).generation,
    )
    assert result["relationship"] == {
        "state": "closed",
        "event_id": original["request"]["event_id"],
    }
    assert (
        queue.get_reservation(key).worktree_writer_requirement
        == reservation.worktree_writer_requirement
    )
    ground = local.ground
    native_id = ground["last_session_id"]
    observation = {
        "ok": True, "contract": pool_decline_observation.CONTRACT, "version": 1,
        "operation": "observe-finalized", "status": "finalized", "task_id": task.id,
        "reservation_key": first.key, "native_id": native_id, "worktree_id": ground["id"],
        "project": ground["repo"], "path": ground["path"], "machine": "host-a",
        "creation_nonce": ground["creation_nonce"], "allocation": ground["dispatch_attempt"],
        "head_revision": 1, "checkout": {"head": "a" * 40, "branch": "worktree/test"},
        "checkout_retained": True, "task_release": False, "process_retirement": False,
    }

    def finalized(*argv: str, timeout: float) -> subprocess.CompletedProcess[str]:
        assert argv[argv.index("--closeout-reservation") + 1] == first.key
        assert argv[argv.index("--closeout-native") + 1] == native_id == "native-a"
        return subprocess.CompletedProcess(argv, 0, json.dumps(observation), "")

    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(procutil, "run_agent_worktrees_capture", finalized)
    arguments = result["receipt"]["arguments"]
    finished = queue.finish_relevance_closeout(task.id, **arguments)
    assert finished.event_type == "task.abandoned"
    assert queue.get(task.id).status == finished.task.status == "abandoned"
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    acknowledgement = closeout["acknowledgement"]
    assert acknowledgement["arguments"] == arguments
    assert acknowledgement["binding"] == closeout["resource_binding"] == result["receipt"]["binding"]
    assert acknowledgement["binding"]["reservation_key"] == key
    assert acknowledgement["observation"] == observation
    assert acknowledgement["process_retirement"] is False


@pytest.mark.parametrize("initially_selected", [False, True])
def test_retry_of_same_allocation_keeps_initial_capture(
    q: TaskQueue, initially_selected: bool
) -> None:
    from agent_dispatch.ordinary_capture import OrdinaryCapture
    from tests.test_pool_placement import pool, publish
    from tests.test_pool_startup_policy import exhaust, renew

    winner = pool()
    revision = publish(q, winner)
    selected = OrdinaryCapture("local-ordinary-instance", winner.identity)
    drain = OrdinaryCapture("local-ordinary-drain")
    q._ordinary_relationship_writer = native.LocalOrdinaryRelationshipWriter(q)
    q._ordinary_capture = selected if initially_selected else drain
    task = new_task(q)
    original, _ = q.reserve_spawn(
        task.id, pool_catalog_revision=revision, reserved_by="allocator", driver="ordinary-bridge"
    )
    assert original.task_relationship_required is initially_selected
    queued = exhaust(q, current_task(q, task.id), original)
    q.request_spawn_release(original.key, finished_startup_revision=1, now=701)
    q.retire_spawn(original.key, exact_absence=True, now=702)
    renew(q, queued.not_before)
    q._ordinary_capture = drain if initially_selected else OrdinaryCapture()
    if initially_selected:
        with pytest.raises(TaskError, match="requires its original task and driver"):
            q.reserve_spawn(
                task.id,
                pool_catalog_revision=q.pool_catalog_snapshot().revision,
                reserved_by="allocator",
                driver="cli",
                now=queued.not_before,
            )
    retry, created = q.reserve_spawn(
        task.id,
        pool_catalog_revision=q.pool_catalog_snapshot().revision,
        now=queued.not_before,
        reserved_by="allocator",
        driver="ordinary-bridge",
    )
    assert (
        created and retry.key != original.key and retry.allocation_reservation_key == original.key
    )
    assert retry.task_relationship_required is initially_selected


@pytest.mark.parametrize("original, successor, driver, required, refusal", [
    ("local-ordinary-drain", "policy:factory", "ordinary-bridge", False, None),
    ("local-ordinary-drain", "policy:factory", "cli", False, None),
    ("local-ordinary", "policy:factory", "cli", True, "original task and driver"),
    ("local-ordinary", "policy:factory", "ordinary-bridge", True, "original admission policy"),
    ("policy:factory", "local-ordinary", "ordinary-bridge", True, "original admission policy"),
    ("policy:factory", "policy:other", "ordinary-bridge", True, "original admission policy"),
    ("policy:factory", "central/policy:factory", "ordinary-bridge", True, "original admission policy"),
    ("anonymous", "policy:factory", "ordinary-bridge", True, "original admission policy"),
    ("policy:factory", "policy:factory", "ordinary-bridge", True, None),
])
def test_ordinary_allocation_decision_survives_factory_cutover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original: str, successor: str,
    driver: str, required: bool, refusal: str | None,
) -> None:
    from agent_dispatch import assignment_admission
    from tests.test_assignment_relationships import GroundOwner
    from tests.test_pool_placement import pool, publish
    from tests.test_pool_startup_policy import exhaust, renew

    owner = GroundOwner()
    monkeypatch.setattr(assignment_admission, "configured_writer", lambda *_: owner)
    monkeypatch.setattr(telemetry, "load_sink_from_config", lambda: True)
    monkeypatch.setattr(server, "require_worker_admission", lambda: None)
    monkeypatch.setattr(server, "create_app", lambda queue, **_: SimpleNamespace(state=SimpleNamespace(queue=queue)))
    cfg = Config(db_path=str(tmp_path / "queue.db"), relationship_authority="local")

    def opened(spec: str) -> TaskQueue:
        if spec == "anonymous":
            queue = TaskQueue(cfg.db_path, relationship_writer=owner, relationship_authority="local")
        else:
            selected_authority = "central" if spec.startswith("central/") else "local"
            queue = server.build_app(replace(cfg, relationship_admission=spec.removeprefix("central/"),
                                             relationship_authority=selected_authority)).state.queue
        monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
        return queue

    q = opened(original)
    revision = publish(q, pool())
    task = new_task(q)
    first, _ = q.reserve_spawn(task.id, reserved_by="allocator", driver="ordinary-bridge", pool_catalog_revision=revision)
    assert first.task_relationship_required is required
    queued = exhaust(q, current_task(q, task.id), first)
    q.request_spawn_release(first.key, finished_startup_revision=1, now=701)
    q.retire_spawn(first.key, exact_absence=True, now=702)
    reopened = opened(successor)
    renew(reopened, queued.not_before)
    revision = reopened.pool_catalog_snapshot().revision

    def retry() -> tuple[SpawnReservation, bool]:
        return reopened.reserve_spawn(task.id, reserved_by="allocator", driver=driver, pool_catalog_revision=revision,
                                      now=queued.not_before)

    if refusal:
        before = reopened.list_reservations(task_id=task.id), current_task(reopened, task.id)
        with pytest.raises(TaskError, match=refusal):
            retry()
        assert (reopened.list_reservations(task_id=task.id), current_task(reopened, task.id)) == before
    else:
        second, created = retry()
        assert created and second.allocation_reservation_key == first.key
        assert second.task_relationship_required is required and second.driver == driver
    assert not owner.calls


def test_reemission_keeps_settled_legacy_workspace_ungoverned(q: TaskQueue) -> None:
    from agent_dispatch.ordinary_capture import OrdinaryCapture
    from tests.test_pool_placement import pool, publish

    winner = pool()
    revision = publish(q, winner)
    task = new_task(q, dedup_key="recurring", exclusive_key="shared")
    first, _ = q.reserve_spawn(
        task.id, reserved_by="allocator", driver="ordinary-bridge", pool_catalog_revision=revision
    )
    q.record_spawn_worktree(
        first.key,
        "original",
        ownership="created",
        creating_host="host-a",
        execution_machine="host-a",
        driver="ordinary-bridge",
    )
    q.request_spawn_release(first.key, finished_startup_revision=1)
    q.retire_spawn(first.key, exact_absence=True, conclusion_state="skipped")
    q.abandon(task.id, permitted=True, reason="source settled")
    successor = new_task(q, dedup_key="recurring", exclusive_key="shared")
    q._ordinary_capture = OrdinaryCapture("local-ordinary-instance", winner.identity)
    q._ordinary_relationship_writer = native.LocalOrdinaryRelationshipWriter(q)
    second, created = q.reserve_spawn(
        successor.id,
        reserved_by="allocator",
        driver="ordinary-bridge",
        pool_catalog_revision=revision,
    )
    assert created and second.worktree == "original"
    assert first.task_id != second.task_id and second.allocation_reservation_key == first.key
    assert not second.task_relationship_required


@pytest.mark.parametrize(
    "drain, driver", [(False, "ordinary-bridge"), (True, "ordinary-bridge"), (True, "cli")]
)
def test_settled_decline_permits_unselected_replacement(
    governed: SimpleNamespace, drain: bool, driver: str
) -> None:
    from agent_dispatch.ordinary_capture import OrdinaryCapture
    from agent_dispatch.queue_task_relationships import require_relationship
    from tests.test_pool_placement import pool

    q: TaskQueue = governed.q
    task_id: str = governed.task_id
    outcome = q.close_pool_decline(task_id, **governed.binding)
    assert outcome.relationship and outcome.relationship["state"] == "closed"
    q.retire_spawn(
        governed.binding["reservation_key"], exact_absence=True, conclusion_state="skipped"
    )
    q.publish_pool_catalog_scope(
        "host-a",
        "linux",
        "publisher-0",
        generation=1,
        expected_revision=1,
        registration_revision=q.pool_registration_revision(),
        expected_cohort_revision=1,
        input_revision="replacement",
        definitions=(pool(), pool("other")),
        now=4000,
    )
    q._relationship_writer = None
    q._ordinary_relationship_writer = native.LocalOrdinaryRelationshipWriter(q)
    q._ordinary_capture = (
        OrdinaryCapture("local-ordinary-drain")
        if drain
        else OrdinaryCapture("local-ordinary-instance", pool().identity)
    )
    replacement, created = q.reserve_spawn(
        task_id,
        reserved_by="allocator",
        driver=driver,
        pool_catalog_revision=q.pool_catalog_snapshot().revision,
        now=4000,
    )
    assert created and not replacement.task_relationship_required
    assert replacement.key != governed.binding["reservation_key"]
    assert assignment_for(q, task_id).registration_id == "other"
    with q._connect() as conn:
        require_relationship(conn, task_id)
        from agent_dispatch.queue_submission_preparation import submission_required_condition

        assert not conn.execute(
            "SELECT " + submission_required_condition("t") + " FROM tasks t WHERE t.id=?",
            (task_id,),
        ).fetchone()[0]
    if driver == "cli":
        q.record_spawn_worktree(
            replacement.key, "new-tree", ownership="created", creating_host="host-a",
            execution_machine="host-a", driver="cli", allocation_project="example",
            worktree_path="/new-tree",
        )
        q.record_spawn(replacement.key, worktree="new-tree", session_handle="second-native",
                       startup_complete=True, startup_revision=1)
        q.start(
            task_id,
            "host-a/new-tree",
            owner_session_id="second-native",
            now=4001,
            expected_generation=current_task(q, task_id).generation,
        )
        q.complete(task_id, "host-a/new-tree", result={"verified": True})
        assert current_task(q, task_id).status == "submitted"
