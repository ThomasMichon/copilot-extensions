"""A granted replacement is stored before send and never resumes retired history."""

import copy
from dataclasses import asdict
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import ordinary_bridge, ordinary_replacement
from agent_dispatch.coordinator import create_app
from agent_dispatch.ordinary_agent_storage import record_created_agent, reservation_native_facts
from agent_dispatch.queue_records import TaskError
from tests.test_ordinary_assignment import assigned as _assigned
from tests._hosting_owner import admission_owner as admission_owner

assigned = _assigned
OLD = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"
pytestmark = [
    pytest.mark.contract("agent_dispatch.ordinary_bridge"),
    pytest.mark.parametrize("assigned", [{"affinity": {"worktree": "worktree-a"}}], indirect=True),
]


@pytest.fixture
def replacement_start(assigned, monkeypatch):
    queue, task, client, record, response, calls = assigned
    previous = task["spawn_reservation_key"]
    queue.defer_spawn(previous, detail="stopped native head")
    record.update(last_session_id=OLD, head_revision=3, lifecycle_revision=5)
    grant = ordinary_replacement.authorize(
        queue, task["id"], reservation_key=previous, worktree=record["id"],
        expected_generation=task["generation"], expected_head=OLD,
        expected_head_revision=3, expected_lifecycle_revision=5, worktree_record=record,
        permitted=True, confirmed_no_live_holder=True, reason="explicit replacement",
    )
    attempt, _ = queue.reserve_spawn(task["id"], driver="ordinary-bridge")
    task.update(asdict(queue.get(task["id"])), spawn_reservation_key=attempt.key)
    response.update(acp_session_id=NEW, durable_session_id=NEW)
    info = {
        "session_id": "bridge-a", "ready": True, "status": "awaiting_first_prompt",
        "creation": {
            "version": 1, "status": "complete", "turn_index": None,
            "native_session_id": NEW, "worktree": {"creation_nonce": record["creation_nonce"]},
            "native_replacement": {**grant, "replacement_session": NEW, "head_revision": 7},
        },
        "connection": {
            "worktree_id": record["id"], "repo": record["repo"], "target_dir": record["path"],
            "native_session_id": NEW,
        },
    }
    run = ordinary_bridge.subprocess.run

    def replacement_run(argv, **kwargs):
        if argv[:3] != ["bridge", "--json", "create"]:
            assert "resume" not in argv
            return run(argv, **kwargs)
        calls.append("create")
        assert argv[argv.index("--creation-key") + 1] == grant["replacement_creation_key"]
        assert argv[argv.index("--replacement-task") + 1] == task["id"]
        assert argv[argv.index("--min-daemon-protocol") + 1] == "52"
        assert argv[argv.index("--worktree-id") + 1] == record["id"]
        assert kwargs.get("input") is None
        record.update(last_session_id=NEW, head_revision=7, lifecycle_revision=7, session_count=2)
        return subprocess.CompletedProcess(argv, 0, json.dumps(info), "")

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", replacement_run)
    return assigned, grant, previous, info


def start(context):
    _, task, client, _, _, _ = context
    return ordinary_bridge.start(task, client=client, agent="local", prompt="continue assigned work")


def test_granted_replacement_stores_before_send_and_keeps_history(replacement_start):
    context, grant, previous, _ = replacement_start
    queue, task, client, _, _, calls = context
    key = task["spawn_reservation_key"]
    assert client.get_reservation(key)["native_replacement_authorization"] == grant
    result = start(context)
    assert result["native_session_id"] == NEW
    assert calls.index("create") < calls.index("created-store") < calls.index("send")
    current = client.get_reservation(key)
    assert current["conversation_retired"] is False
    assert current["native_replacement_authorization"] is None
    assert current["retired_native_session_ids"] == [OLD]
    assert current["agent_info"]["startup_invocation"]["binding"]["admission_generation"] == 1
    historical = queue.get_reservation(previous)
    assert historical.conversation_retired is True
    assert historical.agent_info is None
    assert historical.creation_key == current["creation_key"] == grant["creation_key"]
    start(context)
    assert calls.count("create") == 1


@pytest.mark.parametrize("change", ["proof", "revision", "native", "nonce", "turn", "generation"])
def test_storage_refuses_unproven_replacement_without_erasing_retirement(replacement_start, change):
    context, _, _, info = replacement_start
    queue, task, _, record, _, _ = context
    key = task["spawn_reservation_key"]
    record.update(last_session_id=NEW, head_revision=7, lifecycle_revision=7, session_count=2)
    info = copy.deepcopy(info)
    if change == "proof":
        info["creation"].pop("native_replacement")
    elif change == "revision":
        record["head_revision"] = 8
    elif change == "native":
        info["creation"]["native_replacement"]["replacement_session"] = OLD
    elif change == "nonce":
        record["creation_nonce"] = "different-allocation"
    elif change == "turn":
        info["creation"]["turn_index"] = 0
    else:
        info["creation"]["native_replacement"]["task_generation"] = 0
    before = asdict(queue.get_reservation(key))
    with pytest.raises(TaskError):
        record_created_agent(
            queue, key, task_generation=task["generation"], agent_info=info, worktree_record=record,
        )
    assert asdict(queue.get_reservation(key)) == before
    assert reservation_native_facts(queue, key)["retired_native_session_ids"] == [OLD]


def test_failed_storage_never_sends_and_reuses_creation_key(replacement_start, monkeypatch):
    context, _, _, _ = replacement_start
    _, _, client, _, _, calls = context
    store = client.record_created_agent

    def unavailable(*args, **kwargs):
        raise TaskError("storage unavailable")

    monkeypatch.setattr(client, "record_created_agent", unavailable)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="no prompt sent"):
        start(context)
    assert "send" not in calls
    monkeypatch.setattr(client, "record_created_agent", store)
    result = start(context)
    assert result["native_session_id"] == NEW
    assert calls.count("create") == 2
    assert calls.count("send") == 1


def test_storage_route_reads_ground_owner_instead_of_caller_record(replacement_start, monkeypatch):
    from agent_dispatch import ordinary_assignment

    context, _, _, info = replacement_start
    queue, task, _, record, _, _ = context
    key = task["spawn_reservation_key"]
    reads = []

    def read(reservation, task_id, **kwargs):
        assert reservation["key"] == key and task_id == task["id"]
        reads.append(dict(record))
        return dict(record)

    monkeypatch.setattr(ordinary_assignment, "read_owner", read)
    client = TestClient(create_app(queue))
    body = {
        "task_generation": task["generation"], "agent_info": info,
        "worktree_record": {
            **record, "last_session_id": NEW, "head_revision": 7,
            "lifecycle_revision": 7, "session_count": 2,
        },
    }
    response = client.post(f"/spawn-reservations/{key}/created-agent", json=body)
    assert response.status_code == 409
    assert queue.get_reservation(key).conversation_retired
    record.update(last_session_id=NEW, head_revision=7, lifecycle_revision=7, session_count=2)
    response = client.post(f"/spawn-reservations/{key}/created-agent", json=body)
    assert response.status_code == 200
    assert response.json()["conversation_retired"] is False
    assert [row["last_session_id"] for row in reads] == [OLD, NEW]
