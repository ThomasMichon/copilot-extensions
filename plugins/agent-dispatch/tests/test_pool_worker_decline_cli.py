"""Ordinary decision parsing, preparation and own-finalize selection retain custody."""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from agent_dispatch import __main__ as cli, pool_worker_decline_cli as worker
from agent_dispatch.client import DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue import Status
from tests import test_pool_placement_client as controls
from tests.test_pool_worker_decline import prepared_worker

q = controls.q
api = controls.api


@pytest.fixture
def caller(q, api, monkeypatch):
    task_id, binding = prepared_worker(q)
    requests, commands = [], []
    prefix = ["/selected/python", "-m", "cell.peer_launch", "selected-worktrees"]
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "native")
    monkeypatch.setattr(cli, "_identity", lambda args: ("host-a", "worktree"))
    monkeypatch.setattr(cli, "_client", lambda *a, **k: controls.connected(api, requests, control=False))
    monkeypatch.setattr(worker.procutil, "agent_worktrees_launch_prefix", lambda: prefix)
    descriptor = {
        "contract": worker.CONTRACT, "version": 1, "operations": ["observe-finalized"],
        "own_finalization_cli_versions": [1], "paired_closeout": False,
        "task_release": False, "process_retirement": False,
    }
    finalized = {
        "ok": True, "contract": worker.CONTRACT, "version": 1, "operation": "finalize-own",
        "task_id": task_id, "reservation_key": binding["reservation_key"], "native_id": "native",
        "worktree_id": "worktree", "status": "finalized", "creation_nonce": "incarnation",
        "head_revision": 0, "checkout_retained": True, "task_release": False, "process_retirement": False,
    }

    def run(argv, **kwargs):
        commands.append((argv, kwargs))
        value = descriptor if argv[len(prefix)] == "record" else finalized
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(worker.procutil, "run_background_capture", run)
    args = cli.build_parser().parse_args([
        "decline", task_id, "--pool-generation", str(binding["generation"]),
        "--owner-session-id", "native", "--exclude-self", "worktree", "--note", binding["note"],
    ])
    return args, task_id, binding, requests, commands, prefix, descriptor, finalized


def test_actual_parser_prepares_then_only_finalizes_own_workspace(q, caller, capsys):
    args, task_id, binding, requests, commands, prefix, _, _ = caller
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["task_release"] is False
    assert q.get(task_id).status == Status.CLAIMED and q.get(task_id).owner is None
    assert q.get(task_id).excludes == ["capability:unrelated"]
    assert q.pool_worker_decline(task_id).exclusions == ("worktree:worktree",)
    assert [r.url.path for r in requests] == [f"/tasks/{task_id}", "/health", f"/tasks/{task_id}/pool-decline"]
    assert commands[0][0] == [*prefix, "record", "--describe-closeout-contract", "--json"]
    assert commands[1][0] == [
        *prefix, "finalize", "--dispatch-task-id", task_id,
        "--dispatch-reservation-key", binding["reservation_key"],
        "--dispatch-native-id", "native", "--json",
    ]
    assert commands[0][1]["env"] == commands[1][1]["env"]


@pytest.mark.parametrize("reason", ["old-provider", "invalid-reply", "foreign-reply", "timeout", "blocked"])
def test_failed_or_unknown_own_closeout_records_blocker_without_fallback(q, caller, monkeypatch, reason):
    args, task_id, _, requests, commands, _, descriptor, finalized = caller
    if reason == "old-provider":
        descriptor.pop("own_finalization_cli_versions")
    elif reason == "invalid-reply":
        finalized["ok"] = 1
    elif reason == "foreign-reply":
        finalized["native_id"] = "another-native"
    else:
        original = worker.procutil.run_background_capture

        def fail(argv, **kwargs):
            if "finalize" in argv:
                if reason == "timeout":
                    commands.append((argv, kwargs))
                    raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
                commands.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 1, json.dumps({"error": "Open PR remains"}), "")
            return original(argv, **kwargs)

        monkeypatch.setattr(worker.procutil, "run_background_capture", fail)
    with pytest.raises((DispatchError, subprocess.TimeoutExpired)):
        args.func(args)
    assert q.get(task_id).status == Status.CLAIMED and q.get(task_id).owner is None
    assert q.pool_worker_decline(task_id).blocker
    assert len(commands) == (1 if reason == "old-provider" else 2)
    assert requests[-1].url.path == f"/tasks/{task_id}/pool-decline/blocker"
    assert not any(r.url.path.endswith("/closeout") or "/yield" in r.url.path for r in requests)


def test_unknown_prepare_outcome_never_starts_finalization_or_replays(q, api, caller, monkeypatch):
    args, task_id, _, requests, commands, *_ = caller

    def rewrite(request, response):
        if request.method == "POST":
            raise httpx.ReadTimeout("Lost preparation response", request=request)
        return response

    monkeypatch.setattr(cli, "_client",
                        lambda *a, **k: controls.connected(api, requests, control=False, rewrite=rewrite))
    with pytest.raises(httpx.ReadTimeout):
        args.func(args)
    assert q.pool_worker_decline(task_id) is not None
    assert q.pool_worker_decline(task_id).blocker is None
    assert not commands and sum(r.method == "POST" for r in requests) == 1


@pytest.mark.parametrize("change", ["native", "generation", "identity"])
def test_wrong_caller_refuses_before_decision(q, caller, monkeypatch, change):
    args, task_id, _, requests, commands, *_ = caller
    if change == "native":
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "foreign")
    elif change == "generation":
        args.pool_generation += 1
    else:
        monkeypatch.setattr(cli, "_identity", lambda args: ("host-a", "foreign-worktree"))
    with pytest.raises(DispatchError):
        args.func(args)
    assert q.pool_worker_decline(task_id) is None and not commands
    assert all(r.method == "GET" for r in requests)


@pytest.mark.parametrize("versions", [None, [], [True], [2]])
def test_old_finalizer_is_never_guessed_or_effectfully_probed(caller, versions):
    args, _, _, _, commands, _, descriptor, _ = caller
    descriptor["own_finalization_cli_versions"] = versions
    with pytest.raises(DispatchUpgradeRequired):
        args.func(args)
    assert len(commands) == 1


def test_task_only_credentials_do_not_gain_preacceptance_decline(caller):
    from agent_dispatch.task_worker_cli import TaskWorker, run_task_worker

    args, task_id, _, requests, commands, *_ = caller
    with pytest.raises(DispatchError, match="outside the projected task authority"):
        run_task_worker(args, TaskWorker("task-secret", task_id, "http://example.test", "host-a/worktree"))
    assert not requests and not commands
