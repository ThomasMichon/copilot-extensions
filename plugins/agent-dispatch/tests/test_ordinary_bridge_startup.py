"""Ordinary bridge agents are stored before their first model prompt."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict

import pytest

from agent_dispatch import bridge, ordinary_bridge
from agent_dispatch.queue import TaskError, TaskQueue

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.fixture
def startup(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create(title="Assigned work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="ordinary-bridge")
    snapshot = {**asdict(task), "spawn_reservation_key": reservation.key}
    calls = []

    class Client:
        def get_reservation(self, key):
            return asdict(queue.get_reservation(key))

        def record_spawn_allocation(self, key, project):
            return asdict(queue.record_spawn_allocation(key, project))

        def record_created_agent(self, key, **facts):
            calls.append(("store", facts))
            return asdict(ordinary_bridge.record_created_agent(queue, key, **facts))

    def run(argv, **kwargs):
        assert argv[1] == "--json"
        command = argv[2]
        calls.append((command, argv, kwargs))
        if command == "create":
            assert kwargs.get("input") is None
            assert "--prompt-file" not in argv
            assert "--charter" in argv and "reviewer" in argv
            assert argv[3] == "example@local"
            assert argv[argv.index("--creation-key") + 1] == queue.get_reservation(snapshot["spawn_reservation_key"]).creation_key
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": "bridge-a", "ready": True,
                "status": "awaiting_first_prompt",
                "connection": {
                    "worktree_id": "worktree-a", "repo": "example",
                    "native_session_id": "native-a",
                    "target_dir": "synthetic-worktree-a",
                },
            }), "")
        if command == "send":
            assert argv[argv.index("--expected-native-session-id") + 1] == "native-a"
            saved = queue.latest_reservation(task.id)
            assert saved.state == "reserving"
            assert saved.session_handle == "local-body:bridge-a"
            assert saved.worktree == "worktree-a"
            assert saved.agent_info["status"] == "awaiting_first_prompt"
            scope = snapshot.get("prompt")
            expected = f"{scope}\n\nassigned task\n\n" if scope else "assigned task\n\n"
            assert kwargs["input"].startswith(expected)
            assert "--owner-session-id native-a" in kwargs["input"]
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": "bridge-a", "outcome": "delivered",
                "turn_index": 1, "native_session_id": "native-a",
            }), "")
        if command == "status":
            if "--full" in argv:
                return subprocess.CompletedProcess(argv, 0, json.dumps({
                    "session_id": "bridge-a", "acp_session_id": "native-a",
                    "durable_session_id": "native-a", "worktree_id": "worktree-a",
                    "project": "example", "target_dir": "synthetic-worktree-a",
                    "status": "idle", "read_only": False, "at_rest": True, "liveness": None,
                }), "")
            return subprocess.CompletedProcess(argv, 0, '{"status":"stopped"}', "")
        assert command == "resume" and "--strict" in argv
        return subprocess.CompletedProcess(argv, 0, '{"session_id":"bridge-a"}', "")

    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *args, **kw: None)
    return queue, snapshot, Client(), calls


def test_create_store_send_order_and_readable_identity(startup):
    queue, task, client, calls = startup
    result = ordinary_bridge.start(task, client=client, agent="local", project="example",
                                   charter="reviewer", prompt="assigned task")
    assert result["session"] == "local-body:bridge-a"
    assert result["native_session_id"] == "native-a"
    assert [call[0] for call in calls] == ["create", "store", "store", "send"]
    stored = asdict(queue.get_reservation(task["spawn_reservation_key"]))
    assert stored["agent_info"]["session_id"] == "bridge-a"
    assert stored["driver"] == "ordinary-bridge"
    assert queue.get(task["id"]).status == "queued"


def test_assigned_scope_precedes_generic_worker_seed(startup):
    _, task, client, calls = startup
    task["prompt"] = "Read the trusted named identity and rubric before claiming."
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    assert calls[-1][0] == "send"
    assert calls[-1][2]["input"].startswith(task["prompt"] + "\n\nassigned task")


def test_invalid_assigned_scope_never_creates(startup):
    _, task, client, calls = startup
    task["prompt"] = {"unexpected": "object"}
    with pytest.raises(bridge.BridgeUnavailable, match="task prompt must be a string"):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert not calls


def test_failed_store_never_sends_or_discards_created_identity(startup):
    _, task, client, calls = startup

    def fail(*args, **kwargs):
        raise TaskError("coordinator write refused")

    client.record_created_agent = fail
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="bridge-a"):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert [call[0] for call in calls] == ["create"]


def test_interrupted_send_retries_retained_handle_without_create(startup, monkeypatch):
    queue, task, client, calls = startup
    original = ordinary_bridge.subprocess.run

    def interrupted(argv, **kwargs):
        if argv[2] == "send":
            raise subprocess.TimeoutExpired(argv, 1)
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", interrupted)
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert queue.get_reservation(task["spawn_reservation_key"]).session_handle == "local-body:bridge-a"
    monkeypatch.setattr(ordinary_bridge.subprocess, "run", original)
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    assert [call[0] for call in calls] == [
        "create", "store", "store", "store", "status", "resume", "status", "store", "send",
    ]


@pytest.mark.parametrize("change", [
    {"ready": False}, {"status": "running"}, {"session_id": ""},
    {"connection": {"repo": "other", "worktree_id": "worktree-a"}},
])
def test_unproven_creation_never_sends(startup, monkeypatch, change):
    _, task, client, calls = startup

    def invalid(argv, **kwargs):
        calls.append(("create",))
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
            "connection": {"repo": "example", "worktree_id": "worktree-a",
                           "native_session_id": "native-a"},
            **change,
        }), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", invalid)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert [call[0] for call in calls] == ["create"]


def test_old_acquisition_marker_requires_explicit_migration(startup):
    queue, task, client, calls = startup
    from agent_dispatch.acquisition_receipts import ensure
    with queue._connect() as conn:
        ensure(conn)
        conn.execute("INSERT INTO acquisition_markers VALUES (?)",
                     (task["spawn_reservation_key"],))
    with pytest.raises(TaskError, match="legacy acquisition"):
        ordinary_bridge.record_created_agent(
            queue, task["spawn_reservation_key"], task_generation=task["generation"],
            agent_info={"session_id": "bridge-a", "status": "awaiting_first_prompt",
                        "ready": True, "connection": {"worktree_id": "worktree-a",
                        "repo": "example", "native_session_id": "native-a",
                        "target_dir": "synthetic-worktree-a"}},
        )
    assert not calls


def test_created_agent_storage_fences_changed_task_and_release(startup):
    queue, task, client, calls = startup
    queue.set_hold(task["id"], reason="operator", actor="operator")
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert [call[0] for call in calls] == ["create", "store"]


def test_retry_reservation_carries_agent_without_exclusive_key(startup):
    queue, task, client, calls = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    queue.defer_spawn(task["spawn_reservation_key"], detail="transport interrupted")
    retry, admitted = queue.reserve_spawn(
        task["id"], reserved_by="supervisor", driver="ordinary-bridge")
    assert admitted and retry.agent_info["session_id"] == "bridge-a"
    assert retry.session_handle == "local-body:bridge-a"
    assert retry.worktree == "worktree-a"

@pytest.mark.parametrize("released_origin", [False, True])
def test_lost_create_response_replays_key_after_restart_and_routing_change(
    startup, monkeypatch, released_origin,
):
    queue, task, client, calls = startup
    original = ordinary_bridge.subprocess.run
    lost = True

    def run(argv, **kwargs):
        nonlocal lost
        result = original(argv, **kwargs)
        if argv[2] == "create" and lost:
            lost = False
            raise subprocess.TimeoutExpired(argv, 1)
        return result

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    first = queue.get_reservation(task["spawn_reservation_key"])
    assert first.allocation_project == "example"
    assert first.agent_info is None
    queue.defer_spawn(first.key, detail="lost create response")
    restarted = TaskQueue(queue.db_path)
    retry, admitted = restarted.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.creation_key == first.creation_key
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *_a, **_kw: "changed-routing")
    result = ordinary_bridge.start(
        {**task, "spawn_reservation_key": retry.key}, client=client,
        agent="local", project="changed-routing", charter="reviewer", prompt="assigned task",
    )
    assert result["session"] == "local-body:bridge-a"
    creates = [call[1] for call in calls if call[0] == "create"]
    assert len(creates) == 2 and creates[0] == creates[1]
    assert queue.get_reservation(retry.key).worktree_path == "synthetic-worktree-a"
    assert [call[0] for call in calls].count("send") == 1
    queue.defer_spawn(retry.key, detail="later turn on the replayed allocation")
    third, admitted = restarted.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and third.allocation_reservation_key == first.key
    assert third.creation_key == first.creation_key
    assert third.worktree == queue.get_reservation(retry.key).worktree == "worktree-a"
    assert queue.get_reservation(first.key).worktree is None
    if released_origin:
        with queue._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET state='failed',release_requested=1 WHERE key=?",
                         (first.key,))
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="no proven native"):
            ordinary_bridge.start(
                {**task, "spawn_reservation_key": third.key}, client=client,
                agent="local", project="changed-routing", charter="reviewer", prompt="assigned task",
            )
    else:
        ordinary_bridge.start(
            {**task, "spawn_reservation_key": third.key}, client=client,
            agent="local", project="changed-routing", charter="reviewer", prompt="assigned task",
        )
    assert [call[0] for call in calls].count("create") == 2
    assert [call[0] for call in calls].count("send") == (1 if released_origin else 2)


def test_unrecorded_keyed_creation_reconciles_without_liveness_guess(startup):
    queue, task, _, _ = startup
    import types
    key = task["spawn_reservation_key"]
    queue.record_spawn_allocation(key, "example")
    supervisor = types.SimpleNamespace(
        supervisor_id="supervisor", reserving_timeout=0,
        local_body_verdict_fn=lambda _: pytest.fail("no recorded native handle to probe"),
        client=types.SimpleNamespace(defer_spawn=queue.defer_spawn),
    )
    assert ordinary_bridge.reconcile_created(
        supervisor, asdict(queue.get_reservation(key)), task,
    ) == 1
    retry, admitted = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.creation_key == queue.get_reservation(key).creation_key


def test_replacement_revision_never_recovers_disposed_ordinary_agent(startup):
    queue, task, client, _ = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    first = queue.get_reservation(task["spawn_reservation_key"])
    queue.fail_spawn(first.key, conclusion_state="complete",
                     conclusion_detail=json.dumps({"action": "removed"}))
    replacement, _ = queue.reserve_spawn(task["id"], driver="ordinary-bridge", allocation_revision=2)
    queue.record_spawn_allocation(replacement.key, "example")
    queue.fail_spawn(replacement.key, detail="lost replacement response")
    retry, _ = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert retry.allocation_revision == 2
    assert retry.creation_key == replacement.creation_key != first.creation_key
    assert retry.agent_info is None and retry.worktree is None and retry.session_handle is None


@pytest.mark.parametrize("driver", [None, "cli", "fleet"])
def test_non_bridge_retry_does_not_inherit_ordinary_agent(
    startup: tuple, driver: str | None,
) -> None:
    queue, task, client, _ = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    queue.defer_spawn(task["spawn_reservation_key"], detail="transport interrupted")
    retry, admitted = queue.reserve_spawn(
        task["id"], reserved_by="supervisor", driver=driver)
    assert admitted
    assert retry.driver == driver
    assert retry.agent_info is None
    assert retry.session_handle is None
    assert retry.worktree is None


def test_ordinary_start_checks_native_identity_without_acquisition_receipt(startup):
    queue, task, client, _ = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    from agent_dispatch.acquisition_receipts import start_authority, validate_start
    assert start_authority(queue, task["id"]) is None
    with queue._connect() as conn:
        validate_start(conn, queue.get(task["id"]), "native-a", None)
        with pytest.raises(TaskError, match="ordinary created-agent"):
            validate_start(conn, queue.get(task["id"]), "native-other", None)


@pytest.mark.parametrize("charter", ["reviewer", "task-worker"])
def test_reviewer_and_worker_use_identical_factory_path(startup, monkeypatch, charter):
    _, task, client, calls = startup
    from agent_dispatch.spawn_factories import make_headless_spawn, make_label_routed_spawn
    original = ordinary_bridge.start

    def start(snapshot, **kwargs):
        assert kwargs["charter"] == charter
        return original(snapshot, **{**kwargs, "charter": "reviewer", "prompt": "assigned task"})

    monkeypatch.setattr(ordinary_bridge, "start", start)
    selected = make_headless_spawn(agent="local", charter=charter)
    routed = make_label_routed_spawn(lambda _: (False, {}), overrides={"review": selected})
    snapshot = {**task, "_spawn_client": client, "labels": ["review"]}
    assert routed.ordinary_bridge_for(snapshot)
    ok, handle = routed(snapshot)
    assert ok and handle["session"] == "local-body:bridge-a"
    assert [call[0] for call in calls] == ["create", "store", "store", "send"]


def test_one_shot_uses_no_dispatch_allocation(startup, monkeypatch):
    _, task, client, calls = startup
    import argparse
    from agent_dispatch import create_cli, embody
    monkeypatch.setattr(embody, "prepare_reusable_worktree",
                        lambda *a, **kw: pytest.fail("Dispatch must not allocate ACP worktrees"))
    args = argparse.Namespace(spawn_backend="bridge", spawn_agent="local", spawn_charter="reviewer", run_async=True)
    original = ordinary_bridge.start

    def start(snapshot, **kwargs):
        assert "--worker " not in kwargs["prompt"]
        assert "no owner argument" in kwargs["prompt"]
        assert f"`agent-dispatch start {task['id']}`" in kwargs["prompt"]
        return original(snapshot, **{**kwargs, "prompt": "assigned task"})

    monkeypatch.setattr(ordinary_bridge, "start", start)
    result, _, handle = create_cli._do_spawn(args, {**task, "_spawn_client": client})
    assert result.returncode == 0 and handle["session"] == "local-body:bridge-a"
    assert [call[0] for call in calls] == ["create", "store", "store", "send"]


def test_authenticated_storage_task_info_and_native_start(startup):
    queue, task, client, _ = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator import create_app
    api = TestClient(create_app(queue, token="synthetic-worker", enable_mcp=False))
    key = task["spawn_reservation_key"]
    facts = queue.get_reservation(key).agent_info
    payload = {"task_generation": task["generation"], "agent_info": facts}
    assert api.post(f"/spawn-reservations/{key}/created-agent", json=payload).status_code == 401
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + "synthetic-worker"}
    assert headers["Authorization"].endswith("synthetic-worker")
    assert api.post(f"/spawn-reservations/{key}/created-agent", json=payload, headers=headers).status_code == 200
    shown = api.get(f"/tasks/{task['id']}", headers=headers).json()
    assert shown["spawn_reservation"]["agent_info"]["session_id"] == "bridge-a"
    assert shown["status"] == "queued"
    response = api.post(f"/spawn-reservations/{key}/spawned", headers=headers, json={
        "session_handle": "local-body:bridge-a", "worktree": "worktree-a",
        "session_info": {"session_id": "bridge-a", "outcome": "delivered", "turn_index": 1},
    })
    assert response.status_code == 200
    assert queue.get_reservation(key).agent_info["last_send"]["turn_index"] == 1
    queue.claim_one("dispatch-worker", task_id=task["id"])
    queue.start(task["id"], "dispatch-worker", owner_session_id="native-a")
    assert queue.get(task["id"]).owner_session_id == "native-a"


def test_supervisor_restart_retains_unprompted_agent_and_unrecorded_creation(startup):
    queue, task, client, _ = startup
    import types
    supervisor = types.SimpleNamespace(
        supervisor_id="supervisor", reserving_timeout=0,
        local_body_verdict_fn=lambda _: "gone", local_body_activity_fn=lambda _: "IDLE",
        client=types.SimpleNamespace(defer_spawn=queue.defer_spawn),
    )
    res = asdict(queue.get_reservation(task["spawn_reservation_key"]))
    assert ordinary_bridge.reconcile_created(supervisor, res, task) == 0
    assert queue.get_reservation(res["key"]).state == "reserving"
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    res = asdict(queue.get_reservation(res["key"]))
    assert ordinary_bridge.reconcile_created(supervisor, res, task) == 1
    retry, admitted = queue.reserve_spawn(task["id"], reserved_by="supervisor", driver="ordinary-bridge")
    assert admitted and retry.session_handle == "local-body:bridge-a"
    assert retry.agent_info["connection"]["native_session_id"] == "native-a"


def test_supervisor_preparation_does_not_allocate_and_label_route_keeps_client(startup, monkeypatch):
    _, task, client, _ = startup
    from agent_dispatch import embody
    from agent_dispatch.supervisor import Supervisor
    from agent_dispatch.spawn_factories import make_headless_spawn, make_label_routed_spawn
    monkeypatch.setattr(embody, "prepare_reusable_worktree",
                        lambda *a, **kw: pytest.fail("Bridge owns first allocation"))
    routed = make_label_routed_spawn(lambda _: (False, {}), overrides={
        "review": make_headless_spawn(agent="local", charter="reviewer"),
    })
    supervisor = Supervisor(client, spawn_fn=routed, repo="example")
    snapshot = supervisor._prepare_spawn_task({**task, "labels": ["review"]}, {
        "key": task["spawn_reservation_key"],
    })
    assert snapshot["_spawn_client"] is client
    assert "spawn_worktree" not in snapshot


@pytest.mark.parametrize("failure", ["exit", "timeout", "malformed"])
def test_create_failure_retains_reservation_without_prompt(startup, monkeypatch, failure):
    queue, task, client, calls = startup

    def fail(argv, **kwargs):
        calls.append(("create",))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 1 if failure == "exit" else 0, "invalid", "creation refused")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", fail)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert [call[0] for call in calls] == ["create"]
    assert queue.get_reservation(task["spawn_reservation_key"]).state == "reserving"


def test_supervisor_poll_reserves_creates_stores_sends_and_records(startup, monkeypatch):
    queue, task, _, calls = startup
    from agent_dispatch.supervisor import Supervisor
    from agent_dispatch.spawn_factories import make_headless_spawn
    from tests.test_supervisor import QueueBackedClient
    queue.defer_spawn(task["spawn_reservation_key"], detail="test setup has not created an agent")

    class Client(QueueBackedClient):
        def reserve_spawn(self, task_id, *, reserved_by=None, coordinator_launch=False, driver=None):
            res, ok = queue.reserve_spawn(task_id, reserved_by=reserved_by, driver=driver)
            assert res.driver == "ordinary-bridge"
            return {"reserved": ok, "reservation": asdict(res)}

        def get_reservation(self, key):
            return asdict(queue.get_reservation(key))

        def record_created_agent(self, key, **facts):
            calls.append(("store", facts))
            return asdict(ordinary_bridge.record_created_agent(queue, key, **facts))

        def record_spawn(self, key, *, session_handle=None, worktree=None, session_info=None):
            assert session_info["session_id"] == "bridge-a"
            ordinary_bridge.record_session_info(queue, key, session_info)
            calls.append(("record",))
            return asdict(queue.record_spawn(key, session_handle=session_handle, worktree=worktree))

    original = ordinary_bridge.start
    monkeypatch.setattr(ordinary_bridge, "start", lambda snapshot, **kw:
                        original(snapshot, **{**kw, "prompt": "assigned task"}))
    supervisor = Supervisor(Client(queue), spawn_fn=make_headless_spawn(agent="local", charter="reviewer"),
                            repo="example", machine="host-a")
    assert supervisor.poll_once() == [task["id"]]
    assert [call[0] for call in calls] == ["create", "store", "store", "send", "record"]
    res = queue.latest_reservation(task["id"])
    assert res.agent_info["last_send"]["outcome"] == "delivered"
    assert res.worktree_ownership == "targeted" and res.driver == "ordinary-bridge"


def test_retained_idle_agent_sends_without_invalid_strict_resume(startup, monkeypatch) -> None:
    queue, task, client, calls = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    calls.clear()
    original = ordinary_bridge.subprocess.run

    def idle(argv, **kwargs):
        if argv[2] == "resume":
            pytest.fail("strict resume only accepts stopped sessions")
        if argv[2] == "status":
            calls.append(("status",))
            return subprocess.CompletedProcess(argv, 0, '{"status":"idle"}', "")
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", idle)
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    assert [call[0] for call in calls] == ["store", "status", "store", "send"]
    assert queue.get_reservation(task["spawn_reservation_key"]).session_handle == "local-body:bridge-a"


@pytest.mark.parametrize("change", ["hold", "owner", "generation", "release", "retirement"])
def test_retained_admission_rechecks_changes_during_resume(startup, monkeypatch, change) -> None:
    queue, task, client, calls = startup
    ordinary_bridge.start(task, client=client, agent="local", project="example",
                          charter="reviewer", prompt="assigned task")
    calls.clear()
    original = ordinary_bridge.subprocess.run

    def changed(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[2] == "resume":
            if change == "hold":
                queue.set_hold(task["id"], reason="operator", actor="operator")
            elif change == "owner":
                queue.claim_one("other-owner", task_id=task["id"])
            else:
                with queue._connect() as conn:
                    if change == "generation":
                        conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task["id"],))
                    else:
                        field = "release_requested" if change == "release" else "conversation_retired"
                        conn.execute(f"UPDATE spawn_reservations SET {field}=1 WHERE key=?",
                                     (task["spawn_reservation_key"],))
        return result

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", changed)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="admission fence"):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert "send" not in [call[0] for call in calls]


@pytest.mark.parametrize("failure", ["retained", "preparation"])
def test_retained_failure_consumes_same_poll_capacity(startup, monkeypatch, failure) -> None:
    queue, task, _, _ = startup
    from agent_dispatch.supervisor import Supervisor, SpawnPreparationRetained
    from tests.test_supervisor import QueueBackedClient
    queue.defer_spawn(task["spawn_reservation_key"], detail="no agent created")
    second = queue.create(title="Second work", repo="example")
    attempts = []

    def spawn(snapshot):
        attempts.append(snapshot["id"])
        return False, {"retained": True, "error": "ambiguous creation"}

    supervisor = Supervisor(QueueBackedClient(queue), spawn_fn=spawn, repo="example",
                            max_concurrent=1)
    if failure == "preparation":
        def prepare(snapshot, reservation):
            attempts.append(snapshot["id"])
            raise SpawnPreparationRetained("created identity not recorded")
        monkeypatch.setattr(supervisor, "_prepare_spawn_task", prepare)
    assert supervisor.poll_once() == []
    assert len(attempts) == 1
    assert len(queue.list_reservations(state="reserving")) == 1
    untouched = second.id if attempts[0] == task["id"] else task["id"]
    assert queue.latest_reservation(untouched) is None or queue.latest_reservation(untouched).state == "failed"


@pytest.mark.parametrize("exit_code", [0, 69])
def test_send_diagnostics_preserve_literal_failure(startup, monkeypatch, exit_code) -> None:
    queue, task, client, calls = startup
    original = ordinary_bridge.subprocess.run

    def fail_send(argv, **kwargs):
        if argv[2] == "send":
            return subprocess.CompletedProcess(argv, exit_code, "not-json", "fixture native admission refused")
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", fail_send)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="fixture native admission refused"):
        ordinary_bridge.start(task, client=client, agent="local", project="example",
                              charter="reviewer", prompt="assigned task")
    assert queue.get_reservation(task["spawn_reservation_key"]).agent_info["session_id"] == "bridge-a"


def test_legacy_bound_is_read_only_after_queue_migration(startup, monkeypatch) -> None:
    from contextlib import contextmanager
    queue, task, _, _ = startup
    original = queue._connect

    @contextmanager
    def read_only():
        with original() as conn:
            conn.execute("PRAGMA query_only=ON")
            yield conn

    monkeypatch.setattr(queue, "_connect", read_only)
    assert ordinary_bridge.legacy_bound(queue, task["id"], None) is False


@pytest.mark.parametrize("failure", ["bookkeeping", "retained", "deferred", "unavailable"])
def test_one_shot_spawn_failure_exits_nonzero_without_success_emission(
    startup, monkeypatch, capsys, failure,
) -> None:
    import argparse
    from agent_dispatch import __main__ as core, create_cli
    from agent_dispatch.client import DispatchError
    queue, task, client, _ = startup
    args = argparse.Namespace(run_async=True)
    reservation = {"key": task["spawn_reservation_key"]}

    def fail_record(*a, **kw):
        raise DispatchError(503, "fixture coordinator refused spawn publication")

    client.record_spawn = fail_record
    client.fail_spawn = queue.fail_spawn
    client.defer_spawn = queue.defer_spawn
    if failure == "unavailable":
        spawned = None
    elif failure in ("retained", "deferred"):
        spawned = (subprocess.CompletedProcess([], 1, "", "fixture spawn unavailable"),
                   "agent-bridge", {failure: True})
    else:
        handle = ordinary_bridge.start(task, client=client, agent="local", project="example",
                                       charter="reviewer", prompt="assigned task")
        spawned = (subprocess.CompletedProcess([], 0, "", ""), "agent-bridge", handle)
    monkeypatch.setattr(core, "_do_spawn", lambda *a, **kw: spawned)
    with pytest.raises(SystemExit) as raised:
        create_cli._spawn_ordinary_worker(args, task, reservation, client, route="")
    assert raised.value.code != 0
    output = capsys.readouterr()
    assert not output.out
    assert "reservation" in output.err
    retained = queue.get_reservation(task["spawn_reservation_key"])
    expected = {"deferred": "deferred", "unavailable": "failed"}.get(failure, "reserving")
    assert retained.state == expected
    if failure == "bookkeeping":
        assert retained.agent_info["session_id"] == "bridge-a"


def test_one_shot_reservation_failure_is_nonzero_and_does_not_spawn(startup, monkeypatch) -> None:
    import argparse
    from contextlib import nullcontext
    from agent_dispatch import __main__ as core, create_cli
    from agent_dispatch.client import DispatchError
    queue, task, client, _ = startup

    def fail_reserve(*a, **kw):
        raise DispatchError(503, "fixture reservation unavailable")

    client.reserve_spawn = fail_reserve
    monkeypatch.setattr(core, "_spawn_route", lambda _: "")
    monkeypatch.setattr(core, "_client", lambda _: nullcontext(client))
    monkeypatch.setattr(core, "_do_spawn", lambda *a, **kw: pytest.fail("no admitted reservation"))
    with pytest.raises(SystemExit) as raised:
        create_cli._spawn_worker_for(argparse.Namespace(spawn_backend="bridge"), task)
    assert raised.value.code == 1
    assert queue.get(task["id"]).status == "queued"
