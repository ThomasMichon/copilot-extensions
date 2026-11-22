"""Original-owner HTTP closeout remains separate from generic task authority."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Protocol, TypedDict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_dispatch.assignment_workspace import AssignmentWorkspace, RelationshipWriter
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import Status, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests import test_task_relevance_closeout_ack as acknowledgements
from tests import test_task_relevance_closeout_readiness as readiness
from tests._helpers import required
from tests.test_assignment_relationships import GroundOwner, start_managed
from tests.test_assignment_relationships import independent_readiness as independent_readiness
from tests.test_task_relevance import report
from tests.test_task_relevance_binding import Assignment, _scope

managed_assignment = readiness.managed_assignment
assignment = readiness.assignment
ready = readiness.ready
finalized = acknowledgements.finalized


Json = dict[str, object]


class ReportClosure(Protocol):
    def __call__(self, queue: TaskQueue, task_id: str, /, *, scope: RelevanceScope) -> object: ...


start_worker: Callable[[TaskQueue, AssignmentWorkspace, Task], Task] = start_managed
report_closure: ReportClosure = report


class BoundGroundOwner:
    def __init__(self, owner: GroundOwner) -> None:
        self.owner = owner

    def __call__(self, workspace: AssignmentWorkspace, operation: str, payload: Json) -> Json:
        raise AssertionError("closeout writer invoked without binding the request")

    def bind_request(
        self, _: Json, *, authority_guard: Callable[[], None] | None = None,
    ) -> RelationshipWriter:
        def write(workspace: AssignmentWorkspace, operation: str, payload: Json) -> Json:
            if authority_guard is not None:
                authority_guard()
            return self.owner(workspace, operation, payload)

        return write


def api(value: readiness.Ready, *, wake_is_active: Callable[[], bool] | None = None) -> TestClient:
    if isinstance(value[0]._relationship_writer, GroundOwner):
        value[0]._relationship_writer = BoundGroundOwner(value[1])
    return TestClient(
        create_app(
            value[0],
            token="data",
            control_token="control",
            enable_mcp=False,
            verification_interval=0,
            wake_is_active=wake_is_active,
        )
    )


def path(value: readiness.Ready, suffix: str = "") -> str:
    return f"/tasks/{value[3].id}/relevance-closeout{suffix}"


def body(value: readiness.Ready) -> Json:
    return {
        "version": 1,
        "request_id": "closed-source",
        "worker_id": value[2].worker_id,
        "native_id": "native",
        "expected_generation": required(value[0].get(value[3].id)).generation,
    }


class ScopeOverrides(TypedDict, total=False):
    task_id: str
    worker_id: str
    owner_session_id: str
    generation: int
    now: float


def headers(
    value: readiness.Ready, *, task_id: str | None = None, worker_id: str | None = None,
    owner_session_id: str = "native", generation: int | None = None, now: float | None = None,
) -> dict[str, str]:
    current = required(value[0].get(value[3].id))
    token = mint_task_scope_token(
        "control",
        task_id=current.id if task_id is None else task_id,
        worker_id=value[2].worker_id if worker_id is None else worker_id,
        owner_session_id=owner_session_id,
        generation=current.generation if generation is None else generation,
        epoch=0,
        now=now,
    )
    return {TASK_SCOPE_HEADER: token}


def test_original_owner_prepares_without_abandoning_or_releasing_native(
    ready: readiness.Ready,
) -> None:
    client = api(ready)
    auth = headers(ready)
    before = required(ready[0].get(ready[3].id))
    response = client.post(path(ready, "/prepare"), json=body(ready), headers=auth)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["protocol"] == "task_relevance_closeout/v1"
    assert result["result"]["relationship"]["state"] == "closed"
    assert required(ready[0].get(ready[3].id)) == before
    assert not required(ready[0].latest_reservation(before.id)).release_requested
    assert client.get(path(ready), headers=auth).json()["result"]["readiness"]
    assert client.get("/health", headers=auth).status_code == 403


def test_finish_and_lost_reply_replay_emit_terminal_hooks_once(
    finalized: readiness.Ready,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import handoff_claim_release, pool_decline_observation

    client = api(finalized)
    auth, arguments = headers(finalized), body(finalized)
    calls: list[tuple[Json, bool]] = []
    events: list[Json] = []
    monkeypatch.setattr(
        handoff_claim_release,
        "release_if_handoff",
        lambda task, *, cancel_pending, queue: calls.append((task, cancel_pending)),
    )
    assert isinstance(client.app, FastAPI)
    monkeypatch.setattr(client.app.state.bus, "publish", events.append)
    first = client.post(path(finalized, "/finish"), json=arguments, headers=auth)
    assert first.status_code == 200, first.text
    assert first.json()["result"]["status"] == Status.ABANDONED
    assert set(first.json()["result"]) == {"id", "status", "generation"}

    def unexpected(*_: object) -> Json:
        raise AssertionError("terminal retries must not reobserve the workspace")

    monkeypatch.setattr(pool_decline_observation, "observe", unexpected)
    restarted = api(finalized)
    second = restarted.post(path(finalized, "/finish"), json=arguments, headers=auth)
    assert second.status_code == 200 and second.json() == first.json()
    assert restarted.get(path(finalized), headers=auth).json()["result"]["acknowledgement"]
    assert len(calls) == 1 and calls[0][1] is True
    assert [event["type"] for event in events] == ["task.abandoned"]
    assert not required(finalized[0].latest_reservation(finalized[3].id)).release_requested
    for suffix in ("", "/payload"):
        assert restarted.get(f"/tasks/{finalized[3].id}{suffix}", headers=auth).status_code == 403
    assert (
        restarted.post(
            f"/tasks/{finalized[3].id}/abandon",
            headers=auth,
            json={"worker_id": arguments["worker_id"]},
        ).status_code
        == 403
    )


@pytest.mark.parametrize("submitted", [False, True])
def test_captured_owner_can_close_out_unassigned_or_submitted_work(
    assignment: Assignment,
    independent_readiness: None,
    monkeypatch: pytest.MonkeyPatch,
    submitted: bool,
) -> None:
    from agent_dispatch import pool_decline_observation, submission_readiness_observation

    queue, owner, workspace, task = assignment
    start_worker(queue, workspace, task)
    if submitted:
        queue.complete(task.id, workspace.worker_id, result={"done": True})
        assert required(queue.get(task.id)).status == Status.SUBMITTED
    report_closure(queue, task.id, scope=_scope(queue, task.id))
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    monkeypatch.setattr(
        pool_decline_observation,
        "observe",
        lambda *_: {"status": "finalized", "creation_nonce": workspace.creation_nonce},
    )
    value: readiness.Ready = queue, owner, workspace, task, []
    auth, client = headers(value), api(value)
    readiness.prepare(queue, workspace, task)
    if not submitted:
        with pytest.raises(TaskError, match="retains assignment"):
            queue.yield_task(task.id, workspace.worker_id, release_spawn=False)
        # Older writers could yield here. Keep covering the historical
        # unassigned case without allowing a new writer to produce it.
        with queue._connect() as conn:
            conn.execute("UPDATE tasks SET status='queued', owner=NULL, owner_session_id=NULL "
                         "WHERE id=?", (task.id,))
    assert client.get(path(value), headers=auth).status_code == 200
    assert client.get(f"/tasks/{task.id}", headers=auth).status_code == 403
    response = client.post(path(value, "/finish"), json=body(value), headers=auth)
    assert response.status_code == 200, response.text
    assert set(response.json()["result"]) == {"id", "status", "generation"}


@pytest.mark.parametrize("boundary", ["readiness", "claim", "writer-record", "finalization"])
@pytest.mark.parametrize("invalidation", ["epoch", "expiry"])
def test_authority_is_rechecked_after_independent_owner_io(
    ready: readiness.Ready,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    invalidation: str,
) -> None:
    from agent_dispatch import (
        pool_decline_observation,
        submission_readiness_observation,
        task_closeout_authority,
    )

    class Guard:
        floor = 0

        def is_valid(self, epoch: int) -> bool:
            return epoch >= self.floor

    guard = Guard()
    queue, owner, workspace, task, _ = ready
    client, auth, arguments = api(ready), headers(ready), body(ready)
    assert isinstance(client.app, FastAPI)
    client.app.state.task_fencing_guard = guard
    if boundary == "finalization":
        assert (
            client.post(path(ready, "/prepare"), json=arguments, headers=auth).status_code == 200
        )
    before_closes = sum(operation == "close" for operation, _ in owner.calls)

    def invalidate() -> None:
        if invalidation == "epoch":
            guard.floor = 1
        else:
            expired = time.time() + 1800
            monkeypatch.setattr(
                task_closeout_authority, "time", SimpleNamespace(time=lambda: expired),
            )

    if boundary == "readiness":
        observe = submission_readiness_observation.observe

        def observe_readiness(request: Json, allocation: Json, claim_ref: str) -> Json:
            result = observe(request, allocation, claim_ref)
            invalidate()
            return result

        monkeypatch.setattr(submission_readiness_observation, "observe", observe_readiness)
    elif boundary == "claim":
        def before_reply(operation: str, _: Json) -> None:
            if operation == "inspect":
                invalidate()

        owner.before_reply = before_reply
    elif boundary == "writer-record":
        from agent_dispatch import assignment_relationship_owner
        from tests.test_assignment_relationship_owner import WRITER_IDENTITY, support

        records: list[list[str]] = []

        def capture(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
            if "capabilities" in argv:
                result = support()
            elif "record" in argv:
                records.append(argv)
                if len(records) == 2:
                    invalidate()
                result = {"ok": True, "worktree": {
                    "id": workspace.worktree, "repo": workspace.project,
                    "path": workspace.path, "creation_nonce": workspace.creation_nonce,
                }}
            else:
                operation = argv[argv.index("resource-relationship") + 1]
                input_text = options["input_text"]
                assert isinstance(input_text, str)
                result = owner(workspace, operation, json.loads(input_text))
                result["writer_identity"] = dict(WRITER_IDENTITY)
            return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

        monkeypatch.setattr(assignment_relationship_owner, "run_background_capture", capture)
        def admit(_workspace: AssignmentWorkspace, _identity: Json, original: Json) -> None:
            queue._admit_original_relationship_request(original)

        queue._relationship_writer = assignment_relationship_owner.WorktreeRelationshipWriter(
            ["selected"], admit_workspace=lambda *_: None,
            admit_request=admit,
        )
    else:
        def observe_finalized(*_: object) -> Json:
            invalidate()
            return {"status": "finalized", "creation_nonce": workspace.creation_nonce}

        monkeypatch.setattr(pool_decline_observation, "observe", observe_finalized)

    response = client.post(
        path(ready, "/finish" if boundary == "finalization" else "/prepare"),
        json=arguments,
        headers=auth,
    )
    assert response.status_code == (403 if invalidation == "epoch" else 401), response.text
    assert required(queue.get(task.id)).status == Status.STARTED
    assert not required(queue.latest_reservation(task.id)).release_requested
    assert sum(operation == "close" for operation, _ in owner.calls) == before_closes
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    assert closeout.get("acknowledgement") is None
    if boundary == "readiness":
        assert closeout.get("readiness") is None


def test_scoped_finish_and_replay_never_return_generic_task_content(
    finalized: readiness.Ready,
) -> None:
    queue, _, _, task, _ = finalized
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET payload_inline=?, result=?, card_draft=? WHERE id=?",
            ('{"private":"payload"}', '{"private":"result"}', '{"private":"card"}', task.id),
        )
    client, arguments = api(finalized), body(finalized)
    scheme = "Be" + "arer"
    trusted = client.post(
        path(finalized, "/finish"),
        json=arguments,
        headers={"Authorization": scheme + " data"},
    )
    assert trusted.status_code == 200, trusted.text
    assert all(
        key in trusted.json()["result"] for key in ("payload_inline", "result", "card_draft")
    )
    scoped = client.post(path(finalized, "/finish"), json=arguments, headers=headers(finalized))
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["result"] == {
        "id": task.id, "status": Status.ABANDONED, "generation": arguments["expected_generation"],
    }


def test_opaque_writer_cannot_silently_drop_closeout_authority(
    ready: readiness.Ready,
) -> None:
    client = api(ready)
    ready[0]._relationship_writer = ready[1]
    closes = sum(operation == "close" for operation, _ in ready[1].calls)
    response = client.post(path(ready, "/prepare"), json=body(ready), headers=headers(ready))
    assert response.status_code == 200, response.text
    result = response.json()["result"]["relationship"]
    assert result["state"] == "retained" and "request-bound closeout authority" in result["error"]
    assert sum(operation == "close" for operation, _ in ready[1].calls) == closes
    assert required(ready[0].task_relationship(ready[3].id))["state"] == "bound"


@pytest.mark.parametrize(
    "change",
    [
        {"task_id": "foreign"},
        {"worker_id": "foreign/worker"},
        {"owner_session_id": "foreign"},
        {"generation": 99},
        {"now": time.time() - 1800},
    ],
)
def test_scoped_closeout_refuses_foreign_stale_or_expired_authority(
    ready: readiness.Ready,
    change: ScopeOverrides,
) -> None:
    response = api(ready).post(
        path(ready, "/prepare"), json=body(ready), headers=headers(ready, **change)
    )
    assert response.status_code in {401, 403}
    assert not ready[4]
    assert required(ready[0].task_relationship(ready[3].id))["state"] == "bound"


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "foreign"),
        ("worker_id", "foreign/worker"),
        ("native_id", "foreign"),
        ("expected_generation", 99),
    ],
)
def test_body_cannot_override_captured_scope(
    ready: readiness.Ready,
    field: str,
    value: str | int,
) -> None:
    response = api(ready).post(
        path(ready, "/prepare"),
        json={**body(ready), field: value},
        headers=headers(ready),
    )
    assert response.status_code == 403
    assert not ready[4]


@pytest.mark.parametrize("change", ["hold", "generation", "native", "reservation"])
def test_changed_live_custody_refuses_even_a_valid_original_token(
    ready: readiness.Ready,
    change: str,
) -> None:
    auth = headers(ready)
    with ready[0]._connect() as conn:
        if change == "hold":
            conn.execute("UPDATE tasks SET hold_reason='operator' WHERE id=?", (ready[3].id,))
        elif change == "generation":
            conn.execute(
                "UPDATE task_relevance_closeouts SET generation=generation+1 WHERE task_id=?",
                (ready[3].id,),
            )
        elif change == "native":
            conn.execute("UPDATE tasks SET owner_session_id='foreign' WHERE id=?", (ready[3].id,))
        else:
            conn.execute(
                "UPDATE spawn_reservations SET release_requested=1 WHERE task_id=?", (ready[3].id,)
            )
    assert api(ready).get(path(ready), headers=auth).status_code == 403
    assert not ready[4]


@pytest.mark.parametrize("credential", ["data", "control"])
def test_existing_trusted_bearers_can_use_the_conditional_provider(
    ready: readiness.Ready,
    credential: str,
) -> None:
    scheme = "Be" + "arer"
    auth = {"Authorization": scheme + " " + credential}
    assert auth["Authorization"].split()[1] == credential
    response = api(ready).post(path(ready, "/prepare"), json=body(ready), headers=auth)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"version": True},
        {"expected_generation": True},
        {"receipt": {"finalized": True}},
    ],
)
def test_strict_wire_cannot_supply_proof_or_guess_a_version(
    ready: readiness.Ready,
    change: Json,
) -> None:
    response = api(ready).post(
        path(ready, "/prepare"),
        json={**body(ready), **change},
        headers=headers(ready),
    )
    assert response.status_code == 422
    assert not ready[4]


def test_drain_and_superseded_epoch_preserve_custody(ready: readiness.Ready) -> None:
    class Guard:
        def is_valid(self, epoch: int) -> bool:
            return epoch >= 1

    client = api(ready)
    assert isinstance(client.app, FastAPI)
    client.app.state.task_fencing_guard = Guard()
    assert client.get(path(ready), headers=headers(ready)).status_code == 403
    client.app.state.task_fencing_guard = None
    client.app.state.drain_gate.set_draining(True)
    response = client.post(path(ready, "/prepare"), json=body(ready), headers=headers(ready))
    assert response.status_code == 409 and "draining" in response.text
    assert not ready[4]


def test_no_credentials_or_invalid_scoped_header_cannot_fall_back(ready: readiness.Ready) -> None:
    client = api(ready)
    assert client.get(path(ready)).status_code == 401
    scheme = "Be" + "arer"
    auth = {"Authorization": scheme + " data", TASK_SCOPE_HEADER: "invalid"}
    assert client.get(path(ready), headers=auth).status_code == 401


def test_finish_does_not_treat_preparation_as_finalization(
    ready: readiness.Ready,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation

    def blocked(*_: object) -> Json:
        raise TaskError("workspace is not finalized")

    client, auth = api(ready), headers(ready)
    monkeypatch.setattr(pool_decline_observation, "observe", blocked)
    assert client.post(path(ready, "/prepare"), json=body(ready), headers=auth).status_code == 200
    response = client.post(path(ready, "/finish"), json=body(ready), headers=auth)
    assert response.status_code == 409 and "not finalized" in response.text
    assert required(ready[0].get(ready[3].id)).status == Status.STARTED


def test_passive_coordinator_refuses_closeout_before_observation(ready: readiness.Ready) -> None:
    client = api(ready, wake_is_active=lambda: False)
    response = client.post(path(ready, "/prepare"), json=body(ready), headers=headers(ready))
    assert response.status_code == 503
    assert not ready[4]


def test_terminal_retry_cannot_cross_a_later_task_change(finalized: readiness.Ready) -> None:
    client, auth, arguments = api(finalized), headers(finalized), body(finalized)
    assert client.post(path(finalized, "/finish"), json=arguments, headers=auth).status_code == 200
    with finalized[0]._connect() as conn:
        conn.execute("UPDATE tasks SET updated_at=updated_at+1 WHERE id=?", (finalized[3].id,))
    response = client.post(path(finalized, "/finish"), json=arguments, headers=auth)
    assert response.status_code == 403


def test_serving_generation_advertises_additive_closeout_not_package_inference(
    ready: readiness.Ready,
) -> None:
    scheme = "Be" + "arer"
    response = api(ready).get("/health", headers={"Authorization": scheme + " data"})
    assert response.status_code == 200
    contract = response.json()["task_relevance"]
    assert contract["protocol"] == "task_relevance/v1" and contract["bounded_batches"]
    assert contract["owner_closeout"] == {
        "protocol": "task_relevance_closeout/v1",
        "supported_versions": [1],
        "path": "/tasks/{task_id}/relevance-closeout",
    }
