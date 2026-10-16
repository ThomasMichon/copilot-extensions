"""Current assigned-worktree identity reconciles legacy bookkeeping safely."""

from __future__ import annotations

import copy
import json
import multiprocessing
import subprocess
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import bridge, ordinary_assignment, ordinary_bridge
from agent_dispatch.acquisition_receipts import validate_start
from agent_dispatch.client import DispatchClient
from agent_dispatch.ordinary_agent_storage import reservation_native_facts
from agent_dispatch.queue import TaskError, TaskQueue

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.fixture
def assigned(tmp_path, monkeypatch, request):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", repo="queue-lane", exclusive_key="review-slot",
                        **getattr(request, "param", {}))
    reservation, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="ordinary-bridge")
    queue.record_spawn_allocation(reservation.key, "execution-project")
    worktree_path = str(tmp_path / "worktree-a")
    queue.record_spawn_worktree(
        reservation.key, "worktree-a", ownership="targeted", driver="ordinary-bridge",
        allocation_project="execution-project", worktree_path=worktree_path,
    )
    snapshot = {**asdict(task), "spawn_reservation_key": reservation.key}
    record = {
        "id": "worktree-a", "repo": "execution-project", "path": worktree_path,
        "status": "active", "session_count": 1, "last_session_id": "native-a",
        "creation_nonce": "original-incarnation", "sessions_indexed": True,
        "execution_leg": None, "execution_leg_opaque": False,
        "session_backend": None, "session_backend_opaque": False,
        "dispatch_attempt": {
            "task_id": task.id, "reservation_key": reservation.key,
            "attempt": reservation.attempt, "driver": "ordinary-bridge",
            "supervisor": "supervisor", "creator_machine": "example-owner",
            "ownership": "created",
        },
        "dispatch_attempt_opaque": False,
    }
    response = {
        "session_id": "bridge-a", "acp_session_id": "native-a",
        "durable_session_id": "native-a", "worktree_id": "worktree-a",
        "project": "execution-project", "target_dir": worktree_path,
        "status": "idle", "read_only": False, "at_rest": True, "liveness": None,
    }
    calls = []

    class Client:
        def get(self, task_id):
            return asdict(queue.get(task_id))

        def get_reservation(self, key):
            return {
                **asdict(queue.get_reservation(key)),
                "legacy_acquisition": ordinary_bridge.legacy_bound(queue, task.id, "worktree-a"),
                **reservation_native_facts(queue, key),
            }

        def record_spawn_allocation(self, key, project):
            return asdict(queue.record_spawn_allocation(key, project))

        def record_created_agent(self, key, **facts):
            calls.append("created-store")
            return asdict(ordinary_bridge.record_created_agent(queue, key, **facts))

        def record_resumed_agent(self, key, **facts):
            calls.append("resumed-store")
            return asdict(ordinary_bridge.record_resumed_agent(queue, key, **facts))

    def run(argv, **kwargs):
        if argv[0] == "ground":
            assert argv == [
                "ground", "--project", "execution-project", "record",
                "--worktree-id", "worktree-a", "--json",
            ]
            assert "env" in kwargs
            calls.append("record")
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"version": 1, "ok": True, "worktree": record}), "",
            )
        assert argv[0] == "bridge" and argv[1] == "--json"
        command = argv[2]
        calls.append(command)
        if command == "resume":
            assert argv == ["bridge", "--json", "resume", "worktree-a", "--strict"]
            assert kwargs.get("input") is None
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": response["session_id"], "status": "idle", "verb": "loaded",
            }), "")
        if command == "status":
            assert argv == ["bridge", "--json", "status", "bridge-a", "--full"]
            return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")
        if command == "create":
            assert "--worktree-id" in argv
            assert argv[argv.index("--worktree-id") + 1] == "worktree-a"
            assert argv[argv.index("--creation-key") + 1] == reservation.creation_key
            assert argv[3] == "execution-project@local"
            assert kwargs.get("input") is None
            record.update(last_session_id="native-a", session_count=1)
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
                "connection": {"worktree_id": "worktree-a", "repo": "execution-project",
                               "target_dir": worktree_path, "native_session_id": "native-a"},
            }), "")
        assert command == "send"
        saved = queue.get_reservation(snapshot["spawn_reservation_key"])
        assert saved.agent_info["session_id"] == response["session_id"]
        assert saved.worktree == "worktree-a"
        assert argv[3] == response["session_id"]
        assert argv[argv.index("--expected-native-session-id") + 1] == response["acp_session_id"]
        assert f"--owner-session-id {response['acp_session_id']}" in kwargs["input"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "session_id": response["session_id"], "native_session_id": response["acp_session_id"],
            "outcome": "delivered", "turn_index": 1,
        }), "")

    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(ordinary_assignment, "agent_worktrees_launch_prefix", lambda: ["ground"])
    process_api = SimpleNamespace(**vars(subprocess))
    process_api.run = run
    monkeypatch.setattr(ordinary_bridge, "subprocess", process_api)
    monkeypatch.setattr(ordinary_assignment, "subprocess", process_api)
    monkeypatch.setattr(bridge, "registered_agent_project", lambda *_a, **_kw: "changed-routing")
    return queue, snapshot, Client(), record, response, calls


def _legacy(queue, task, *, native="native-a", handle="legacy-bridge"):
    key = task["spawn_reservation_key"]
    with queue._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET session_handle=? WHERE key=?",
                     (f"local-body:{handle}", key))
        conn.execute("INSERT INTO acquisition_markers VALUES (?)", (key,))
        conn.execute(
            "INSERT INTO acquisition_receipts VALUES (?,?,?,?,?,?,?,?)",
            (key, task["id"], task["generation"], "worktree-a", handle, native, "acp",
             f"old-delivery:{key}"),
        )


def _start(assigned):
    _, task, client, _, _, _ = assigned
    return ordinary_bridge.start(
        task, client=client, agent="local", project="changed-routing", prompt="assigned work",
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_ordinary_assignment_resumes_same_native_before_one_send(assigned, legacy):
    queue, task, _, _, response, calls = assigned
    if legacy:
        _legacy(queue, task)
    with queue._connect() as conn:
        before = list(conn.execute("SELECT * FROM acquisition_receipts"))
    before_task = queue.get(task["id"])
    result = _start(assigned)
    assert result["worktree"] == "worktree-a" and result["native_session_id"] == "native-a"
    assert calls == ["record", "resume", "status", "record", "resumed-store", "record", "resumed-store", "send"]
    info = queue.get_reservation(task["spawn_reservation_key"]).agent_info
    assert info["status"] == "idle" and info["durable_session_id"] == "native-a"
    assert "ready" not in info and "connection" not in info
    assert {key: info[key] for key in response} == response
    assert queue.get(task["id"]) == before_task
    with queue._connect() as conn:
        assert list(conn.execute("SELECT * FROM acquisition_receipts")) == before
        validate_start(conn, queue.get(task["id"]), "native-a", None)
        with pytest.raises(TaskError):
            validate_start(conn, queue.get(task["id"]), "wrong-native", None)


def test_ordinary_assignment_same_legacy_bridge_handle_is_retained(assigned):
    queue, task, _, _, _, _ = assigned
    _legacy(queue, task, handle="bridge-a")
    assert _start(assigned)["session"] == "local-body:bridge-a"


def test_ordinary_assignment_positive_sessionless_creation_keeps_original_key(assigned):
    queue, task, _, record, _, calls = assigned
    record.update(last_session_id=None, session_count=0)
    with queue._connect() as conn:
        conn.execute("INSERT INTO acquisition_markers VALUES (?)", (task["spawn_reservation_key"],))
    before_key = queue.get_reservation(task["spawn_reservation_key"]).creation_key
    _start(assigned)
    assert calls.count("create") == 1 and calls.count("send") == 1 and "resume" not in calls
    assert queue.get_reservation(task["spawn_reservation_key"]).creation_key == before_key


@pytest.mark.parametrize("change", [
    {"last_session_id": None, "session_count": None},
    {"last_session_id": None, "session_count": 2},
    {"last_session_id": None, "session_count": 0, "execution_leg": {"opaque": True}},
    {"native_liveness": "unknown"}, {"native_liveness": "live"},
    {"status": "removed"}, {"path": "other-path"},
    {"repo": "foreign-project"}, {"id": "other-worktree"},
    {"dispatch_attempt": {"task_id": "foreign-task"}},
])
def test_ordinary_assignment_unproven_owner_never_resumes_or_creates(assigned, change):
    _, _, _, record, _, calls = assigned
    record.update(change)
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        _start(assigned)
    assert calls == ["record"]


def test_ordinary_assignment_legacy_native_mismatch_refuses_before_resume(assigned):
    queue, task, _, _, _, calls = assigned
    _legacy(queue, task, native="another-native")
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="known or retired"):
        _start(assigned)
    assert calls == ["record"]


@pytest.mark.parametrize("retirement_requested", [False, True])
def test_ordinary_assignment_distinguishes_released_exclusivity_from_native_retirement(
    assigned, retirement_requested,
):
    queue, task, _, _, _, calls = assigned
    _legacy(queue, task)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state='failed',release_requested=?,"
            "exclusive_released=? WHERE key=?",
            (int(retirement_requested), int(not retirement_requested), task["spawn_reservation_key"]),
        )
    current, reserved = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert reserved
    task["spawn_reservation_key"] = current.key
    facts = reservation_native_facts(queue, current.key)
    assert facts["retired_native_session_ids"] == (["native-a"] if retirement_requested else [])
    if retirement_requested:
        with pytest.raises(bridge.BridgeCarriedSessionBusy):
            _start(assigned)
        assert calls == ["record"]
    else:
        _start(assigned)
        assert calls.count("resume") == calls.count("send") == 1
        assert "create" not in calls


@pytest.mark.parametrize("old_retired", [False, True])
def test_ordinary_assignment_current_head_respects_unretired_not_retired_old_natives(
    assigned, old_retired,
):
    queue, task, _, record, response, calls = assigned
    _legacy(queue, task)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET state='failed',release_requested=?,"
            "exclusive_released=1 WHERE key=?",
            (int(old_retired), task["spawn_reservation_key"]),
        )
    current, reserved = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert reserved
    task["spawn_reservation_key"] = current.key
    record.update(last_session_id="native-b", session_count=2)
    response.update(acp_session_id="native-b", durable_session_id="native-b")
    assert current.conversation_retired is old_retired
    assert current.session_handle == (None if old_retired else "local-body:legacy-bridge")
    if old_retired:
        _start(assigned)
        assert calls.count("resume") == calls.count("send") == 1
        assert "create" not in calls
        assert queue.get_reservation(current.key).agent_info["acp_session_id"] == "native-b"
        assert queue.get_reservation(current.key).conversation_retired is False
        assert queue.get_reservation(current.key).creation_key == current.creation_key
        with queue._connect() as conn:
            validate_start(conn, queue.get(task["id"]), "native-b", None)
            with pytest.raises(TaskError):
                validate_start(conn, queue.get(task["id"]), "native-a", None)
    else:
        with pytest.raises(bridge.BridgeCarriedSessionBusy, match="known or retired"):
            _start(assigned)
        assert calls == ["record"]


@pytest.mark.parametrize("version", [True, 1.0, None, "1", 2])
def test_ordinary_assignment_rejects_incompatible_exact_owner_envelope(
    assigned, monkeypatch, version,
):
    queue, task, _, record, _, calls = assigned
    original = ordinary_bridge.subprocess.run

    def incompatible(argv, **kwargs):
        if argv[0] == "ground":
            calls.append("record")
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"version": version, "ok": True, "worktree": record}), "",
            )
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", incompatible)
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        _start(assigned)
    assert calls == ["record"]
    assert queue.get_reservation(task["spawn_reservation_key"]).agent_info is None


@pytest.mark.parametrize("missing_project", [False, True])
def test_ordinary_assignment_reconciles_missing_metadata_from_exact_current_owner(
    assigned, monkeypatch, missing_project,
):
    queue, task, _, record, _, calls = assigned
    _legacy(queue, task)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET allocation_project=?,worktree_path=NULL WHERE key=?",
            (None if missing_project else "execution-project", task["spawn_reservation_key"]),
        )
    monkeypatch.setattr(
        bridge, "registered_agent_project", lambda *_a, **_kw: "execution-project",
    )
    _start(assigned)
    stored = queue.get_reservation(task["spawn_reservation_key"])
    assert stored.allocation_project == "execution-project"
    assert stored.worktree_path == record["path"]
    assert stored.agent_info["acp_session_id"] == "native-a"
    assert calls.count("resume") == calls.count("send") == 1
    assert "create" not in calls


@pytest.mark.parametrize("flag", ["sessions_indexed", "execution_leg_opaque", "session_backend_opaque"])
def test_ordinary_assignment_unknown_history_flags_never_create(assigned, flag):
    _, _, _, record, _, calls = assigned
    record.update(last_session_id=None, session_count=0)
    record[flag] = False if flag == "sessions_indexed" else True
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        _start(assigned)
    assert calls == ["record"]


def test_ordinary_assignment_failed_namespace_lookup_does_not_bind_missing_history(
    assigned, monkeypatch,
):
    queue, task, _, _, _, calls = assigned
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET allocation_project=NULL,worktree_path=NULL WHERE key=?",
            (task["spawn_reservation_key"],),
        )

    def missing_owner(argv, **kwargs):
        assert argv[0] == "ground"
        calls.append("record")
        return subprocess.CompletedProcess(argv, 1, "", "exact record unavailable")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", missing_owner)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="exact record unavailable"):
        _start(assigned)
    stored = queue.get_reservation(task["spawn_reservation_key"])
    assert stored.allocation_project is None and stored.worktree_path is None
    assert stored.agent_info is None
    assert calls == ["record"]


@pytest.mark.parametrize("ownership", ["created", "reused", "targeted"])
def test_ordinary_assignment_preserves_original_workspace_ownership(assigned, ownership):
    queue, task, _, _, _, _ = assigned
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree_ownership=? WHERE key=?",
            (ownership, task["spawn_reservation_key"]),
        )
    _start(assigned)
    assert queue.get_reservation(task["spawn_reservation_key"]).worktree_ownership == ownership


def test_ordinary_fresh_creation_does_not_claim_bridge_workspace_ownership(assigned):
    queue, _, _, record, _, _ = assigned
    task = queue.create("new assignment", repo="queue-lane")
    reservation, _ = queue.reserve_spawn(task.id, driver="ordinary-bridge")
    info = {
        "session_id": "fresh-bridge", "ready": True, "status": "awaiting_first_prompt",
        "connection": {
            "worktree_id": "fresh-worktree", "native_session_id": "fresh-native",
            "repo": "execution-project", "target_dir": record["path"] + "-fresh",
        },
    }
    stored = ordinary_bridge.record_created_agent(
        queue, reservation.key, task_generation=task.generation, agent_info=info,
    )
    assert stored.worktree_ownership == "targeted"


@pytest.mark.parametrize("change", [
    {"acp_session_id": "other-native"}, {"durable_session_id": "other-native"},
    {"target_dir": "other-path"}, {"project": "other-project"}, {"worktree_id": "other-worktree"},
    {"status": "running"}, {"read_only": True}, {"at_rest": None}, {"liveness": "unknown"},
    {"ready": True},
])
def test_ordinary_assignment_resume_facts_must_be_actual_and_exact(assigned, change):
    queue, task, _, _, response, calls = assigned
    response.update(change)
    with pytest.raises(bridge.BridgeCarriedSessionBusy):
        _start(assigned)
    assert calls == ["record", "resume", "status"]
    assert queue.get_reservation(task["spawn_reservation_key"]).agent_info is None


def test_ordinary_assignment_resume_failure_never_falls_back_to_create(assigned, monkeypatch):
    _, _, _, _, _, calls = assigned
    original = ordinary_bridge.subprocess.run

    def failed(argv, **kwargs):
        if argv[0] == "bridge":
            calls.append("resume")
            return subprocess.CompletedProcess(argv, 69, "", "native history is unproven")
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", failed)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="native history is unproven"):
        _start(assigned)
    assert calls == ["record", "resume"]


@pytest.mark.parametrize("change", ["hold", "owner", "generation", "release", "retirement", "exclusive"])
def test_ordinary_assignment_rechecks_fences_before_first_prompt(assigned, monkeypatch, change):
    queue, task, _, _, _, calls = assigned
    original = ordinary_bridge.subprocess.run

    def changed(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[0] == "bridge" and argv[2] == "resume":
            if change == "hold":
                queue.set_hold(task["id"], reason="operator", actor="operator")
            elif change == "owner":
                queue.claim_one("foreign-owner", task_id=task["id"])
            else:
                with queue._connect() as conn:
                    if change == "generation":
                        conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (task["id"],))
                    else:
                        field = {"release": "release_requested", "retirement": "conversation_retired",
                                 "exclusive": "exclusive_released"}[change]
                        conn.execute(f"UPDATE spawn_reservations SET {field}=1 WHERE key=?",
                                     (task["spawn_reservation_key"],))
        return result

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", changed)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="admission fence"):
        _start(assigned)
    assert "send" not in calls and "create" not in calls


def test_ordinary_assignment_foreign_reservation_refuses_before_native_effects(assigned):
    queue, _, _, _, _, calls = assigned
    other = queue.create("other", repo="queue-lane")
    reservation, _ = queue.reserve_spawn(other.id, driver="ordinary-bridge")
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="targeted")
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="another active owner"):
        _start(assigned)
    assert not calls


def test_ordinary_assignment_held_task_refuses_before_native_effects(assigned):
    queue, task, _, _, _, calls = assigned
    queue.set_hold(task["id"], reason="operator", actor="operator")
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="admission fence"):
        _start(assigned)
    assert not calls


def _record_same_agent(db_path, key, generation, info, record, barrier, results):
    queue = TaskQueue(db_path)
    barrier.wait(timeout=15)
    result = ordinary_bridge.record_resumed_agent(
        queue, key, task_generation=generation, session_info=info, worktree_record=record,
    )
    results.put((result.session_handle, result.agent_info))


def test_ordinary_assignment_concurrent_native_recording_is_idempotent(assigned):
    queue, task, _, record, response, _ = assigned
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(3), ctx.Queue()
    processes = [
        ctx.Process(target=_record_same_agent, args=(
            queue.db_path, task["spawn_reservation_key"], task["generation"],
            response, record, barrier, results,
        )) for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        barrier.wait(timeout=15)
        for process in processes:
            process.join(timeout=30)
            assert process.exitcode == 0
        assert results.get(timeout=5) == results.get(timeout=5)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            process.close()
        results.close()
        results.join_thread()


def test_ordinary_assignment_resumed_client_route_and_raw_facts(assigned):
    queue, task, _, record, response, _ = assigned
    api = TestClient(create_app_for(queue))

    def exchange(request):
        result = api.request(request.method, str(request.url), content=request.content,
                             headers=dict(request.headers))
        return httpx.Response(result.status_code, json=result.json())

    try:
        with DispatchClient("http://testserver", transport=httpx.MockTransport(exchange)) as client:
            result = client.record_resumed_agent(
                task["spawn_reservation_key"], task_generation=task["generation"],
                session_info=copy.deepcopy(response), worktree_record=record,
            )
            assert result["agent_info"]["acp_session_id"] == "native-a"
            assert result["agent_info"]["fact_kind"] == "resumed"
            assert "ready" not in result["agent_info"]
    finally:
        api.close()


def create_app_for(queue):
    from agent_dispatch.coordinator import create_app
    return create_app(queue, enable_mcp=False)
