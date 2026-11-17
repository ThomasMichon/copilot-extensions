"""A new managed control record must retain the original native and evidence."""

from __future__ import annotations

from typing import Any
import copy
import hashlib
import json
import sqlite3
from dataclasses import asdict

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import ordinary_bridge
from agent_dispatch.bridge import BridgeCarriedSessionBusy
from agent_dispatch.client import DispatchClient
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.ordinary_agent_storage import record_resumed_agent
from agent_dispatch.ordinary_control_adoption import ADOPTION, HISTORY
from agent_dispatch.queue_records import TaskError
from agent_dispatch.queue_pool_placement import pool_placement_contract
from agent_dispatch.queue_task_relationships import WRITER
from agent_dispatch.queue import TaskQueue
from tests.test_ordinary_assignment import (
    _qualified_start, _required_writer, _start, assigned as assigned, create_app_for,
)

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.fixture
def adopting(assigned: Any) -> Any:
    queue, task, _, record, response, _ = assigned
    _required_writer(assigned)
    original_send = _qualified_start(assigned)
    key = task["spawn_reservation_key"]
    ordinary_bridge.record_session_info(queue, key, original_send["session_info"])
    before = asdict(queue.get_reservation(key))
    digest = hashlib.sha256(json.dumps([
        record["repo"], before["creation_key"],
    ]).encode()).hexdigest()
    info = {
        **response, "session_id": "adopted-bridge",
        "worktree_writer_requirement": copy.deepcopy(task["_writer_requirement"]),
        ADOPTION: {
            "version": 1, "predecessor_id": "bridge-a", "session_id": "adopted-bridge",
            "request": {
                "version": 1, "operation_id": "ordinary-resume:original",
                "journal_key": digest, "native_session_id": "native-a",
                "head_revision": 5, "bridge_installation": "/bridge",
            },
            "history": {
                "status": "unavailable", "predecessor_id": "bridge-a",
                "event_epoch": "adopted-bridge", "turn_epoch": "adopted-bridge",
            },
            "prior_operations": ["settled-not-sent", "unresolved-original"],
        },
        "worktree_writer_observation": {
            "schema": "copilot-extensions.creation-worktree-writer", "version": 1,
            "key_digest": digest, "session_id": "adopted-bridge", "native_session_id": "native-a",
            "bridge_instance": {"installation_root": "/bridge", "pid": 42, "generation": 7},
            "project": record["repo"], "worktree_id": record["id"], "worktree_path": record["path"],
            "worktree_writer": copy.deepcopy(task["_writer_requirement"]),
            "executing_writer_identity": {
                "installation_root": "/worktrees", "runtime_generation": "b" * 32,
            },
            "writer_scope": "executing-process", "writer_contract": WRITER,
            "retained_session_writes": 1, "automatic_recovery_stopped": False,
        },
    }
    return assigned, before, info


def _record(adopting: Any, info: Any=None) -> Any:
    assigned, before, proposed = adopting
    queue, task, _, record, _, _ = assigned
    return record_resumed_agent(
        queue, before["key"], task_generation=task["generation"],
        session_info=info if info is not None else proposed,
        worktree_record=record, startup_invocation=before["agent_info"]["startup_invocation"],
    )


def test_control_adoption_commits_history_without_task_or_startup_acceptance(adopting: Any) -> None:
    assigned, before, info = adopting
    queue, task, _, _, _, calls = assigned
    original_task = asdict(queue.get(task["id"]))
    calls.clear()
    stored = _record(adopting)
    assert stored.session_handle == "local-body:adopted-bridge"
    assert stored.agent_info[HISTORY]["predecessor"] == before["agent_info"]
    assert stored.agent_info[HISTORY]["adoption"] == info[ADOPTION]
    assert stored.agent_info["startup_invocation"] == before["agent_info"]["startup_invocation"]
    assert "last_send" not in stored.agent_info
    assert stored.agent_info["durable_session_id"] == "native-a"
    assert {key: asdict(stored)[key] for key in before if key not in {
        "agent_info", "session_handle", "updated_at",
    }} == {key: value for key, value in before.items() if key not in {
        "agent_info", "session_handle", "updated_at",
    }}
    assert asdict(queue.get(task["id"])) == original_task
    assert calls == []
    assert _record(adopting).agent_info == stored.agent_info
    with pytest.raises(BridgeCarriedSessionBusy, match="excluded after control adoption"):
        _start(assigned)
    assert not calls
    with pytest.raises(TaskError, match="predecessor control epoch"):
        ordinary_bridge.record_session_info(queue, before["key"], {
            "session_id": "adopted-bridge",
            **before["agent_info"]["startup_invocation"],
        })
    claimed = queue.claim_one("worker", task_id=task["id"])
    assert claimed is not None
    with pytest.raises(TaskError):
        queue.start(task["id"], "worker", owner_session_id="other-native")
    started = queue.start(task["id"], "worker", owner_session_id="native-a")
    assert started.status == "started" and started.owner_session_id == "native-a"


@pytest.mark.parametrize("change", [
    "native", "predecessor", "journal", "missing-history", "writer-installation",
    "writer-nonce", "writer-key", "writer-missing", "stop-hold", "new-epoch-history",
    "original-invocation", "task-generation", "released-reservation",
])
def test_control_adoption_refuses_changed_owner_and_preserves_every_row(adopting: Any, change: Any) -> None:
    assigned, before, info = adopting
    queue, task, _, _, _, _ = assigned
    if change == "native":
        info.update(acp_session_id="other", durable_session_id="other")
    elif change == "predecessor":
        info[ADOPTION]["predecessor_id"] = "foreign-control"
    elif change == "journal":
        info[ADOPTION]["request"]["journal_key"] = "f" * 64
    elif change == "missing-history":
        info[ADOPTION].pop("history")
    elif change == "writer-installation":
        info["worktree_writer_observation"]["executing_writer_identity"]["installation_root"] = "/foreign"
    elif change == "writer-nonce":
        info["worktree_writer_observation"]["worktree_writer"]["creation_nonce"] = "foreign"
    elif change == "writer-key":
        info["worktree_writer_observation"]["key_digest"] = "f" * 64
    elif change == "writer-missing":
        info.pop("worktree_writer_observation")
    elif change == "stop-hold":
        info["worktree_writer_observation"]["automatic_recovery_stopped"] = True
    elif change == "new-epoch-history":
        info[ADOPTION]["history"]["status"] = "complete"
    elif change == "original-invocation":
        before["agent_info"]["startup_invocation"]["prompt"] += " changed"
    elif change == "task-generation":
        task["generation"] += 1
    else:
        with queue._connect() as conn:
            conn.execute("UPDATE spawn_reservations SET release_requested=1 WHERE key=?", (before["key"],))
    reservation_before = asdict(queue.get_reservation(before["key"]))
    task_before = asdict(queue.get(task["id"]))
    with pytest.raises(TaskError):
        _record(adopting)
    assert asdict(queue.get_reservation(before["key"])) == reservation_before
    assert asdict(queue.get(task["id"])) == task_before


def test_control_adoption_refresh_cannot_erase_or_change_predecessor_evidence(adopting: Any) -> None:
    stored = _record(adopting)
    _, _, info = adopting
    refreshed = _record(adopting, {**info, "at_rest": False, "liveness": "active"})
    assert refreshed.agent_info[HISTORY] == stored.agent_info[HISTORY]
    changed = copy.deepcopy(refreshed.agent_info)
    changed[HISTORY]["predecessor"]["startup_invocation"]["prompt"] += " changed"
    with pytest.raises(TaskError, match="owner evidence"):
        _record(adopting, changed)
    changed = copy.deepcopy(info)
    changed[ADOPTION]["prior_operations"] = []
    with pytest.raises(TaskError, match="owner evidence"):
        _record(adopting, changed)


def test_control_adoption_lost_reply_and_competing_epoch_preserve_the_committed_winner(adopting: Any) -> None:
    assigned, before, info = adopting
    queue, task, _, record, _, _ = assigned
    stored = _record(adopting)
    restarted = TaskQueue(queue.db_path)
    replay = record_resumed_agent(
        restarted, before["key"], task_generation=task["generation"],
        session_info=info, worktree_record=record,
        startup_invocation=before["agent_info"]["startup_invocation"],
    )
    assert replay.agent_info == stored.agent_info
    competitor = copy.deepcopy(info)
    competitor["session_id"] = "competing-control"
    competitor[ADOPTION]["session_id"] = "competing-control"
    competitor[ADOPTION]["history"].update(event_epoch="competing-control", turn_epoch="competing-control")
    competitor["worktree_writer_observation"]["session_id"] = "competing-control"
    with pytest.raises(TaskError, match="owner evidence"):
        record_resumed_agent(
            restarted, before["key"], task_generation=task["generation"],
            session_info=competitor, worktree_record=record,
            startup_invocation=before["agent_info"]["startup_invocation"],
        )
    winner = restarted.get_reservation(before["key"])
    assert winner is not None and winner.agent_info == stored.agent_info


def test_control_adoption_rollback_recorder_cannot_erase_history(adopting: Any, monkeypatch: Any) -> None:
    from agent_dispatch import ordinary_control_adoption

    assigned, before, info = adopting
    queue, _, _, _, _, _ = assigned
    stored = _record(adopting)
    restarted = TaskQueue(queue.db_path)
    legacy = {key: value for key, value in info.items() if key != ADOPTION}
    # The preceding recorder rewrites ordinary SessionInfo without retaining epoch fields.
    monkeypatch.setattr(ordinary_control_adoption, "retain_control", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        ordinary_control_adoption, "protect_history",
        lambda *_a: pytest.fail("the rollback recorder cannot install the new guard"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="control history is immutable"):
        record_resumed_agent(
            restarted, before["key"], task_generation=stored.agent_info["task_generation"],
            session_info=legacy, worktree_record=assigned[3],
            startup_invocation=before["agent_info"]["startup_invocation"],
        )
    remaining = restarted.get_reservation(before["key"])
    assert remaining is not None and remaining.agent_info == stored.agent_info


@pytest.mark.parametrize("change", ["null", "history", "adoption", "invocation"])
def test_control_adoption_durable_guard_refuses_legacy_json_replacement(adopting: Any, change: str) -> None:
    assigned, before, _ = adopting
    queue, _, _, _, _, _ = assigned
    stored = _record(adopting)
    replacement = copy.deepcopy(stored.agent_info)
    if change == "null":
        replacement = None
    else:
        replacement.pop({"history": HISTORY, "adoption": ADOPTION, "invocation": "startup_invocation"}[change])
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="control history is immutable"):
        conn.execute(
            "UPDATE spawn_reservations SET agent_info=? WHERE key=?",
            (json.dumps(replacement, sort_keys=True) if replacement is not None else None, before["key"]),
        )
    assert queue.get_reservation(before["key"]).agent_info == stored.agent_info


def test_control_adoption_uses_existing_http_storage_contract(adopting: Any) -> None:
    assigned, before, info = adopting
    queue, task, _, record, _, _ = assigned
    with TestClient(create_app_for(queue)) as api:
        result = api.post(f"/spawn-reservations/{before['key']}/resumed-agent", json={
            "task_generation": task["generation"], "session_info": info,
            "worktree_record": record, "startup_invocation": before["agent_info"]["startup_invocation"],
        })
    assert result.status_code == 200
    assert result.json()["agent_info"][HISTORY]["predecessor"] == before["agent_info"]


@pytest.mark.parametrize("versions", [None, [], [True], [2], [1]])
def test_control_adoption_client_negotiates_before_storage(adopting: Any, versions: Any) -> None:
    assigned, before, info = adopting
    queue, task, _, record, _, _ = assigned
    mutations: list[str] = []
    with TestClient(create_app_for(queue)) as api:
        def exchange(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={
                    **pool_placement_contract(), "ordinary_control_adoption_versions": versions,
                })
            mutations.append(str(request.url.path))
            response = api.request(
                request.method, str(request.url), content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient("http://testserver", transport=httpx.MockTransport(exchange)) as client:
            facts = {
                "task_generation": task["generation"], "session_info": info,
                "worktree_record": record, "startup_invocation": before["agent_info"]["startup_invocation"],
            }
            if versions == [1] and type(versions[0]) is int:
                stored = client.record_resumed_agent(before["key"], **facts)
                assert stored["agent_info"][HISTORY]["predecessor"] == before["agent_info"]
                assert mutations == [f"/spawn-reservations/{before['key']}/resumed-agent"]
            else:
                with pytest.raises(DispatchError, match="control adoption version 1"):
                    client.record_resumed_agent(before["key"], **facts)
                assert not mutations
                assert asdict(queue.get_reservation(before["key"])) == before
