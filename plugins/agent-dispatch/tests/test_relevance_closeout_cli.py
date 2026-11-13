"""Actual task-only parser, pinned HTTP and queue; only ground-owner IPC is synthetic."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch import pool_worker_decline_cli, procutil, remote_dispatch
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.client import DispatchClient
from agent_dispatch.queue import Task, TaskQueue
from agent_dispatch.task_scope import TASK_SCOPE_HEADER
from agent_dispatch.task_worker_cli import TaskWorker
from tests import test_task_relevance_closeout_transport as provider
from tests.test_assignment_relationships import independent_readiness as independent_readiness

managed_assignment = provider.managed_assignment
assignment = provider.assignment


@dataclass
class Caller:
    queue: TaskQueue
    workspace: AssignmentWorkspace
    task: Task
    allocation: dict
    api: Any
    requests: list[httpx.Request] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    finalized: bool = False
    final_nonce: str | None = None
    rewrite: Any = None
    block_finalize: bool = False

    def run(self) -> int:
        return cli.main(["relevance-closeout", self.task.id])

    @property
    def posts(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.method == "POST"]


@pytest.fixture
def caller(
    assignment: provider.readiness.bindings.Assignment,
    monkeypatch: pytest.MonkeyPatch,
    independent_readiness: None,
    request: pytest.FixtureRequest,
) -> Caller:
    queue, owner, workspace, task = assignment
    workspace_path = workspace.path
    os.makedirs(workspace_path)
    monkeypatch.chdir(workspace_path)
    provider.readiness.start_managed(queue, workspace, task)
    state = getattr(request, "param", "")
    if state == "suspended":
        queue.suspend(task.id, workspace.worker_id, reason="waiting")
    elif state == "submitted":
        queue.complete(task.id, workspace.worker_id, result={"done": True})
    provider.readiness.report(
        queue, task.id, scope=provider.readiness.bindings._scope(queue, task.id)
    )
    if state == "unassigned":
        queue.yield_task(task.id, workspace.worker_id, release_spawn=False)
    reservation = queue.latest_reservation(task.id)
    allocation = {
        "task_id": task.id,
        "reservation_key": reservation.key,
        "attempt": reservation.attempt,
        "driver": reservation.driver,
        "supervisor": reservation.reserved_by,
        "creator_machine": workspace.machine,
        "ownership": "created",
    }
    value = Caller(
        queue, workspace, task, allocation, provider.api((queue, owner, workspace, task, []))
    )
    prefix = ["/selected/python", "-m", "selected.worktrees"]
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: prefix)
    monkeypatch.setattr(procutil, "agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: workspace.machine)
    # No ready fixture: both independent observations execute their real validators.
    relationship = queue.task_relationship(task.id)
    revision = 7

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess:
        value.commands.append(argv)
        if "--describe-closeout-contract" in argv:
            result = {
                "contract": pool_worker_decline_cli.CONTRACT,
                "version": 1,
                "own_finalization_cli_versions": [1],
                "paired_closeout": False,
                "task_release": False,
                "process_retirement": False,
            }
        elif "--describe-readiness-contract" in argv:
            result = {
                "contract": "agent-worktrees.dispatch-worker-readiness",
                "version": 1,
                "operations": ["observe-ready"],
                "platforms": ["linux", "wsl"],
                "native_proof": "inuse-hold-v1",
                "paired_readiness": False,
                "finalized": False,
                "task_release": False,
                "process_retirement": False,
            }
        elif "--cwd" in argv:
            result = {
                "ok": True,
                "context": {
                    "schema": "agent-worktrees.workspace-context",
                    "version": 1,
                    "project": workspace.project,
                    "root": workspace.path,
                    "worktree": {
                        "id": workspace.worktree,
                        "repo": workspace.project,
                        "path": workspace.path,
                        "creation_nonce": workspace.creation_nonce,
                        "status": "active",
                        "dispatch_attempt_opaque": False,
                        "dispatch_attempt": allocation,
                        "head": {
                            "version": 1,
                            "worktree_id": workspace.worktree,
                            "tracked": True,
                            "head_session": "native",
                            "active": True,
                            "state": "active",
                            "pending_handoffs": [],
                            "head_revision": revision,
                        },
                    },
                },
            }
        else:
            finalizing = "finalize" in argv
            observing = "--closeout-task" in argv
            if finalizing:
                assert queue.get(task.id).status != "abandoned"
                assert queue.task_relationship(task.id)["state"] == "closed"
                if value.block_finalize:
                    return subprocess.CompletedProcess(argv, 1, '{"error":"Open PR remains"}', "")
                assert (
                    argv[argv.index("--dispatch-reservation-key") + 1]
                    == allocation["reservation_key"]
                )
                value.finalized = True
            if observing:
                assert value.finalized
            result = {
                "ok": True,
                "contract": pool_worker_decline_cli.CONTRACT
                if finalizing or observing
                else "agent-worktrees.dispatch-worker-readiness",
                "version": 1,
                "operation": "finalize-own"
                if finalizing
                else "observe-finalized"
                if observing
                else "observe-ready",
                "task_id": task.id,
                "reservation_key": allocation["reservation_key"],
                "native_id": "native",
                "worktree_id": workspace.worktree,
                "creation_nonce": value.final_nonce or workspace.creation_nonce,
                "head_revision": revision,
                "project": workspace.project,
                "path": workspace.path,
                "machine": workspace.machine,
                "allocation": allocation,
                "checkout": {"head": "a" * 40, "branch": "worktree/test"},
                "checkout_retained": True,
                "task_release": False,
                "process_retirement": False,
            }
            if finalizing or observing:
                result["status"] = "finalized"
            else:
                result.update(
                    ready=True,
                    finalized=False,
                    task_claim_ref=relationship["receipt"]["ref"],
                    landing={"bases": ["dev"], "prs": []},
                )
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(procutil, "run_background_capture", run)
    monkeypatch.setattr(
        procutil,
        "run_agent_worktrees_capture",
        lambda *argv, **options: run([*prefix, *argv], **options),
    )
    auth = provider.headers((queue, owner, workspace, task, []))
    for name, item in {
        "COPILOT_AGENT_SESSION_ID": "native",
        "AGENT_DISPATCH_TASK_TOKEN": auth[TASK_SCOPE_HEADER],
        "AGENT_DISPATCH_TASK_ID": task.id,
        "AGENT_DISPATCH_TASK_URL": "http://coordinator.test",
        "AGENT_DISPATCH_TASK_WORKER_ID": workspace.worker_id,
        "AGENT_DISPATCH_TOKEN": "unused-fleet",
        "AGENT_DISPATCH_CONTROL_TOKEN": "unused-control",
    }.items():
        monkeypatch.setenv(name, item)

    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("task closeout attempted fleet discovery, identity or autostart")

    for name in (
        "_identity",
        "_ensure_local_coordinator",
        "_resolve_client_target",
        "_should_ssh_failover",
        "client_token",
        "client_control_token",
        "shared_control_token",
        "_enrich",
    ):
        monkeypatch.setattr(cli, name, forbidden)

    def send(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "coordinator.test"
        assert "authorization" not in request.headers
        assert request.headers[TASK_SCOPE_HEADER] == auth[TASK_SCOPE_HEADER]
        assert "/relevance-closeout" in request.url.path
        value.requests.append(request)
        response = value.api.request(
            request.method,
            request.url.path,
            content=request.content,
            headers=dict(request.headers),
        )
        result = httpx.Response(response.status_code, content=response.content)
        return value.rewrite(request, result) if value.rewrite else result

    monkeypatch.setattr(
        TaskWorker,
        "client",
        lambda self: DispatchClient(
            self.url, task_token=self.token, transport=httpx.MockTransport(send)
        ),
    )
    return value


def test_original_worker_finalizes_then_acknowledges_without_native_release(
    caller: Caller,
    capsys: pytest.CaptureFixture,
) -> None:
    assert caller.run() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["workspace_closeout"] == "finalized" and result["process_retirement"] is False
    assert caller.queue.get(caller.task.id).status == "abandoned"
    assert [request.url.path.rsplit("/", 1)[-1] for request in caller.posts] == [
        "prepare",
        "finish",
    ]
    assert all(request.extensions["timeout"]["read"] == 300 for request in caller.posts)
    assert not caller.queue.latest_reservation(caller.task.id).release_requested
    assert caller.queue.relevance_snapshot(caller.task.id)["closeout"]["acknowledgement"]
    commands, posts = len(caller.commands), len(caller.posts)
    assert caller.run() == 0
    assert len(caller.commands) == commands and len(caller.posts) == posts


@pytest.mark.parametrize("stage", ["prepare", "finish"])
def test_lost_reply_converges_through_original_request(
    caller: Caller,
    stage: str,
    capsys: pytest.CaptureFixture,
) -> None:
    def lose(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/" + stage):
            caller.rewrite = None
            raise httpx.ReadTimeout("reply lost", request=request)
        return response

    caller.rewrite = lose
    assert caller.run() == 1
    capsys.readouterr()
    before_commands, before_posts = len(caller.commands), len(caller.posts)
    assert caller.run() == 0
    assert caller.queue.get(caller.task.id).status == "abandoned"
    if stage == "finish":
        assert (len(caller.commands), len(caller.posts)) == (before_commands, before_posts)
    else:
        assert sum("finalize" in command for command in caller.commands) == 1


def test_unknown_claim_close_does_not_finalize_until_original_receipt_converges(
    caller: Caller,
) -> None:
    caller.queue._relationship_writer.owner.lose_reply = "close"
    assert caller.run() == 1
    assert not caller.finalized
    assert caller.queue.get(caller.task.id).status == "started"
    assert caller.run() == 0
    assert caller.finalized


def test_caller_uses_readiness_original_allocation_not_execution_key(caller: Caller) -> None:
    def continuation(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        value = response.json()
        result = value["result"]
        binding = result.get("resource_binding")
        if binding is not None:
            binding["reservation_key"] = "later-execution"
        receipt = result.get("receipt")
        if receipt is not None:
            receipt["binding"]["reservation_key"] = "later-execution"
        return httpx.Response(response.status_code, json=value)

    caller.rewrite = continuation
    assert caller.run() == 0
    finalizer = next(command for command in caller.commands if "finalize" in command)
    assert (
        finalizer[finalizer.index("--dispatch-reservation-key") + 1]
        == caller.allocation["reservation_key"]
    )


@pytest.mark.parametrize("change", ["native", "cwd", "worker", "missing-native"])
def test_wrong_actual_caller_has_no_mutations(
    caller: Caller,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    change: str,
) -> None:
    if change == "native":
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "foreign")
    elif change == "missing-native":
        monkeypatch.delenv("COPILOT_AGENT_SESSION_ID")
    elif change == "worker":
        monkeypatch.setenv("AGENT_DISPATCH_TASK_WORKER_ID", "host-a/foreign")
    else:
        monkeypatch.chdir(tmp_path)
    assert caller.run() == 1
    assert not caller.posts and not caller.commands


@pytest.mark.parametrize("failure", ["legacy", "malformed", "version", "rollback"])
def test_unsupported_provider_is_pre_effect_and_has_no_fallback(
    caller: Caller, failure: str
) -> None:
    reads = 0

    def unsupported(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        nonlocal reads
        reads += 1
        if failure == "rollback" and reads == 1:
            return response
        if failure == "legacy" or failure == "rollback":
            return httpx.Response(404, json={"detail": "Not Found"})
        if failure == "malformed":
            return httpx.Response(200, content=b"not json")
        value = response.json()
        value["version"] = True
        return httpx.Response(200, json=value)

    caller.rewrite = unsupported
    assert caller.run() == 1
    assert not caller.posts and not caller.commands


@pytest.mark.parametrize("caller", ["suspended", "submitted", "unassigned"], indirect=True)
def test_retained_owner_closes_out_without_resuming_original_work(
    caller: Caller,
) -> None:
    assert caller.run() == 0
    assert caller.queue.get(caller.task.id).status == "abandoned"
    assert not caller.queue.latest_reservation(caller.task.id).release_requested


@pytest.mark.parametrize("block", ["open-pr", "nonce"])
def test_unconfirmed_finalization_retains_nonterminal_task(caller: Caller, block: str) -> None:
    if block == "open-pr":
        caller.block_finalize = True
    else:
        # Retained readiness remains correct; the subsequent finalizer reply moves.
        original = caller.rewrite

        def move(request: httpx.Request, response: httpx.Response) -> httpx.Response:
            if request.url.path.endswith("/prepare"):
                caller.final_nonce = "foreign-incarnation"
            return original(request, response) if original else response

        caller.rewrite = move
    assert caller.run() == 1
    assert caller.queue.get(caller.task.id).status != "abandoned"
    assert len(caller.posts) == 1


def test_parser_and_projection_do_not_accept_owner_overrides(caller: Caller) -> None:
    args = cli.build_parser().parse_args(["relevance-closeout", caller.task.id])
    args.owner_session_id = "foreign"
    from agent_dispatch.client_errors import DispatchError
    from agent_dispatch.task_worker_cli import run_task_worker, task_worker

    with pytest.raises(DispatchError, match="identity"):
        run_task_worker(args, task_worker(args))
    assert not caller.requests and not caller.commands


def test_normal_caller_keeps_local_identity_and_disables_failover(
    caller: Caller,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager

    token = os.environ["AGENT_DISPATCH_TASK_TOKEN"]
    for name in (
        "AGENT_DISPATCH_TASK_TOKEN",
        "AGENT_DISPATCH_TASK_ID",
        "AGENT_DISPATCH_TASK_URL",
        "AGENT_DISPATCH_TASK_WORKER_ID",
    ):
        monkeypatch.delenv(name)
    projected = TaskWorker(
        token,
        caller.task.id,
        "http://coordinator.test",
        caller.workspace.worker_id,
    )

    def identity(args):
        assert not getattr(args, "machine", None) and not getattr(args, "worktree", None)
        return caller.workspace.machine, caller.workspace.worktree

    @contextmanager
    def selected(args, **options):
        assert options == {"allow_ssh_failover": False}
        with projected.client() as client:
            yield client

    monkeypatch.setattr(cli, "_identity", identity)
    monkeypatch.setattr(cli, "_client", selected)
    assert caller.run() == 0
