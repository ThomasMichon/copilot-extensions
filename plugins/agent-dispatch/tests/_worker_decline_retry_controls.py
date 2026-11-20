"""Drive the worker entrypoint against the fixture coordinator and an inert finalizer."""

import json
import subprocess
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli
from agent_dispatch import pool_worker_decline_cli as worker
from agent_dispatch import procutil
from agent_dispatch.client import DispatchClient
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.queue import TaskQueue
from tests.test_pool_placement_client import connected


def exercise(
    app: TestClient,
    q: TaskQueue,
    task: str,
    root: str,
    binding: dict[str, Any],
    nonce: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executing = binding["reservation_key"]
    requests: list[httpx.Request] = []
    finalized: list[str] = []
    fault = ""

    def rewrite(req: httpx.Request, response: httpx.Response) -> httpx.Response:
        if fault in {"continuation", "missing-bound-receipt"} and req.url.path.endswith(
            "/relationship"
        ):
            value = response.json()
            if fault == "continuation":
                value["continuation"]["generation"] += 1
            else:
                del value["receipt"]
        elif fault in {"foreign", "missing"} and req.url.path.endswith("/settlement"):
            value = response.json()
            obs = value["closeout"]["observation"]
            if fault == "missing":
                del value["closeout"]["observation"]
            else:
                obs["reservation_key"] = obs["allocation"]["reservation_key"] = "foreign"
        else:
            return response
        return httpx.Response(200, json=value)

    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", binding["native_id"])
    monkeypatch.setattr(cli, "_identity", lambda *_: ("host-a", "worktree-a"))
    monkeypatch.setattr(
        cli, "_client", lambda *a, **k: connected(app, requests, control=False, rewrite=rewrite)
    )
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: ["test-worktrees"])
    capture = procutil.run_background_capture

    def fake(argv: list[str], **kwargs: Any) -> Any:
        if "--describe-closeout-contract" in argv:
            result = {
                "contract": worker.CONTRACT,
                "version": 1,
                "own_finalization_cli_versions": [1],
                "task_release": False,
                "paired_closeout": False,
                "process_retirement": False,
            }
        elif "finalize" in argv:
            key = argv[argv.index("--dispatch-reservation-key") + 1]
            assert (key, argv[argv.index("--dispatch-native-id") + 1]) == (
                root,
                binding["native_id"],
            )
            assert argv[argv.index("--dispatch-task-id") + 1] == task
            finalized.append(key)
            result = {
                "ok": True,
                "contract": worker.CONTRACT,
                "version": 1,
                "operation": "finalize-own",
                "task_id": task,
                "reservation_key": key,
                "worktree_id": "worktree-a",
                "status": "finalized",
                "creation_nonce": nonce,
                "head_revision": 1,
                "checkout_retained": True,
                "task_release": False,
                "process_retirement": False,
                "native_id": binding["native_id"],
            }
        else:
            return capture(argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(procutil, "run_background_capture", fake)
    await_disposition = worker._await_disposition

    def dispose(client: DispatchClient, task_id: str, decline: dict[str, Any]) -> None:
        assert decline["reservation_key"] == executing
        assert q.close_pool_decline(task_id, **binding).applied
        await_disposition(client, task_id, decline)

    monkeypatch.setattr(worker, "_await_disposition", dispose)
    args = cli.build_parser().parse_args(
        [
            "decline",
            task,
            "--pool-generation",
            str(binding["generation"]),
            "--owner-session-id",
            binding["native_id"],
            "--exclude-self",
            "worktree",
            "--note",
            "missing tool",
        ]
    )
    relationship = q.task_relationship(task)
    if relationship and relationship.get("continuation"):
        fault = "continuation"
        with pytest.raises(DispatchError, match="continuation"):
            worker.cmd_decline(args)
    fault = "missing-bound-receipt"
    with pytest.raises(DispatchError, match="bound task relationship receipt"):
        worker.cmd_decline(args)
    assert not any(r.method == "POST" for r in requests)
    fault = ""
    assert worker.cmd_decline(args) == 0 and finalized == [root]
    fault = "missing"
    with pytest.raises(DispatchError, match="allocation readiness"):
        worker.cmd_decline(args)
    fault = "foreign"
    with pytest.raises(DispatchError, match="allocation readiness"):
        worker.cmd_decline(args)
    assert finalized == [root]
    fault = ""
    assert worker.cmd_decline(args) == 0 and finalized == [root, root]
    writes = [r for r in requests if r.method == "POST"]
    assert sum(r.url.path.endswith("/pool-decline") for r in writes) == 1
    assert all(json.loads(r.content)["reservation_key"] == executing for r in writes)
