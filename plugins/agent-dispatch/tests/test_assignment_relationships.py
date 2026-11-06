"""Actual assignment owners retain a single cross-owner request before admission."""

from __future__ import annotations

import base64
import copy
import json
import secrets
import sqlite3
import subprocess
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_task_relationships import WRITER
from agent_dispatch.task_obligations import read_snapshot
from agent_dispatch.task_relationships import AssignmentSubject
from agent_dispatch.workspace_binding import parse_created_workspace
from tests.test_pool_placement import place, pool, publish


class GroundOwner:
    def __init__(self) -> None:
        self.claims: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.lose_reply: str | None = None
        self.before_reply: Any = None
        self.queue: TaskQueue | None = None

    def __call__(self, workspace: AssignmentWorkspace, operation: str, payload: dict) -> dict:
        self.calls.append((operation, copy.deepcopy(payload)))
        assert payload["expected_creation_nonce"] == workspace.creation_nonce
        if operation == "reserve":
            request_id = payload["request_id"]
            if request_id not in self.claims:
                self.claims[request_id] = {
                    "version": 2,
                    "lifetime": "retained-relationship",
                    "token": secrets.token_urlsafe(32),
                    "namespace": payload["namespace"],
                    "phase": "pending",
                    "creator": workspace.owner_ref,
                    "request_id": request_id,
                    "recovery": payload["recovery"],
                }
            metadata = self.claims[request_id]
        else:
            metadata = (
                self.claims.get(payload["request_id"])
                if "request_id" in payload
                else next(
                    value for value in self.claims.values() if value["token"] == payload["token"]
                )
            )
            if metadata is None:
                return {
                    "schema": "copilot-extensions.resource-relationship",
                    "version": 1,
                    "ok": True,
                    "owner": workspace.owner_ref,
                    "request_id": payload["request_id"],
                    "creation_nonce": workspace.creation_nonce,
                    "writer_contract": WRITER,
                    "absent": True,
                }
            if operation == "bind":
                assert metadata["phase"] != "closed"
                metadata.update(phase="bound", identity=payload["identity"])
            elif operation == "close":
                assert self.queue is not None
                subject = AssignmentSubject.decode(
                    self.reference(metadata).split(":", 1)[1], authority="local"
                )
                proof = subject.verification(
                    {
                        "task_obligation": read_snapshot(
                            self.queue,
                            subject.task_id,
                            assignment_event_id=subject.assignment_event_id,
                        )
                    }
                )
                assert proof["relationship_verification"]["retired"]
                metadata.update(phase="closed", closeout={"available": True, **proof})
        result = {
            "schema": "copilot-extensions.resource-relationship",
            "version": 1,
            "ok": True,
            "owner": workspace.owner_ref,
            "creation_nonce": workspace.creation_nonce,
            "writer_contract": WRITER,
            "ref": self.reference(metadata),
            "producer": copy.deepcopy(metadata),
            **{key: metadata[key] for key in ("creator", "request_id", "token", "phase")},
        }
        if self.before_reply:
            self.before_reply(operation, result)
        if self.lose_reply == operation:
            self.lose_reply = None
            raise TimeoutError("owner wrote; response was lost")
        return result

    @staticmethod
    def reference(metadata: dict) -> str:
        envelope = {
            key: metadata[key] for key in ("version", "token", "creator", "request_id", "recovery")
        }
        envelope["phase"] = "bound" if "identity" in metadata else "pending"
        if "identity" in metadata:
            envelope["identity"] = metadata["identity"]
        return (
            metadata["namespace"]
            + ":l1."
            + base64.urlsafe_b64encode(
                json.dumps(
                    envelope,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("ascii")
            )
            .decode("ascii")
            .rstrip("=")
        )


@pytest.fixture
def assignment(tmp_path):
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.db", relationship_writer=owner, relationship_authority="local"
    )
    owner.queue = queue
    workspace = AssignmentWorkspace(
        "host-a", "example", "worktree", str(tmp_path / "checkout"), "original"
    )
    task = queue.create("work", repo="example", require_verification=True)
    return queue, owner, workspace, task


def claim(queue, workspace, task):
    return queue.claim_one(workspace.worker_id, task_id=task.id, assignment_workspace=workspace)


def test_direct_claim_journals_outbound_obligation_before_success_and_replays(assignment):
    queue, owner, workspace, task = assignment
    claimed = claim(queue, workspace, task)
    value = queue.task_relationship(task.id)
    assert claimed.status == Status.CLAIMED and value["state"] == "bound"
    assert value["request"]["workspace"] == asdict(workspace)
    assert value["request"]["reservation_key"] is None
    assert [operation for operation, _ in owner.calls] == ["reserve", "bind"]
    assert value["receipt"]["owner"] == workspace.owner_ref
    assert value["receipt"]["producer"]["identity"]["worker_id"] == workspace.worker_id
    assert claim(queue, workspace, task) == claimed
    assert len(owner.claims) == 1 and queue.task_relationship(task.id) == value
    with pytest.raises(TaskError, match="conflicts"):
        claim(queue, replace(workspace, creation_nonce="replacement"), task)
    from agent_dispatch.task_obligations import ASSIGNMENT_PREDICATE

    with queue._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id, worker,"  # noqa: S608 -- fixed schema predicate
            " task_relationship FROM task_events WHERE task_id=? AND "
            + ASSIGNMENT_PREDICATE + " ORDER BY id DESC LIMIT 1",
            (task.id,),
        ).fetchall()
    assert any("USING INDEX idx_task_events_worker_assignment" in row[3] for row in plan)


@pytest.mark.parametrize("operation", ["reserve", "bind"])
def test_lost_effect_response_retains_request_and_blocks_execution_until_reconciled(
    assignment, operation
):
    queue, owner, workspace, task = assignment
    owner.lose_reply = operation
    with pytest.raises(TaskError, match="unresolved"):
        claim(queue, workspace, task)
    value = queue.task_relationship(task.id)
    assert value["state"] == "pending" and queue.get(task.id).owner == workspace.worker_id
    with pytest.raises(TaskError, match="unconfirmed"):
        queue.start(task.id, workspace.worker_id)
    restarted = TaskQueue(queue.db_path)
    with pytest.raises(TaskError, match="unavailable"):
        restarted.reconcile_task_relationship(task.id)
    with restarted._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
        conn.execute("UPDATE tasks SET status='started' WHERE id=?", (task.id,))
    queue._relationship_writer = owner
    assert claim(queue, workspace, task).generation == value["request"]["generation"]
    assert queue.task_relationship(task.id)["state"] == "bound" and len(owner.claims) == 1
    queue.start(task.id, workspace.worker_id)


@pytest.mark.parametrize("operation", ["reserve", "bind"])
def test_cancelled_before_acknowledgement_settles_original_pending_or_bound_claim(
    assignment, operation
):
    queue, owner, workspace, task = assignment
    owner.lose_reply = operation
    with pytest.raises(TaskError):
        claim(queue, workspace, task)
    pending = queue.task_relationship(task.id)
    queue.yield_task(task.id, workspace.worker_id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert len(owner.claims) == 1
    new = claim(queue, workspace, task)
    assert new.generation > pending["request"]["generation"]
    assert (
        queue.task_relationship(task.id)["request"]["request_id"]
        != pending["request"]["request_id"]
    )


def test_cancelled_unknown_reserve_is_tombstoned_not_released_from_absence_alone(assignment):
    queue, owner, workspace, task = assignment
    queue._relationship_writer = lambda *a: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(TaskError):
        claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    queue._relationship_writer = owner
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert [operation for operation, _ in owner.calls] == ["inspect", "reserve", "close"]


def test_submission_and_abandonment_keep_responsibility_until_independent_completion(assignment):
    queue, owner, workspace, task = assignment
    claim(queue, workspace, task)
    queue.start(task.id, workspace.worker_id, owner_session_id="native")
    queue.complete(task.id, workspace.worker_id, result={"answer": "proposed"})
    assert queue.task_relationship(task.id)["state"] == "bound"
    assert queue.reconcile_task_relationship(task.id)["state"] == "bound"
    queue.confirm(task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert next(iter(owner.claims.values()))["closeout"]["relationship_verification"]["retired"]


def test_legacy_or_forged_writer_answer_never_admits_execution(assignment):
    queue, owner, workspace, task = assignment

    def old_reply(operation, result):
        result.pop("writer_contract")

    owner.before_reply = old_reply
    with pytest.raises(TaskError, match="did not confirm"):
        claim(queue, workspace, task)
    assert queue.task_relationship(task.id)["state"] == "pending"
    with pytest.raises(TaskError):
        queue.start(task.id, workspace.worker_id)


def test_allocator_attachment_not_pool_actor_owns_the_relationship_and_survives_rollback(
    assignment,
):
    queue, owner, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    assert reservation.task_relationship_required and queue.task_relationship(task.id) is None
    with pytest.raises(TaskError, match="creation nonce"):
        queue.record_spawn_worktree(
            reservation.key,
            workspace.worktree,
            ownership="created",
            execution_machine=workspace.machine,
            allocation_project=workspace.project,
            worktree_path=workspace.path,
            driver="cli",
        )
    owner.lose_reply = "bind"
    with pytest.raises(TaskError):
        queue.record_spawn_worktree(
            reservation.key,
            workspace.worktree,
            ownership="created",
            execution_machine=workspace.machine,
            allocation_project=workspace.project,
            worktree_path=workspace.path,
            driver="cli",
            creation_nonce=workspace.creation_nonce,
        )
    recorded = queue.get_reservation(reservation.key)
    assert (
        recorded.creation_nonce == workspace.creation_nonce and recorded.task_relationship_event_id
    )
    snapshot = read_snapshot(queue, task.id)
    assert snapshot["assignment"]["worker_id"] == workspace.worker_id
    assert snapshot["assignment"]["worker_id"] != reservation.reserved_by
    restarted = TaskQueue(queue.db_path)
    with restarted._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
        conn.execute("UPDATE tasks SET status='started' WHERE id=?", (task.id,))
    queue.record_spawn_worktree(
        reservation.key,
        workspace.worktree,
        ownership="created",
        execution_machine=workspace.machine,
        allocation_project=workspace.project,
        worktree_path=workspace.path,
        driver="cli",
        creation_nonce=workspace.creation_nonce,
    )
    assert queue.task_relationship(task.id)["state"] == "bound"
    assert queue.get(task.id).owner is None and len(owner.claims) == 1


def test_current_api_and_client_negotiate_and_do_not_send_new_syntax_to_old_owner(assignment):
    queue, owner, workspace, task = assignment
    requests = []
    with TestClient(create_app(queue, enable_mcp=False)) as api:

        def handle(request):
            requests.append((request.method, request.url.path))
            result = api.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=request.headers,
            )
            return httpx.Response(result.status_code, json=result.json())

        with DispatchClient("http://testserver", transport=httpx.MockTransport(handle)) as client:
            result = client.claim(
                workspace.worker_id,
                repo="example",
                task_id=task.id,
                assignment_workspace=asdict(workspace),
            )
        assert result["status"] == Status.CLAIMED and len(owner.claims) == 1
    assert requests == [("GET", "/health"), ("POST", "/claim")]
    requests.clear()

    def old(request):
        requests.append(request.method)
        return httpx.Response(200, json={"status": "ok"})

    with DispatchClient("http://old.invalid", transport=httpx.MockTransport(old)) as client:
        with pytest.raises(DispatchError, match="cannot protect"):
            client.claim("worker", repo="example", assignment_workspace=asdict(workspace))
    assert requests == ["GET"]


def test_original_creation_nonce_is_not_dropped():
    from subprocess import CompletedProcess

    value = parse_created_workspace(
        CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "worktree": {
                        "id": "tree",
                        "path": "/checkout",
                        "repo": "example",
                        "creation_nonce": "original",
                    },
                    "creation": {"version": 1, "status": "complete"},
                }
            ),
            "",
        ),
        creation_key="original-request",
    )
    assert value["creation_nonce"] == "original"


@pytest.mark.parametrize("state", ["suspended", "abandoned", "dead_letter"])
def test_other_dispositions_do_not_settle_pending_responsibility(assignment, state):
    queue, owner, workspace, task = assignment
    owner.lose_reply = "reserve"
    with pytest.raises(TaskError):
        claim(queue, workspace, task)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (state, task.id))
    if state == "suspended":
        assert queue.reconcile_task_relationship(task.id)["state"] == "bound"
    else:
        with pytest.raises(TaskError, match="changed"):
            queue.reconcile_task_relationship(task.id)
    assert not any(operation == "close" for operation, _ in owner.calls)


def test_assignment_change_during_ground_write_never_publishes_admission(assignment):
    queue, owner, workspace, task = assignment

    def unassign(operation, result):
        if operation == "bind":
            queue.yield_task(task.id, workspace.worker_id)

    owner.before_reply = unassign
    with pytest.raises(TaskError, match="changed during"):
        claim(queue, workspace, task)
    assert queue.get(task.id).status == Status.QUEUED
    assert queue.task_relationship(task.id)["state"] == "pending"
    owner.before_reply = None
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("cancel", ["before", "after"])
@pytest.mark.parametrize("with_outcome", [False, True])
def test_cancelled_reconciliation_never_acknowledges_original_or_replayed_claim(
    assignment, monkeypatch, replay, cancel, with_outcome,
):
    queue, owner, workspace, task = assignment
    if replay:
        claim(queue, workspace, task)
    reconcile = queue.reconcile_task_relationship

    def interrupted(task_id, *, event_id=None):
        if cancel == "before":
            queue.yield_task(task_id, workspace.worker_id)
        result = reconcile(task_id, event_id=event_id)
        if cancel == "after":
            queue.yield_task(task_id, workspace.worker_id)
            reconcile(task_id, event_id=event_id)
        return result

    monkeypatch.setattr(queue, "reconcile_task_relationship", interrupted)
    operation = queue.claim_outcome if with_outcome else queue.claim_one
    with pytest.raises(TaskError, match=r"cancelled during|changed during"):
        operation(
            workspace.worker_id, task_id=task.id, assignment_workspace=workspace,
        )
    assert queue.get(task.id).status == Status.QUEUED
    assert queue.get(task.id).owner is None
    assert queue.task_relationship(task.id)["state"] == "closed"
    assert len(owner.claims) == 1


def test_retained_requirement_cannot_be_omitted_or_cleared_after_requeue(assignment):
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    queue.reconcile_task_relationship(task.id)
    old_queue = TaskQueue(queue.db_path)
    with pytest.raises(TaskError, match="contract"):
        old_queue.claim_one(workspace.worker_id, task_id=task.id)
    with old_queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE task_events SET task_relationship=NULL WHERE task_id=?", (task.id,))
    with old_queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
        conn.execute(
            "UPDATE tasks SET status='claimed', owner=?, generation=generation+1 WHERE id=?",
            (workspace.worker_id, task.id),
        )


def test_required_matching_workspace_still_reconciles_pending_owner_write(assignment):
    from agent_dispatch.workspace_binding import record_prepared

    queue, owner, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    owner.lose_reply = "bind"
    facts = {
        "ownership": "created",
        "execution_machine": workspace.machine,
        "driver": "cli",
        "creating_host": workspace.machine,
        "allocation_project": workspace.project,
        "worktree_path": workspace.path,
        "creation_nonce": workspace.creation_nonce,
    }
    with pytest.raises(TaskError):
        queue.record_spawn_worktree(reservation.key, workspace.worktree, **facts)

    class Client:
        def record_spawn_worktree(self, key, worktree, **kwargs):
            return asdict(queue.record_spawn_worktree(key, worktree, **kwargs))

    current = asdict(queue.get_reservation(reservation.key))
    record_prepared(
        Client(),
        current,
        {
            "worktree": workspace.worktree,
            "ownership": "created",
            "project": workspace.project,
            "path": workspace.path,
            "creation_nonce": workspace.creation_nonce,
        },
        driver="cli",
        creating_host=workspace.machine,
    )
    assert queue.task_relationship(task.id)["state"] == "bound" and len(owner.claims) == 1


def test_disposition_reconciliation_uses_control_authority_not_a_worker_receipt(assignment):
    queue, owner, workspace, task = assignment
    owner.lose_reply = "reserve"
    with pytest.raises(TaskError):
        claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    event_id = queue.task_relationship(task.id)["request"]["event_id"]
    with TestClient(create_app(queue, control_token="test-control", enable_mcp=False)) as api:

        def handle(request):
            result = api.request(
                request.method,
                str(request.url),
                content=request.content,
                headers=request.headers,
            )
            return httpx.Response(result.status_code, json=result.json())

        transport = httpx.MockTransport(handle)
        with DispatchClient("http://testserver", transport=transport) as worker:
            with pytest.raises(DispatchError) as rejected:
                worker.reconcile_task_relationship(task.id, event_id=event_id)
            assert rejected.value.status_code == 403
        with DispatchClient(
            "http://testserver",
            transport=transport,
            control_token="test-control",
        ) as controller:
            assert (
                controller.reconcile_task_relationship(task.id, event_id=event_id)["state"]
                == "closed"
            )


@pytest.fixture
def request_assignment(assignment, monkeypatch):
    from agent_dispatch import assignment_relationship_owner as module
    from tests.test_assignment_relationship_owner import WRITER_IDENTITY, support

    queue, owner, workspace, task = assignment
    control = SimpleNamespace(refuse=False, admissions=[], captures=[])

    def capture(argv, **kwargs):
        control.captures.append(argv)
        if "capabilities" in argv:
            result = support()
        elif "record" in argv:
            result = {"ok": True, "worktree": {
                "id": workspace.worktree, "repo": workspace.project,
                "path": workspace.path, "creation_nonce": workspace.creation_nonce,
            }}
        else:
            operation = argv[argv.index("resource-relationship") + 1]
            result = owner(workspace, operation, json.loads(kwargs["input_text"]))
            result["writer_identity"] = dict(WRITER_IDENTITY)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    def admit(selected, identity, request):
        assert selected == workspace and identity == WRITER_IDENTITY
        retained = queue.task_relationship(task.id, event_id=request["event_id"])
        assert retained["request"] == request
        control.admissions.append(copy.deepcopy(request))
        if control.refuse:
            raise TaskError("original queue/cohort is unqualified")
        request["workspace"].clear()
        request["recovery"].clear()
        identity.clear()

    monkeypatch.setattr(module, "run_background_capture", capture)
    queue._relationship_writer = module.WorktreeRelationshipWriter(
        ["selected"], admit_workspace=lambda *a: None, admit_request=admit,
    )
    return queue, owner, workspace, task, control


def test_original_request_is_admitted_before_reserve_and_recovered_without_recapture(request_assignment):
    queue, owner, workspace, task, control = request_assignment
    control.refuse = True
    with pytest.raises(TaskError, match="unresolved"):
        claim(queue, workspace, task)
    original = queue.task_relationship(task.id)
    assert original["state"] == "pending" and owner.calls == []
    assert len(control.captures) == 1
    control.refuse = False
    recovered = TaskQueue(
        queue.db_path, relationship_writer=queue._relationship_writer,
        relationship_authority="local",
    )
    claim(recovered, workspace, task)
    assert recovered.task_relationship(task.id)["request"] == original["request"]
    assert control.admissions == [original["request"]] * 3
    assert [operation for operation, _ in owner.calls] == ["reserve", "bind"]


@pytest.mark.parametrize("operation", ["reserve", "bind"])
def test_request_bound_unknown_response_replays_same_request_and_retires(request_assignment, operation):
    queue, owner, workspace, task, control = request_assignment
    owner.lose_reply = operation
    with pytest.raises(TaskError, match="unresolved"):
        claim(queue, workspace, task)
    original = queue.task_relationship(task.id)["request"]
    assert queue.task_relationship(task.id)["state"] == "pending"
    claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert all(request == original for request in control.admissions)
    assert len(owner.claims) == 1


def test_request_bound_retirement_tombstones_absence_under_original_admission(request_assignment):
    queue, owner, workspace, task, control = request_assignment
    control.refuse = True
    with pytest.raises(TaskError):
        claim(queue, workspace, task)
    original = queue.task_relationship(task.id)["request"]
    queue.yield_task(task.id, workspace.worker_id)
    control.refuse = False
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert [operation for operation, _ in owner.calls] == ["inspect", "reserve", "close"]
    assert control.admissions == [original] * 4
    assert len(owner.claims) == 1


def test_request_bound_submission_rejection_resubmission_keeps_same_native_and_relationship(
    request_assignment,
):
    queue, owner, workspace, task, control = request_assignment
    claim(queue, workspace, task)
    queue.start(task.id, workspace.worker_id, owner_session_id="original-native")
    original = queue.task_relationship(task.id)["request"]
    for _ in range(2):
        queue.complete(task.id, workspace.worker_id, result={"answer": "proposed"})
        submitted = queue.get(task.id)
        assert submitted.status == Status.SUBMITTED
        assert queue.reconcile_task_relationship(task.id)["state"] == "bound"
        queue.reject_submission(
            task.id, reason="revise", expected_generation=submitted.generation,
            expected_owner_session_id=submitted.owner_session_id,
            expected_completed_by=submitted.completed_by, expected_updated_at=submitted.updated_at,
        )
        current = queue.get(task.id)
        assert current.status == Status.STARTED
        assert current.owner == workspace.worker_id and current.owner_session_id == "original-native"
        assert queue.task_relationship(task.id)["request"] == original
    queue.complete(task.id, workspace.worker_id, result={"answer": "accepted"})
    queue.confirm(task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert all(request == original for request in control.admissions)
    assert [operation for operation, _ in owner.calls] == ["reserve", "bind", "inspect", "close"]
    assert len(owner.claims) == 1


def test_request_admission_does_not_replace_post_io_assignment_checks(request_assignment):
    queue, owner, workspace, task, control = request_assignment

    def unassign(operation, result):
        if operation == "bind":
            queue.yield_task(task.id, workspace.worker_id)

    owner.before_reply = unassign
    with pytest.raises(TaskError, match="changed during"):
        claim(queue, workspace, task)
    original = queue.task_relationship(task.id)["request"]
    assert queue.get(task.id).status == Status.QUEUED
    assert queue.task_relationship(task.id)["state"] == "pending"
    owner.before_reply = None
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    assert all(request == original for request in control.admissions)


@pytest.mark.parametrize("operation", ["bind", "close"])
def test_bound_request_refuses_another_token_after_exact_inspection(request_assignment, operation):
    queue, owner, workspace, task, control = request_assignment
    claim(queue, workspace, task)
    original = queue.task_relationship(task.id)["request"]
    writer = queue._relationship_writer.bind_request(original)
    writer(workspace, "inspect", {"request_id": original["request_id"], "allow_missing": True})
    before = len(owner.calls), len(control.admissions)
    payload = {"token": "foreign-token"}
    if operation == "bind":
        payload["identity"] = original["identity"]
    with pytest.raises(TaskError, match="conflicts"):
        writer(workspace, operation, payload)
    assert (len(owner.calls), len(control.admissions)) == before
    assert queue.task_relationship(task.id)["state"] == "bound"
