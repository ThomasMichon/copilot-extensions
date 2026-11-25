"""Use the real original-worker client and coordinator, with synthetic Worktrees I/O."""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import procutil, relevance_closeout_cli, remote_dispatch
from agent_dispatch.client import DispatchClient
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_relevance import RelevanceScope
from agent_dispatch.review_closeout_submission import SUBMIT
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests.test_pool_placement import pool, publish
from tests.test_pool_suspended_recovery import _disable_pools
from tests.test_task_relevance import request as relevance_request


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "old-closeout",
        "withdrawn-pool",
        "withdrawn-wrong-generation",
        "worker",
        "task",
        "native",
        "generation",
        "conflict",
        "retarget-before-submit",
        "foreign-stamp",
        "agent-replaced",
        "lost-finalize",
        "lost-finalize-retry",
        "old-finalize-receipt",
        "ambiguous-finish",
        "no-scope",
    ],
)
def test_original_reservation_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    q = TaskQueue(tmp_path / "queue.db")
    task = q.create(
        "Review",
        repo="example",
        require_verification=True,
        labels=["pr-review"],
        source="emitter",
        origin_ref="reviews",
        evaluator_ref="review-evaluator",
    )
    assert isinstance(task, Task)
    worker, native, machine, tree = "host-a/tree", "native", "host-a", "tree"
    cwd = tmp_path / tree
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    revision = publish(q, pool())
    r, acquired = q.reserve_spawn(
        task.id, reserved_by="allocator", driver="ordinary-bridge", pool_catalog_revision=revision
    )
    assert acquired
    stamp: dict[str, Any] = {
        "task_id": task.id,
        "reservation_key": r.key,
        "attempt": r.attempt,
        "driver": "ordinary-bridge",
        "supervisor": "allocator",
        "creator_machine": machine,
        "ownership": "created",
    }
    if scenario.startswith("withdrawn-"):
        q.record_spawn_allocation(r.key, "example", cohort_transport="cohort-v1")
        assert r.allocation_cohort and r.allocation_cohort["pool"]
        stamp["cohort"] = r.allocation_cohort
    q.record_spawn_worktree(
        r.key,
        tree,
        ownership="created",
        creating_host=machine,
        execution_machine=machine,
        driver="ordinary-bridge",
        allocation_project="example",
        worktree_path=str(cwd),
    )
    info = {
        "ready": True,
        "status": "awaiting_first_prompt",
        "session_id": "original-bridge",
        "task_generation": task.generation,
        "connection": {
            "native_session_id": native,
            "repo": "example",
            "worktree_id": tree,
            "target_dir": str(cwd),
        },
    }
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET agent_info=? WHERE key=?", (json.dumps(info), r.key)
        )
    q.record_spawn(
        r.key,
        session_handle="local-body:original-bridge",
        startup_complete=True,
        startup_revision=1,
    )
    live = q.start(
        task.id, worker, owner_session_id=native, expected_generation=task.generation + 1
    )
    q.suspend(task.id, worker, reason="awaiting review disposition")
    scope = RelevanceScope("example", "emitter", "reviews")
    q.reconcile_task_relevance(
        task.id, **cast(Callable[..., dict[str, Any]], relevance_request)(q, task.id, scope=scope)
    )
    wake = q.claim_due_wake()
    assert wake and wake.delivery_token and wake.owner_session_id == native
    q.finish_wake(wake.id, wake.delivery_token, delivered=True)
    orig = q.task_pool_assignment(task.id)
    reservation = q.get_reservation(r.key)
    assert (
        orig
        and q.task_relationship(task.id) is None
        and reservation
        and not reservation.creation_nonce
    )

    token = mint_task_scope_token(
        "control",
        task_id="other" if scenario == "task" else task.id,
        worker_id="host-a/else" if scenario == "worker" else worker,
        owner_session_id="other" if scenario == "native" else native,
        generation=live.generation + (scenario == "generation"),
        epoch=0,
    )
    done: list[str] = []

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        contract = "agent-worktrees.dispatch-worker-closeout"
        result: dict[str, Any]
        if "--cwd" in argv:
            result = {
                "ok": True,
                "context": {
                    "schema": "agent-worktrees.workspace-context",
                    "version": 1,
                    "project": "example",
                    "anchor": str(tmp_path / "example"),
                    "root": str(cwd),
                    "identity": None,
                    "worktree": {
                        "id": tree,
                        "repo": "example",
                        "path": str(cwd),
                        "status": "active",
                        "checkout_managed": True,
                        "creation_nonce": "workspace-nonce",
                        "dispatch_attempt_opaque": False,
                        "dispatch_attempt": (
                            {**stamp, "reservation_key": "foreign"}
                            if scenario == "foreign-stamp"
                            else stamp
                        ),
                        "head": {
                            "version": 1,
                            "worktree_id": tree,
                            "tracked": True,
                            "active": True,
                            "state": "active",
                            "head_session": native,
                            "head_revision": 5,
                            "pending_handoffs": [],
                        },
                    },
                },
            }
        elif "--describe-closeout-contract" in argv:
            result = {
                "contract": contract,
                "version": 1,
                "own_finalization_cli_versions": [1],
                "paired_closeout": False,
                "task_release": False,
                "process_retirement": False,
            }
        else:
            finalizing = "finalize" in argv
            if finalizing:
                assert argv[argv.index("--dispatch-reservation-key") + 1] == r.key
                assert q.task_relationship(task.id) is None
                done.append("finalized")
                if scenario == "lost-finalize" or (
                    scenario == "lost-finalize-retry" and len(done) == 1
                ):
                    return subprocess.CompletedProcess(argv, 1, "{}", "")
            else:
                assert "--closeout-task" in argv and done
                if scenario == "retarget-before-submit":
                    with q._connect() as conn:
                        conn.execute(
                            "UPDATE task_pool_assignments SET reservation_key='other'"
                            " WHERE task_id=?",
                            (task.id,),
                        )
            result = {
                "ok": True,
                "contract": contract,
                "version": 1,
                "operation": ("finalize-own" if finalizing else "observe-finalized"),
                "task_id": task.id,
                "reservation_key": r.key,
                "worktree_id": tree,
                "project": "example",
                "path": str(cwd),
                "status": "finalized",
                "checkout_retained": True,
                "task_release": False,
                "process_retirement": False,
                "head_revision": 5,
                "machine": machine,
                "native_id": native,
                "creation_nonce": "changed"
                if not finalizing and scenario == "ambiguous-finish"
                else "workspace-nonce",
                "allocation": stamp,
                "checkout": {"branch": "worktree/tree", "head": "a" * 40},
            }
            if finalizing and scenario == "old-finalize-receipt":
                del result["creation_nonce"]
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(procutil, "run_background_capture", run)
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: ["fixture"])
    monkeypatch.setattr(procutil, "agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: machine)
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", native)
    if scenario.startswith("withdrawn-"):
        _disable_pools(q)
        assert not q.pool_catalog_snapshot().definitions
        assert q.task_pool_assignment(task.id) == orig
        if scenario == "withdrawn-wrong-generation":
            with q._connect() as conn:
                conn.execute(
                    "UPDATE task_pool_assignments SET task_generation=task_generation+1"
                    " WHERE task_id=?",
                    (task.id,),
                )
    if scenario == "old-closeout":
        with q._connect() as conn:
            conn.execute(
                "UPDATE task_relevance_closeouts SET resource_binding=NULL WHERE task_id=?",
                (task.id,),
            )
    if scenario == "conflict":
        with q._connect() as conn:
            conn.execute(
                "UPDATE task_pool_assignments SET reservation_key='other' WHERE task_id=?",
                (task.id,),
            )
    if scenario == "agent-replaced":
        with q._connect() as conn:
            conn.execute(
                "UPDATE spawn_reservations SET session_handle='local-body:other' WHERE key=?",
                (r.key,),
            )
    with TestClient(
        create_app(
            q, token="data", control_token="control", enable_mcp=False, verification_interval=0
        )
    ) as api:

        def send(req: httpx.Request) -> httpx.Response:
            assert (
                req.headers.get(TASK_SCOPE_HEADER) == token and "authorization" not in req.headers
            )
            response = api.request(
                req.method, req.url.path, headers=dict(req.headers), content=req.content
            )
            return httpx.Response(response.status_code, content=response.content)

        if scenario == "no-scope":
            response = api.get(
                f"/tasks/{task.id}/relevance-closeout",
                headers={"Authorization": "Bearer data", "X-Dispatch-Closeout-Action": SUBMIT},
            )
            assert response.status_code == 403
            return
        with DispatchClient(
            "http://fixture", task_token=token, transport=httpx.MockTransport(send)
        ) as client:
            if scenario == "lost-finalize-retry":
                with pytest.raises(DispatchError):
                    relevance_closeout_cli.closeout(client, task.id, worker)
                assert not q.list_verification_requests(task.id)
            if scenario not in (
                "success",
                "old-closeout",
                "withdrawn-pool",
                "lost-finalize-retry",
            ):
                with pytest.raises(DispatchError):
                    relevance_closeout_cli.closeout(client, task.id, worker)
                remaining = q.get(task.id)
                assert remaining and remaining.status == "suspended"
                assert not q.list_verification_requests(task.id)
                assert not done or scenario in (
                    "lost-finalize",
                    "old-finalize-receipt",
                    "ambiguous-finish",
                    "retarget-before-submit",
                )
                return
            outcome = relevance_closeout_cli.closeout(client, task.id, worker)
            assert (
                outcome["status"] == "submitted" and outcome["task_relationship"] == "not-required"
            )
            assert relevance_closeout_cli.closeout(client, task.id, worker) == outcome
            # Lost finish HTTP response: replay the same authenticated POST, not finalization.
            ack = client.relevance_closeout(task.id)["acknowledgement"]
            again = client.finish_relevance_closeout(
                task.id, {**ack["arguments"], "finish_action": SUBMIT}
            )
            assert again == {"id": task.id, "status": "submitted", "generation": live.generation}
    assert len(done) == (2 if scenario == "lost-finalize-retry" else 1)
    assert q.task_pool_assignment(task.id) == orig
    submitted, retained = q.get(task.id), q.get_reservation(r.key)
    assert (
        submitted
        and submitted.completed_by == worker
        and len(q.list_verification_requests(task.id)) == 1
    )
    assert q.task_relationship(task.id) is None and retained and retained.state == "spawned"
