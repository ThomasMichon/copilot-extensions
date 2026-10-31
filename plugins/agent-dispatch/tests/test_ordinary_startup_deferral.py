"""Transport failures defer ordinary startup without inventing new authority."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict

import pytest

from agent_dispatch import bridge, create_cli, ordinary_bridge, ordinary_invocation
from agent_dispatch.client import DispatchError
from agent_dispatch.queue import TaskError
from agent_dispatch.spawn_factories import make_headless_spawn
from agent_dispatch.supervisor import Supervisor
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_ordinary_bridge_startup import startup as startup
from tests.test_supervisor import QueueBackedClient

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.mark.parametrize("entrypoint", ["supervisor", "one-shot"])
@pytest.mark.parametrize("failure", [DispatchError, TaskError])
def test_seed_read_failure_defers_actual_caller_then_recovers(
    startup, monkeypatch, entrypoint, failure,
):
    queue, task, _, calls = startup
    task["_canonical_seed"] = True
    origin = queue.get_reservation(task["spawn_reservation_key"])
    reads = []
    error = (DispatchError(503, "fixture reservation GET unavailable")
             if failure is DispatchError else TaskError("fixture reservation GET unavailable"))

    class Client(QueueBackedClient):
        def reserve_spawn(self, task_id, *, reserved_by=None, coordinator_launch=False, driver=None):
            reservation, admitted = queue.reserve_spawn(
                task_id, reserved_by=reserved_by, driver=driver,
            )
            return {"reserved": admitted, "reservation": asdict(reservation)}

        def get_reservation(self, key):
            reads.append(key)
            if len(reads) == 1:
                raise error
            return asdict(queue.get_reservation(key))

        def record_spawn_allocation(self, key, project):
            calls.append(("allocation", key, project))
            return super().record_spawn_allocation(key, project)

        def record_created_agent(self, key, **facts):
            calls.append(("store", facts))
            return asdict(ordinary_bridge.record_created_agent(queue, key, **facts))

        def record_spawn(
            self, key, *, session_handle=None, worktree=None, session_info=None,
            startup_complete=None, startup_revision=None,
        ):
            ordinary_bridge.record_session_info(queue, key, session_info)
            return super().record_spawn(
                key, session_handle=session_handle, worktree=worktree,
                startup_complete=startup_complete, startup_revision=startup_revision,
            )

    client = Client(queue)
    args = argparse.Namespace(spawn_backend="bridge", spawn_agent="local",
                              spawn_charter="reviewer", run_async=True)
    if entrypoint == "supervisor":
        queue.defer_spawn(origin.key, detail="fixture has not allocated")
        supervisor = Supervisor(
            client, spawn_fn=make_headless_spawn(agent="local", charter="reviewer"),
            repo="example", machine="host-a", max_concurrent=1,
        )
        assert supervisor.poll_once() == []
    else:
        with pytest.raises(SystemExit) as raised:
            create_cli._spawn_ordinary_worker(args, task, asdict(origin), client, route="")
        assert raised.value.code == 1

    deferred = queue.latest_reservation(task["id"])
    assert deferred.state == "deferred"
    assert str(error) in deferred.detail
    assert len(reads) == 1
    assert calls == []
    assert deferred.allocation_project is None
    assert deferred.worktree is None and deferred.agent_info is None
    assert deferred.session_handle is None
    assert queue.get(task["id"]).status == "queued"

    if entrypoint == "supervisor":
        assert supervisor.poll_once() == [task["id"]]
    else:
        reserved = client.reserve_spawn(task["id"], reserved_by="supervisor",
                                        driver="ordinary-bridge")
        assert reserved["reserved"]
        create_cli._spawn_ordinary_worker(args, task, reserved["reservation"], client, route="")
    recovered = queue.latest_reservation(task["id"])
    assert recovered.state == "spawned"
    assert recovered.creation_key == origin.creation_key
    assert recovered.allocation_reservation_key == origin.key
    assert recovered.agent_info["session_id"] == "bridge-a"
    assert recovered.agent_info["last_send"]["turn_index"] == 0
    assert [call[0] for call in calls].count("create") == 1
    assert [call[0] for call in calls].count("send") == 1
    assert queue.get(task["id"]).status == "queued"


@pytest.mark.parametrize("entrypoint", ["factory", "one-shot"])
@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_historical_receipt_transport_failure_defers_then_reads_same_g0(
    startup, monkeypatch, entrypoint, failure,
):
    queue, task, client, calls = startup
    task["_canonical_seed"] = True
    args = argparse.Namespace(spawn_backend="bridge", spawn_agent="local",
                              spawn_charter="reviewer", run_async=True)
    factory = make_headless_spawn(agent="local", charter="reviewer")

    def invoke(snapshot):
        snapshot = {**snapshot, "_spawn_client": client}
        if entrypoint == "factory":
            return factory(snapshot)
        result, _, handle = create_cli._do_spawn(args, snapshot)
        return result.returncode == 0, {**handle, "error": result.stderr}

    ok, first = invoke(task)
    assert ok
    original_bytes = next(call[2]["input"] for call in calls if call[0] == "send")
    original = json.loads(original_bytes)
    claimed = queue.claim_one("worker", task_id=task["id"])
    assert claimed.generation == 1
    before = json.dumps(asdict(queue.get_reservation(task["spawn_reservation_key"])),
                        sort_keys=True)
    calls.clear()

    def forbidden(*_a, **_kw):
        pytest.fail("historical receipt transport must not write readiness or check fresh permission")

    monkeypatch.setattr(client, "record_created_agent", forbidden)
    monkeypatch.setattr(client, "record_spawn_allocation", forbidden)
    monkeypatch.setattr(client, "get", forbidden)
    transport_error = None

    def receipt(argv, **kwargs):
        nonlocal transport_error
        assert "--receipt-only" in argv
        assert argv[argv.index("--expected-native-session-id") + 1] == "native-a"
        assert kwargs["input"] == original_bytes
        assert json.loads(kwargs["input"]) == original
        calls.append(("receipt", argv, kwargs))
        if len(calls) == 1:
            transport_error = (subprocess.TimeoutExpired(argv, 1) if failure == "timeout"
                               else OSError("fixture historical receipt RPC unavailable"))
            raise transport_error
        return subprocess.CompletedProcess(argv, 0, json.dumps(first["session_info"]), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", receipt)
    snapshot = {**asdict(claimed), "spawn_reservation_key": task["spawn_reservation_key"]}
    ok, deferred = invoke(snapshot)
    assert not ok and deferred["deferred"] is True
    assert str(transport_error) in deferred.get("error", "")
    assert json.dumps(asdict(queue.get_reservation(task["spawn_reservation_key"])),
                      sort_keys=True) == before
    ok, recovered = invoke(snapshot)
    assert ok and recovered["session_info"] == first["session_info"]
    assert [call[0] for call in calls] == ["receipt", "receipt"]
    assert original["binding"]["admission_generation"] == 0
    assert json.dumps(asdict(queue.get_reservation(task["spawn_reservation_key"])),
                      sort_keys=True) == before
    assert queue.get(task["id"]).generation == 1


@pytest.mark.parametrize("failure", ["missing-origin", "rendering", "rendering-task-error"])
def test_seed_does_not_reclassify_metadata_or_rendering_errors(startup, monkeypatch, failure):
    from agent_dispatch import embody
    queue, task, client, calls = startup
    if failure == "missing-origin":
        monkeypatch.setattr(client, "get_reservation", lambda _: {})
        expected = KeyError
    else:
        expected = TaskError if failure == "rendering-task-error" else ValueError

        def invalid_render(*_a, **_kw):
            raise expected("fixture renderer rejected input")
        monkeypatch.setattr(embody, "autopilot_worker_prompt", invalid_render)
    with pytest.raises(expected):
        make_headless_spawn(agent="local", charter="reviewer")({**task, "_spawn_client": client})
    assert calls == []
    assert queue.get_reservation(task["spawn_reservation_key"]).agent_info is None


@pytest.mark.parametrize("failure", [DispatchError, TaskError])
def test_seed_read_deferral_chains_original_error(startup, monkeypatch, failure):
    _, task, client, calls = startup
    error = DispatchError(503, "fixture GET failed") if failure is DispatchError else TaskError("fixture GET failed")

    def failed_read(_key):
        raise error

    monkeypatch.setattr(client, "get_reservation", failed_read)
    with pytest.raises(bridge.BridgeCarriedSessionBusy) as raised:
        ordinary_invocation.startup_seed(task, client)
    assert raised.value.__cause__ is error
    assert str(error) in str(raised.value)
    assert calls == []
