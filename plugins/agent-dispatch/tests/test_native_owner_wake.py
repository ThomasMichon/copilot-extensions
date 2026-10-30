"""Default outbox delivery addresses the captured hosted native conversation."""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from dataclasses import replace

import pytest

from agent_dispatch import bridge
from agent_dispatch.events import EventBus
from agent_dispatch.ordinary_agent_storage import record_created_agent
from agent_dispatch.owner_wake_delivery import deliver_owner_wake
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from agent_dispatch.wake import drain_wake_outbox

OWNER = "host-a/worktree-a"
NATIVE = "native-a"
BRIDGE = "bridge-a"


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", repo="queue-project", source="wake-test")
    reservation, _ = queue.reserve_spawn(
        task.id, reserved_by="supervisor", driver="ordinary-bridge",
    )
    queue.record_spawn_allocation(reservation.key, "execution-project")
    info = {
        "session_id": BRIDGE, "ready": True, "status": "awaiting_first_prompt",
        "connection": {
            "native_session_id": NATIVE, "worktree_id": "worktree-a",
            "repo": "execution-project", "target_dir": str(tmp_path / "worktree-a"),
        },
    }
    record_created_agent(queue, reservation.key, task_generation=task.generation, agent_info=info)
    queue.record_spawn(
        reservation.key, session_handle=f"local-body:{BRIDGE}",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.claim_one(OWNER, task_id=task.id)
    queue.start(task.id, OWNER, owner_session_id=NATIVE)
    queue.suspend(task.id, OWNER, reason="external")
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: ["bridge-python", "-m", "agent_bridge"])
    monkeypatch.setattr(
        bridge, "resume_steered_owner",
        lambda *_args, **_kwargs: pytest.fail("hosted wake must not enter the represented CLI inbox"),
    )
    return queue, task.id


def _capture(monkeypatch, *, returncode=0, unknown_first=False):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        code = 70 if unknown_first and len(calls) == 1 else returncode
        return subprocess.CompletedProcess(argv, code, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", run)
    return calls


def _expected(key):
    return [
        "bridge-python", "-m", "agent_bridge", "--json", "send", BRIDGE,
        "--expected-native-session-id", NATIVE, "--idempotency-key", key,
        "--prompt-file", "-", "--no-wait",
    ]


def test_native_wake_keeps_standard_send_key_and_literal_prompt(hosted, monkeypatch):
    queue, task_id = hosted
    calls = _capture(monkeypatch)
    assert queue.latest_reservation(task_id).agent_info["task_generation"] < queue.get(task_id).generation
    prompt = "Continue with the author's `choice` & (new head)."
    for _ in range(2):
        assert deliver_owner_wake(queue, OWNER, NATIVE, task_id, prompt, "wake-1")
    assert [argv for argv, _ in calls] == [_expected("wake-1")] * 2
    assert all(kwargs["input"] == prompt for _, kwargs in calls)
    assert all("--queue" not in argv and "--force" not in argv for argv, _ in calls)


def test_native_wake_accepts_retained_resume_facts_and_cold_body(hosted, monkeypatch):
    queue, task_id = hosted
    original = queue.latest_reservation(task_id)
    info = {
        "fact_kind": "resumed", "session_id": BRIDGE, "acp_session_id": NATIVE,
        "durable_session_id": NATIVE, "worktree_id": "worktree-a",
        "project": "execution-project", "target_dir": original.worktree_path,
        "status": "idle", "read_only": False, "at_rest": True, "liveness": None,
    }
    monkeypatch.setattr(queue, "latest_reservation", lambda _id: replace(
        original, state="cold", agent_info=info,
    ))
    calls = _capture(monkeypatch)
    assert deliver_owner_wake(queue, OWNER, NATIVE, task_id, None, "wake-1")
    assert calls[0][0] == _expected("wake-1")
    assert f"agent-dispatch steer take {task_id} --all" in calls[0][1]["input"]


def test_native_wake_does_not_guess_local_machine_for_remote_owner(hosted, monkeypatch):
    queue, task_id = hosted
    calls = _capture(monkeypatch)
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: None)
    with pytest.raises(RuntimeError, match="local machine identity"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []


def test_native_wake_fleet_uses_same_standard_command_over_ssh(hosted, monkeypatch):
    queue, task_id = hosted
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET session_handle=?, creating_host=? WHERE task_id=?",
            (f"fleet-body:host-a:{BRIDGE}", "host-a", task_id),
        )
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "coordinator")
    monkeypatch.setattr(bridge.shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge, "run_ssh_command", run)
    assert deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    argv, kwargs = calls[0]
    assert argv[:6] == ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "host-a"]
    assert shlex.split(argv[6]) == ["agent-bridge", *_expected("wake-1")[3:]]
    assert kwargs["input"] == "continue"


@pytest.mark.parametrize("changes", [
    {"session_handle": "local-body:other"},
    {"session_handle": "local-body:"},
    {"session_handle": None},
    {"driver": None, "session_handle": None},
    {"session_handle": "fleet-body:other-host:bridge-a"},
    {"conversation_retired": True},
    {"release_requested": True},
    {"conclusion_state": "pending"},
    {"cooling_token": "unsettled-stop"},
    {"exclusive_key": "review-slot", "exclusive_released": True},
    {"state": "failed"},
    {"agent_info": None},
    {"worktree": "different"},
    {"allocation_project": "different"},
    {"creating_host": "different"},
])
def test_invalid_hosted_facts_never_fall_back_to_cli(hosted, monkeypatch, changes):
    queue, task_id = hosted
    original = queue.latest_reservation(task_id)
    monkeypatch.setattr(queue, "latest_reservation", lambda _id: replace(original, **changes))
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError, match="hosted wake"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []


@pytest.mark.parametrize("mutation", ["native", "worktree", "bridge", "malformed"])
def test_conflicting_agent_identity_is_refused(hosted, monkeypatch, mutation):
    queue, task_id = hosted
    original = queue.latest_reservation(task_id)
    info = json.loads(json.dumps(original.agent_info))
    if mutation == "native":
        info["connection"]["native_session_id"] = "other"
    elif mutation == "worktree":
        info["connection"]["worktree_id"] = "other"
    elif mutation == "bridge":
        info["session_id"] = "other"
    elif mutation == "malformed":
        info["connection"] = {}
    monkeypatch.setattr(queue, "latest_reservation", lambda _id: replace(original, agent_info=info))
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []


@pytest.mark.parametrize("returncode", [1, 2, 69, 70])
def test_native_refusal_or_unknown_never_uses_another_transport(hosted, monkeypatch, returncode):
    queue, task_id = hosted
    calls = _capture(monkeypatch, returncode=returncode)
    assert not deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert [argv for argv, _ in calls] == [_expected("wake-1")]


@pytest.mark.parametrize("kind", ["owner", "waiter"])
@pytest.mark.parametrize("unknown_first", [False, True])
def test_actual_default_drainer_delivers_native_prompt_with_outbox_key(
    hosted, monkeypatch, kind, unknown_first,
):
    queue, task_id = hosted
    if kind == "owner":
        queue.resume(task_id, OWNER, wake_requested=True, wake_message="continue")
        list_wakes = queue.list_wakes
    else:
        waiter = queue.register_run_waiter(
            task_id, pid=101, host="host-a", start_token="token",
            resume_worktree=OWNER, command=["synthetic-wait"],
        )
        queue.recover_dead_run_waiter(
            task_id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
        list_wakes = queue.list_run_waiter_wakes
    [wake] = list_wakes(task_id)
    calls = _capture(monkeypatch, unknown_first=unknown_first)

    async def scenario():
        drain = (
            drain_wake_outbox(queue, EventBus(), interval=0.01, retry_base=0.01)
            if kind == "owner" else drain_run_waiter_wakes(queue, interval=0.01, retry_base=0.01)
        )
        loop = asyncio.create_task(drain)
        try:
            for _ in range(200):
                if list_wakes(task_id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("default wake did not deliver")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert [argv for argv, _ in calls] == [_expected(wake.id)] * (2 if unknown_first else 1)
    assert all(kwargs["input"] == "continue" for _, kwargs in calls)


@pytest.mark.parametrize("reserved", [False, True])
def test_explicit_cli_owner_retains_existing_delivery(tmp_path, monkeypatch, reserved):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("external CLI owner", repo="project")
    if reserved:
        reservation, _ = queue.reserve_spawn(task.id, reserved_by="supervisor", driver="cli")
        queue.record_spawn(reservation.key, session_handle=NATIVE, worktree="worktree-a")
    calls = []
    monkeypatch.setattr(
        bridge, "resume_steered_owner",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )
    assert deliver_owner_wake(queue, OWNER, NATIVE, task.id, "continue", "wake-1")
    assert calls == [((OWNER, task.id, "continue"), {
        "owner_session_id": NATIVE, "idempotency_key": "wake-1",
    })]


def test_retired_history_prevents_native_delivery(hosted, monkeypatch):
    from agent_dispatch import owner_wake_delivery

    queue, task_id = hosted
    monkeypatch.setattr(owner_wake_delivery, "native_bindings", lambda *_args: ([NATIVE], [NATIVE]))
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError, match="retired native"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []
