"""Ordinary decision parsing, preparation and own-finalize selection retain custody."""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch import pool_worker_decline_cli as worker
from agent_dispatch.client import DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue import Status
from tests import test_pool_decline_closeout as closeout
from tests import test_pool_placement_client as controls
from tests.test_pool_worker_decline import prepared_worker

q = controls.q
governed = closeout.governed
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
    assert [r.url.path for r in requests] == [
        f"/tasks/{task_id}", "/health", f"/tasks/{task_id}/relationship", "/health", f"/tasks/{task_id}/pool-decline",
    ]
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


@pytest.mark.parametrize("operation", ["record", "finalize"])
def test_capture_none_records_blocker_for_discovery_and_finalization(q, caller, monkeypatch, operation):
    args, task_id, _, requests, commands, *_ = caller
    original = worker.procutil.run_background_capture

    def unavailable(argv, **kwargs):
        if operation in argv:
            commands.append((argv, kwargs))
            return None
        return original(argv, **kwargs)

    monkeypatch.setattr(worker.procutil, "run_background_capture", unavailable)
    with pytest.raises(DispatchError, match="timed out or failed to launch"):
        args.func(args)
    assert q.pool_worker_decline(task_id).blocker
    assert q.get(task_id).status == Status.CLAIMED
    assert requests[-1].url.path.endswith("/pool-decline/blocker")
    assert len(commands) == (1 if operation == "record" else 2)


@pytest.mark.parametrize("scope", ["worktree", "machine"])
def test_host_case_variants_decline_with_exact_assignment_exclusion(q, caller, monkeypatch, scope):
    args, task_id, _, _, _, *_ = caller
    monkeypatch.setattr(cli, "_identity", lambda args: ("HOST-A", "worktree"))
    args.exclude_self = scope
    with q._connect() as conn:
        conn.execute("UPDATE task_pool_assignments SET machine='Host-A' WHERE task_id=?", (task_id,))
    assert args.func(args) == 0
    decline = q.pool_worker_decline(task_id)
    assert decline.worker_id == "HOST-A/worktree"
    assert decline.exclusions == (f"{scope}:{'Host-A' if scope == 'machine' else 'worktree'}",)


@pytest.mark.parametrize("change", ["absent", "host", "reservation"])
def test_machine_self_exclusion_refuses_unavailable_or_changed_assignment(
    q, api, caller, monkeypatch, change,
):
    args, task_id, _, requests, commands, *_ = caller
    args.exclude_self = "machine"

    def rewrite(request, response):
        if request.url.path == f"/tasks/{task_id}":
            value = response.json()
            if change == "absent":
                value.pop("pool_assignment")
            else:
                field = "machine" if change == "host" else "reservation_key"
                value["pool_assignment"][field] = "foreign"
            return httpx.Response(200, json=value)
        return response

    monkeypatch.setattr(cli, "_client",
                        lambda *a, **k: controls.connected(api, requests, control=False, rewrite=rewrite))
    with pytest.raises(DispatchUpgradeRequired, match="retained pool assignment"):
        args.func(args)
    assert q.pool_worker_decline(task_id) is None
    assert not commands and all(r.method == "GET" for r in requests)


@pytest.fixture
def governed_cli(governed, monkeypatch):
    """The real parser and coordinator over a workspace that retains its task claim."""
    from fastapi.testclient import TestClient

    from tests import test_pool_placement as placement_controls

    g = governed
    order, requests = [], []
    with TestClient(placement_controls.create_app(
        g.q, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    )) as app:
        monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "native")
        monkeypatch.setattr(cli, "_identity", lambda args: ("host-a", "tree"))
        monkeypatch.setattr(cli, "_client", lambda *a, **k: controls.connected(app, requests, control=False))
        monkeypatch.setattr(worker, "DISPOSITION_POLL_SECONDS", 0)

        def finalize(binding, worktree):
            order.append(("finalize", g.q.get(g.task_id).status, g.q.task_relationship(g.task_id)["state"]))
            assert binding["reservation_key"] == g.binding["reservation_key"] and worktree == "tree"

        monkeypatch.setattr(worker, "_finalize", finalize)
        args = cli.build_parser().parse_args([
            "decline", g.task_id, "--pool-generation", str(g.binding["generation"]),
            "--owner-session-id", "native", "--exclude-self", "worktree",
            "--note", "The pool lacks the required tool.",
        ])
        yield g, args, order, requests, app


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_retained_claim_orders_readiness_requeue_settlement_then_finalization(governed_cli, monkeypatch, capsys):
    g, args, order, requests, _ = governed_cli
    await_disposition = worker._await_disposition

    def supervisor_observes(client, task_id, decline):
        order.append(("prepared", g.q.get(task_id).status, g.q.task_relationship(task_id)["state"]))
        g.q.close_pool_decline(task_id, **g.binding)
        await_disposition(client, task_id, decline)

    monkeypatch.setattr(worker, "_await_disposition", supervisor_observes)
    assert args.func(args) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["routing"] == "requeued" and emitted["task_relationship"] == "closed"
    assert order == [
        ("prepared", Status.CLAIMED, "bound"), ("finalize", Status.QUEUED, "closed"),
    ]
    current = g.q.get(g.task_id)
    assert current.generation == g.binding["generation"] + 1 and current.excludes == ["worktree:tree"]
    assert sum("observe-ready" in argv for argv in g.calls) == 1
    paths = [r.url.path for r in requests if r.method == "POST"]
    assert paths == [f"/tasks/{g.task_id}/pool-decline", f"/tasks/{g.task_id}/pool-decline/settlement"]


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_unconfirmed_disposition_retains_claim_then_rerun_converges_once(governed_cli, monkeypatch, capsys):
    g, args, order, requests, _ = governed_cli
    monkeypatch.setattr(worker, "DISPOSITION_WAIT_SECONDS", 0)
    with pytest.raises(DispatchError, match="not yet confirmed"):
        args.func(args)
    assert not order and g.q.get(g.task_id).status == Status.CLAIMED
    assert g.q.task_relationship(g.task_id)["state"] == "bound"
    assert "not yet confirmed" in g.q.pool_worker_decline(g.task_id).blocker
    g.owner.lose_reply = "close"
    assert g.q.close_pool_decline(g.task_id, **g.binding).relationship["state"] == "retained"
    requeued = g.q.get(g.task_id)
    for _ in range(2):
        assert args.func(args) == 0
        assert json.loads(capsys.readouterr().out)["task_relationship"] == "closed"
    assert order == [("finalize", Status.QUEUED, "closed")] * 2
    assert g.q.get(g.task_id) == requeued
    assert sum("observe-ready" in argv for argv in g.calls) == 1
    assert not any(r.url.path.endswith("/closeout") for r in requests)


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_unconfirmed_disposition_reads_keep_polling_until_requeue(governed_cli, monkeypatch, capsys):
    g, args, order, requests, _ = governed_cli
    await_disposition = worker._await_disposition
    failures = [
        httpx.ConnectError("lost"), DispatchError(503, "unavailable"), ValueError("invalid json"),
    ]

    def lossy_observation(client, task_id, decline):
        real_get = client.get

        def get(identifier, **kwargs):
            if failures:
                raise failures.pop(0)
            return real_get(identifier, **kwargs)

        monkeypatch.setattr(client, "get", get)
        g.q.close_pool_decline(task_id, **g.binding)
        await_disposition(client, task_id, decline)

    monkeypatch.setattr(worker, "_await_disposition", lossy_observation)
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["task_relationship"] == "closed"
    assert not failures and order == [("finalize", Status.QUEUED, "closed")]


@pytest.mark.parametrize("governed", [False], indirect=True)
@pytest.mark.parametrize("blocker_lost", [False, True])
def test_persistently_lost_disposition_reads_record_blocker_and_retain_claim(
    governed_cli, monkeypatch, blocker_lost,
):
    g, args, order, requests, _ = governed_cli
    monkeypatch.setattr(worker, "DISPOSITION_WAIT_SECONDS", 0)
    await_disposition = worker._await_disposition

    def lost_observation(client, task_id, decline):
        def get(identifier, **kwargs):
            raise httpx.ReadTimeout("observation lost")

        def unreachable(*a, **k):
            raise httpx.ConnectError("coordinator unreachable")

        monkeypatch.setattr(client, "get", get)
        if blocker_lost:
            monkeypatch.setattr(client, "record_pool_decline_blocker", unreachable)
        await_disposition(client, task_id, decline)

    monkeypatch.setattr(worker, "_await_disposition", lost_observation)
    with pytest.raises(DispatchError, match="not yet confirmed .*observation lost.*custody retained"):
        args.func(args)
    assert not order and g.q.get(g.task_id).status == Status.CLAIMED
    assert g.q.task_relationship(g.task_id)["state"] == "bound"
    blocker = g.q.pool_worker_decline(g.task_id).blocker
    assert blocker is None if blocker_lost else "observation lost" in blocker


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_old_coordinator_with_retained_claim_refuses_before_decision(governed_cli, monkeypatch):
    g, args, order, requests, app = governed_cli

    def rewrite(request, response):
        if request.url.path == "/health":
            value = response.json()
            value["pool_placement"].pop("worker_decline_readiness_closeout_versions")
            return httpx.Response(200, json=value)
        return response

    monkeypatch.setattr(cli, "_client", lambda *a, **k: controls.connected(
        app, requests, control=False, rewrite=rewrite,
    ))
    with pytest.raises(DispatchUpgradeRequired):
        args.func(args)
    assert g.q.pool_worker_decline(g.task_id) is None and not order
    assert all(r.method == "GET" for r in requests)


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_unsettled_claim_for_another_reservation_refuses_before_decision(governed_cli, monkeypatch):
    g, args, order, requests, app = governed_cli

    def rewrite(request, response):
        if request.url.path.endswith("/relationship"):
            value = response.json()
            value["request"]["reservation_key"] = "replacement-reservation"
            return httpx.Response(200, json=value)
        return response

    monkeypatch.setattr(cli, "_client", lambda *a, **k: controls.connected(
        app, requests, control=False, rewrite=rewrite,
    ))
    with pytest.raises(DispatchError, match="original task relationship"):
        args.func(args)
    assert g.q.pool_worker_decline(g.task_id) is None and not order
    assert all(r.method == "GET" for r in requests)
    assert g.q.task_relationship(g.task_id)["state"] == "bound"


def _replace_after_settled_decline(g, args, monkeypatch):
    monkeypatch.setattr(worker, "DISPOSITION_WAIT_SECONDS", 0)
    with pytest.raises(DispatchError, match="not yet confirmed"):
        args.func(args)
    # Dispatch settled the claim, but the worker never finalized its workspace.
    assert g.q.close_pool_decline(g.task_id, **g.binding).relationship["state"] == "closed"
    requeued = g.q.get(g.task_id)
    g.q.retire_spawn(g.binding["reservation_key"], exact_absence=True, conclusion_state="skipped")
    g.q.publish_pool_catalog_scope(
        "host-a", "linux", "publisher-0", generation=1, expected_revision=1,
        registration_revision=g.q.pool_registration_revision(), expected_cohort_revision=1,
        input_revision="fresh-pool", definitions=(closeout.pool(),), now=requeued.not_before,
    )
    replacement, admitted = g.q.reserve_spawn(
        g.task_id, reserved_by="allocator", driver="cli",
        pool_catalog_revision=g.q.pool_catalog_snapshot().revision, now=requeued.not_before,
    )
    assert admitted and replacement.key != g.binding["reservation_key"]
    assert g.q.pool_worker_decline(g.task_id) is None
    return g.q.get(g.task_id)


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_rerun_after_replacement_placement_recovers_exact_original_binding(governed_cli, monkeypatch, capsys):
    g, args, order, requests, _ = governed_cli
    placed = _replace_after_settled_decline(g, args, monkeypatch)
    requests.clear()
    for _ in range(2):
        assert args.func(args) == 0
        emitted = json.loads(capsys.readouterr().out)
        assert emitted["task_relationship"] == "closed"
        assert emitted["decline"]["reservation_key"] == g.binding["reservation_key"]
    assert order == [("finalize", placed.status, "closed")] * 2
    assert g.q.get(g.task_id) == placed
    assert not any(
        r.method == "POST" and r.url.path.endswith(("/closeout", "/pool-decline")) for r in requests
    )


@pytest.mark.parametrize("governed", [False], indirect=True)
def test_recovery_refuses_another_incarnation_or_unsettled_decline(governed_cli):
    g, _, _, _, app = governed_cli
    from tests.test_pool_placement import headers
    url = f"/tasks/{g.task_id}/pool-decline/recovery"
    body = {"worker_id": g.binding["worker_id"], "native_id": "native",
            "generation": g.binding["generation"]}
    g.q.prepare_pool_decline(
        g.task_id, **g.binding, exclusions=["worktree:tree"], note="The pool lacks the required tool.",
    )
    assert app.get(url, headers=headers("data"), params=body).status_code == 404
    g.q.close_pool_decline(g.task_id, **g.binding)
    found = app.get(url, headers=headers("data"), params=body)
    assert found.status_code == 200
    assert found.json()["decline"]["reservation_key"] == g.binding["reservation_key"]
    assert found.json()["worktree"] == "tree"
    for change in ({"native_id": "other"}, {"generation": g.binding["generation"] + 1},
                   {"worker_id": "host-a/other"}):
        assert app.get(url, headers=headers("data"), params={**body, **change}).status_code == 404


@pytest.mark.parametrize("versions", ["missing", None, [], [True], [2]])
@pytest.mark.parametrize("governed", [False], indirect=True)
def test_rolled_back_coordinator_refuses_recovery_before_settlement(governed_cli, monkeypatch, versions):
    g, args, order, requests, app = governed_cli
    placed = _replace_after_settled_decline(g, args, monkeypatch)

    def rewrite(request, response):
        if request.url.path == "/health":
            value = response.json()
            if versions == "missing":
                value["pool_placement"].pop("worker_decline_binding_recovery_versions")
            else:
                value["pool_placement"]["worker_decline_binding_recovery_versions"] = versions
            return httpx.Response(200, json=value)
        return response

    monkeypatch.setattr(cli, "_client", lambda *a, **k: controls.connected(
        app, requests, control=False, rewrite=rewrite,
    ))
    requests.clear()
    with pytest.raises(DispatchUpgradeRequired, match="binding recovery"):
        args.func(args)
    assert not order and g.q.get(g.task_id) == placed
    assert not any(r.url.path.endswith(("/recovery", "/settlement")) for r in requests)
    assert all(r.method == "GET" for r in requests)
