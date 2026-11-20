"""Exact creator reconciliation without weakening task or request ownership."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from types import SimpleNamespace
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from agent_dispatch import handoff_claim_release as release, procutil
from agent_dispatch.handoff_claim_binding import CONTRACT, bind_claim
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import Status, TaskError
from tests._helpers import required


def _task(**changes: Any) -> dict[str, Any]:
    task = {
        "id": "T1", "labels": ["handoff"], "source": "context-handoff",
        "target_worktree": "wt-9", "target_machine": "box-1",
        "status": "abandoned", "attempts": 0, "owner": None,
        "owner_session_id": None, "started_at": None, "claimed_at": None,
        "handoff_claim": {
            "contract": CONTRACT, "machine": "box-1", "project": "creator",
            "worktree_id": "wt-9", "creation_nonce": "record-one",
            "claim_nonce": "claim-one", "kind": "task", "ref": "T1",
        },
    }
    return {**task, **changes}


@pytest.mark.parametrize("task,expected", [
    ({"labels": ["handoff"]}, True),
    ({"labels": [], "source": "context-handoff"}, True),
    ({"labels": ["review"]}, False),
    ({"source": None}, False),
    (None, False),
    ("not-a-dict", False),
])
def test_handoff_classification_remains_shared(task: Any, expected: bool) -> None:
    assert release.is_handoff_task(task) is expected


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls = []

    def capture(*args: Any, timeout: float) -> SimpleNamespace:
        calls.append(list(args))
        if args[:2] == ("claims", "binding"):
            result = {"claim_binding": _task()["handoff_claim"]}
        elif args[:2] == ("claims", "settle"):
            result = {"worktree_id": "wt-9", "ref": "T1", "disposition": "released"}
        else:
            assert args[0] == "cancel-handoff"
            result = {"cancelled": True}
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(procutil, "run_agent_worktrees_capture", capture)
    return calls


def test_release_targets_recorded_project_and_both_incarnations(native: list[list[str]]) -> None:
    result = release._release_task_claim("T1", worktree="wt-9", task=_task())
    assert result == {"taskTerminal": True, "claimSettled": True}
    assert native == [
        ["claims", "binding", "T1", "--owner-ref", "box-1/creator/wt-9", "--json"],
        [
            "claims", "settle", "T1", "--owner-ref", "box-1/creator/wt-9", "--released",
            "--expected-record-nonce", "record-one", "--expected-claim-nonce", "claim-one",
            "--expected-kind", "task", "--json",
        ],
    ]


@pytest.mark.parametrize("changes", [
    {"status": "queued"}, {"status": "claimed"}, {"status": "started"},
    {"status": "suspended"}, {"status": "dead_letter"}, {"status": None},
    {"owner": "worker"}, {"owner_session_id": "session"},
    {"attempts": 1}, {"attempts": None}, {"claimed_at": 1.0}, {"started_at": 1.0},
    {"id": "other-task"},
])
def test_unknown_live_or_execution_bearing_abandonment_never_releases(native: list[list[str]], changes: dict[str, Any]) -> None:
    result = release._release_task_claim("T1", worktree="wt-9", task=_task(**changes))
    assert not result["taskTerminal"] and not result["claimSettled"]
    assert native == []


@pytest.mark.parametrize("changes", [
    {"handoff_claim": None}, {"handoff_claim": {}},
    {"handoff_claim": {**_task()["handoff_claim"], "project": "../other"}},
    {"handoff_claim": {**_task()["handoff_claim"], "ref": "other"}},
    {"target_worktree": "other"}, {"target_machine": "other"},
])
def test_unbound_or_contradictory_selector_never_falls_back_to_cwd(native: list[list[str]], changes: dict[str, Any]) -> None:
    result = release._release_task_claim("T1", worktree="wt-9", task=_task(**changes))
    assert result["taskTerminal"] and not result["claimSettled"]
    assert native == []


def test_completed_consumption_and_detached_affinity_keep_existing_closeout(native: list[list[str]]) -> None:
    task = _task(status="completed", attempts=1, owner="worker", target_worktree=None, affinity={"worktree": "wt-9"})
    assert release._release_task_claim("T1", worktree="wt-9", task=task)["claimSettled"]


@pytest.mark.parametrize("process", [
    None,
    SimpleNamespace(returncode=2, stdout="{}", stderr="unknown command"),
    SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    SimpleNamespace(returncode=0, stdout='{"claim_binding": null}', stderr=""),
])
def test_unavailable_or_prior_native_owner_is_explicitly_pending_without_write(monkeypatch: pytest.MonkeyPatch, process: SimpleNamespace | None) -> None:
    calls: list[tuple[str, ...]] = []

    def capture(*args: str, timeout: float) -> SimpleNamespace | None:
        calls.append(args)
        return process

    monkeypatch.setattr(
        procutil, "run_agent_worktrees_capture",
        capture,
    )
    result = release._release_task_claim("T1", worktree="wt-9", task=_task())
    assert result["taskTerminal"] and not result["claimSettled"] and result["claimNote"]
    assert len(calls) == 1 and calls[0][:2] == ("claims", "binding")


@pytest.mark.parametrize("target,here,cancels", [
    ("box-1", "box-1", True), ("BOX-1", "box-1", True),
    ("box-2", "box-1", False), ("box-2", None, False), (None, None, True),
])
def test_background_cancellation_is_local_and_owner_qualified(monkeypatch: pytest.MonkeyPatch, native: list[list[str]], target: str | None, here: str | None, cancels: bool) -> None:
    task = _task(target_machine=target)
    if target and target.casefold() == "box-2":
        task["handoff_claim"] = {**task["handoff_claim"], "machine": target}
    monkeypatch.setattr(release, "_this_machine", lambda: here)
    release._release_in_background(
        "T1", target, worktree="wt-9", timeout=1, cancel_pending=True, task=task,
    )
    cancellations = [args for args in native if args[0] == "cancel-handoff"]
    assert bool(cancellations) is cancels
    if cancels:
        assert cancellations == [[
            "cancel-handoff", "--owner-ref", "box-1/creator/wt-9",
            "--expected-record-nonce", "record-one", "--token", "T1",
        ]]


def test_release_is_nonblocking_and_rechecks_actual_queue_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create(
        "handoff", repo="https://github.com/example/project",
        labels=["handoff"], source="context-handoff", target_worktree="wt-9",
    )
    assert isinstance(task, Task)
    snapshot = asdict(queue.abandon(task.id, permitted=True))
    entered, finish, done = threading.Event(), threading.Event(), threading.Event()
    seen = []

    def capture(task_id: str, *, worktree: str | None, timeout: float, task: Any) -> dict[str, Any]:
        seen.append((task_id, task["status"], threading.current_thread() is threading.main_thread()))
        entered.set()
        finish.wait(2)
        done.set()
        return {"taskTerminal": False, "claimSettled": False}

    monkeypatch.setattr(release, "_release_task_claim", capture)
    release.release_if_handoff({**snapshot, "status": "queued"}, queue=queue)
    try:
        assert entered.wait(1)
        assert not done.is_set()
        assert seen == [(task.id, Status.ABANDONED, False)]
    finally:
        finish.set()
        assert done.wait(2)


def test_nonhandoffs_or_missing_ids_do_not_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        threading, "Thread", lambda *args, **kwargs: pytest.fail("unrelated task spawned a release"),
    )
    for task in (None, {"id": "ordinary", "labels": []}, {"labels": ["handoff"]}):
        release.release_if_handoff(task)


def test_binding_is_durable_immutable_and_does_not_change_task_custody(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    queue = TaskQueue(path)
    task = queue.create(
        "handoff", repo="https://github.com/example/project", status=Status.PROPOSED,
        labels=["handoff"], target_worktree="wt-9", target_machine="box-1",
    )
    assert isinstance(task, Task)
    binding = {**_task()["handoff_claim"], "ref": task.id}
    bound = bind_claim(queue, task.id, binding)
    assert bound.status == Status.PROPOSED and bound.owner is None and bound.attempts == 0
    assert required(TaskQueue(path).get(task.id)).handoff_claim == binding
    event_count = len(queue.events(task.id))
    assert bind_claim(queue, task.id, binding) == bound
    assert len(queue.events(task.id)) == event_count
    with pytest.raises(TaskError, match="another incarnation"):
        bind_claim(queue, task.id, {**binding, "claim_nonce": "replacement"})
    with pytest.raises(TaskError, match="does not match"):
        bind_claim(queue, task.id, {**binding, "worktree_id": "other"})
    assert required(queue.get(task.id)).handoff_claim == binding


@pytest.mark.parametrize("control_token", [None, "", "fixture-control"])
def test_health_only_advertises_reconciliation_with_control_authority(
    tmp_path: Path, control_token: str | None,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_dispatch.coordinator_handoff_claim import register_routes
    from agent_dispatch.coordinator_loops import DrainGate
    from agent_dispatch.coordinator_status import register_status_routes
    from agent_dispatch.events import EventBus

    app = FastAPI()
    app.state.drain_gate = DrainGate()
    queue, bus = TaskQueue(tmp_path / "tasks.db"), EventBus()
    register_status_routes(app, queue, bus, control_token=control_token)
    register_routes(app, queue, bus, control_token=control_token)
    with TestClient(app) as api:
        health = api.get("/health")
        assert health.status_code == 200
        assert (health.json().get("handoff_claim_contract") == CONTRACT) == bool(control_token)
        assert api.post("/tasks/T1/handoff-claim", json={}).status_code == (
            503 if control_token is None else 403
        )


@pytest.mark.parametrize("read_timeout", [10.0, 90.0, None])
def test_reconciliation_read_timeout_covers_native_budgets_without_shortening(
    read_timeout: float | None,
) -> None:
    from agent_dispatch.client import DispatchClient

    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"taskTerminal": True, "claimSettled": False})

    with DispatchClient("http://testserver", transport=httpx.MockTransport(handle)) as owner:
        owner._http.timeout = httpx.Timeout(read_timeout)
        assert owner.reconcile_handoff_claim("T1")["taskTerminal"]
        assert owner._http.timeout.read == read_timeout
    assert len(requests) == 1
    timeout = requests[0].extensions["timeout"]
    assert timeout["read"] == (max(read_timeout, 60.0) if read_timeout is not None else None)
    assert timeout["connect"] == timeout["write"] == timeout["pool"] == read_timeout


def test_actual_client_and_route_preserve_control_auth_and_partial_results(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_dispatch.client import DispatchClient, DispatchError
    from agent_dispatch.coordinator_handoff_claim import register_routes
    from agent_dispatch.events import EventBus

    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create(
        "handoff", repo="https://github.com/example/project", status=Status.PROPOSED,
        labels=["handoff"], target_worktree="wt-9", target_machine="box-1",
    )
    assert isinstance(task, Task)
    binding = {**_task()["handoff_claim"], "ref": task.id}
    app = FastAPI()
    register_routes(app, queue, EventBus(), control_token="fixture-control")
    with TestClient(app) as api:
        def handle(request: httpx.Request) -> httpx.Response:
            response = api.request(
                request.method, str(request.url), headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code, headers=dict(response.headers), content=response.content,
            )

        with DispatchClient("http://testserver", transport=httpx.MockTransport(handle)) as owner:
            with pytest.raises(DispatchError) as error:
                owner.reconcile_handoff_claim(task.id, binding=binding)
            assert error.value.status_code == 403
            assert required(queue.get(task.id)).handoff_claim is None
        with DispatchClient(
            "http://testserver", control_token="fixture-control",
            transport=httpx.MockTransport(handle),
        ) as owner:
            result = owner.reconcile_handoff_claim(task.id, binding=binding)
            assert result["claimBinding"] == binding
            assert not result["taskTerminal"] and not result["claimSettled"]
            assert required(queue.get(task.id)).status == Status.PROPOSED


@pytest.mark.parametrize("supported", [False, True])
def test_real_cli_parser_preserves_peer_capability_and_explicit_owner(monkeypatch: pytest.MonkeyPatch, supported: bool) -> None:
    from contextlib import contextmanager

    from agent_dispatch import __main__ as cli

    calls = []

    class Client:
        def get(self, task_id: str) -> dict[str, Any]:
            return {
                "id": "T1",
                **({"handoff_claim_contract": CONTRACT} if supported else {}),
            }

        def reconcile_handoff_claim(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append((task_id, kwargs))
            return {"id": task_id, "taskTerminal": True, "claimSettled": False}

    @contextmanager
    def selected_client(*args: Any, **kwargs: Any) -> Iterator[Client]:
        assert kwargs == {"ensure": False, "allow_ssh_failover": False}
        yield Client()

    monkeypatch.setattr(cli, "_client", selected_client)
    monkeypatch.setattr(cli, "_emit", lambda result: 0)
    args = cli.build_parser().parse_args([
        "handoff-claim", "T1", "--owner-ref", "box-1/creator/wt-9",
    ])
    if supported:
        assert args.func(args) == 0
        assert calls == [("T1", {"binding": None, "owner_ref": "box-1/creator/wt-9"})]
    else:
        with pytest.raises(ValueError, match="does not support"):
            args.func(args)
        assert calls == []
