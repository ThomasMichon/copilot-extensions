"""Ordinary startup restores control without replaying a predecessor's send."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import asdict
from typing import Any

import pytest

from agent_dispatch import ordinary_bridge
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.ordinary_control_adoption import ADOPTION, HISTORY
from agent_dispatch.ordinary_control_invocation import CONTROL
from agent_dispatch.queue_records import TaskError
from tests.test_ordinary_assignment import _qualified_start, assigned as assigned
from tests.test_ordinary_control_adoption import adopting as adopting
from tests.test_ordinary_pool_startup import (
    ordinary as ordinary, q as q, api as api, admission_owner as admission_owner,
    launch,
)

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


@pytest.fixture
def restoring(adopting: Any, monkeypatch: Any) -> Any:
    assigned, before, info = adopting
    return _restoring(assigned, before, info, monkeypatch)


def _restoring(assigned: Any, before: Any, info: Any, monkeypatch: Any) -> Any:
    queue, task, client, record, _, calls = assigned
    original = ordinary_bridge.subprocess.run
    original_status = queue.get(task["id"]).status
    effects: list[str] = []
    receipts: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {"absent": True, "loss": False, "unknown": False, "commands": []}

    def run(argv: Any, **kwargs: Any) -> Any:
        if argv[0] != "bridge":
            return original(argv, **kwargs)
        state["commands"].append(list(argv))
        command = argv[2]
        if command == "version":
            result = original(argv, **kwargs)
            value = json.loads(result.stdout)
            value["contracts"]["agent-bridge.resume"]["capabilities"].append("exact-worktree-target")
            value["contracts"]["agent-bridge.status"]["capabilities"].append("exact-session-absence")
        elif command == "status" and len(argv) == 3:
            result = original(argv, **kwargs)
            value = json.loads(result.stdout)
            value["capabilities"].update(
                retained_worktree_resume=1, retained_native_metadata_adoption=1,
                retained_native_session_recovery=1,
                retained_keyed_native_admission=1,
            )
        elif command == "status" and argv[3] == "bridge-a":
            if not state["absent"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps(assigned[4]), "")
            return subprocess.CompletedProcess(argv, 1, json.dumps({
                "outcome": "refused_unavailable", "reason": "native_session_unavailable",
                "target": "bridge-a",
            }), "")
        elif command == "status":
            assert argv[3] == "adopted-bridge"
            value = copy.deepcopy(info)
        elif command == "resume":
            assert argv == [
                "bridge", "--json", "resume", "--worktree-id", "worktree-a",
                "--strict", "--require-agent-hosting",
            ]
            assert kwargs.get("input") is None
            assert queue.get(task["id"]).status == original_status
            effects.append("resume")
            record.update(session_live=True, native_liveness="active")
            if state.pop("resume_loss", False):
                raise subprocess.TimeoutExpired(argv, 1)
            value = copy.deepcopy(info)
        elif command == "send":
            invocation = json.loads(kwargs["input"])
            if argv[3] == "bridge-a":
                assert "--receipt-only" in argv
                assert invocation["invocation_key"] == before["agent_info"]["startup_invocation"]["invocation_key"]
                return subprocess.CompletedProcess(argv, 69, json.dumps({
                    "outcome": "refused_unavailable", "reason": "native_session_unavailable",
                }), "")
            assert argv[3] == "adopted-bridge"
            retained = queue.get_reservation(before["key"]).agent_info
            assert retained[HISTORY]["predecessor"] == before["agent_info"]
            assert retained[CONTROL]["invocation"]["invocation_key"] == invocation["invocation_key"]
            assert retained["startup_invocation"] == before["agent_info"]["startup_invocation"]
            assert "--no-resume" in argv
            if "--receipt-only" in argv:
                if state["unknown"]:
                    return subprocess.CompletedProcess(argv, 69, json.dumps({
                        "outcome": "refused_unavailable", "reason": "native_admission_history_unproven",
                    }), "")
                if invocation["invocation_key"] not in receipts:
                    return subprocess.CompletedProcess(argv, 69, json.dumps({
                        "outcome": "refused_unavailable", "reason": "native_receipt_missing",
                    }), "")
            else:
                effects.append("send")
                receipts[invocation["invocation_key"]] = {
                    "version": 1, "session_id": "adopted-bridge", "native_session_id": "native-a",
                    "invocation_key": invocation["invocation_key"],
                    "outcome": "delivered", "admission": "accepted", "queued": False,
                    "turn_index": invocation["binding"]["expected_turn_index"],
                }
                if state["loss"]:
                    raise subprocess.TimeoutExpired(argv, 1)
            value = receipts[invocation["invocation_key"]]
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    calls.clear()
    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    return assigned, before, info, effects, state, run


@pytest.mark.parametrize("ordinary", [True], indirect=True)
def test_placed_resume_records_startup_but_worker_explicitly_accepts_over_http(
    ordinary: Any, monkeypatch: Any,
) -> None:
    o = ordinary
    original = launch(o)
    key = o.placed["reservation"]["key"]
    ordinary_bridge.record_session_info(o.q, key, original["session_info"])
    before = asdict(o.q.get_reservation(key))
    requirement = before["worktree_writer_requirement"]
    digest = hashlib.sha256(json.dumps([o.ground["repo"], before["creation_key"]]).encode()).hexdigest()
    info = {
        "session_id": "adopted-bridge", "acp_session_id": "native-a", "durable_session_id": "native-a",
        "worktree_id": o.ground["id"], "project": o.ground["repo"], "target_dir": o.ground["path"],
        "status": "idle", "at_rest": True, "read_only": False, "liveness": "active", "turn_count": 0,
        "worktree_writer_requirement": requirement,
        ADOPTION: {
            "version": 1, "predecessor_id": "bridge-a", "session_id": "adopted-bridge",
            "request": {
                "version": 1, "operation_id": "ordinary-resume:original", "journal_key": digest,
                "native_session_id": "native-a", "head_revision": 5, "bridge_installation": "/bridge",
            },
            "history": {
                "status": "unavailable", "predecessor_id": "bridge-a",
                "event_epoch": "adopted-bridge", "turn_epoch": "adopted-bridge",
            },
            "prior_operations": ["settled-not-sent"],
        },
        "worktree_writer_observation": {
            "schema": "copilot-extensions.creation-worktree-writer", "version": 1,
            "key_digest": digest, "session_id": "adopted-bridge", "native_session_id": "native-a",
            "bridge_instance": {"installation_root": "/bridge", "pid": 42, "generation": 7},
            "project": o.ground["repo"], "worktree_id": o.ground["id"], "worktree_path": o.ground["path"],
            "worktree_writer": requirement, "executing_writer_identity": o.writer,
            "writer_scope": "executing-process",
            "writer_contract": {
                "schema": "copilot-extensions.worktree-record-writer", "version": 1,
                "retained_relationships": 1, "creation_nonce": 1,
            },
            "retained_session_writes": 1, "automatic_recovery_stopped": False,
        },
    }
    bound: Any = (o.q, o.task, o.client, o.ground, {}, [])
    _, _, _, effects, _, _ = _restoring(bound, before, info, monkeypatch)
    result = launch(o)
    assert effects == ["resume", "send"]
    assert o.q.get(o.task["id"]).status == "claimed"
    o.client.record_spawn(
        key, session_handle=result["session"], worktree=result["worktree"], startup_complete=True,
        startup_revision=before["startup_revision"], session_info=result["session_info"],
    )
    assert o.q.get(o.task["id"]).status == "claimed"
    assert o.q.get_reservation(key).startup_complete is True
    with pytest.raises(DispatchError):
        o.client.start_pool_task(
            o.task["id"], "host-a/worktree-a", owner_session_id="other-native",
            expected_generation=o.task["generation"],
        )
    started = o.client.start_pool_task(
        o.task["id"], "host-a/worktree-a", owner_session_id="native-a",
        expected_generation=o.task["generation"],
    )
    assert started["status"] == "started" and started["owner_session_id"] == "native-a"


def test_ordinary_start_restores_and_records_before_new_send_but_worker_alone_starts(restoring: Any) -> None:
    assigned, before, _, effects, state, _ = restoring
    queue, task, _, _, _, _ = assigned
    original_task = asdict(queue.get(task["id"]))
    result = _qualified_start(assigned)
    assert effects == ["resume", "send"]
    assert len(state["commands"]) == 9
    stored = queue.get_reservation(before["key"])
    assert stored.session_handle == "local-body:adopted-bridge"
    assert stored.startup_complete is False and stored.state == "reserving"
    assert stored.agent_info[CONTROL]["invocation"]["invocation_key"] != (
        stored.agent_info["startup_invocation"]["invocation_key"]
    )
    assert asdict(queue.get(task["id"])) == original_task
    ordinary_bridge.record_session_info(queue, before["key"], result["session_info"])
    with pytest.raises(TaskError):
        ordinary_bridge.record_session_info(queue, before["key"], before["agent_info"]["last_send"])
    claimed = queue.claim_one("worker", task_id=task["id"])
    assert claimed is not None
    with pytest.raises(TaskError):
        queue.start(task["id"], "worker", owner_session_id="other-native")
    assert queue.start(task["id"], "worker", owner_session_id="native-a").status == "started"


def test_lost_current_control_reply_recovers_receipt_without_resume_or_second_send(restoring: Any) -> None:
    assigned, before, _, effects, state, _ = restoring
    state["loss"] = True
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded, match="recovery retained"):
        _qualified_start(assigned)
    saved = copy.deepcopy(assigned[0].get_reservation(before["key"]).agent_info)
    state["commands"].clear()
    result = _qualified_start(assigned)
    assert result["session"] == "local-body:adopted-bridge"
    assert effects == ["resume", "send"]
    assert len(state["commands"]) == 1 and "--receipt-only" in state["commands"][0]
    assert assigned[0].get_reservation(before["key"]).agent_info == saved


@pytest.mark.parametrize("lost", ["resume-reply", "uncommitted-storage"])
def test_retry_before_control_storage_reconciles_only_the_original_live_native(
    restoring: Any, monkeypatch: Any, lost: str,
) -> None:
    assigned, before, _, effects, state, _ = restoring
    queue, _, client, _, _, _ = assigned
    if lost == "resume-reply":
        state["resume_loss"] = True
    else:
        record = client.record_resumed_agent

        def unavailable(*args: Any, **kwargs: Any) -> Any:
            monkeypatch.setattr(client, "record_resumed_agent", record)
            raise DispatchError(503, "storage unavailable before commit")

        monkeypatch.setattr(client, "record_resumed_agent", unavailable)
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        _qualified_start(assigned)
    assert queue.get_reservation(before["key"]).agent_info == before["agent_info"]
    result = _qualified_start(assigned)
    assert result["native_session_id"] == before["agent_info"]["connection"]["native_session_id"]
    assert effects == ["resume", "resume", "send"]


def test_lost_resume_reply_never_admits_a_foreign_live_head(restoring: Any) -> None:
    assigned, before, _, effects, state, _ = restoring
    queue, _, _, record, _, _ = assigned
    state["resume_loss"] = True
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        _qualified_start(assigned)
    record["last_session_id"] = "foreign-native"
    with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
        _qualified_start(assigned)
    assert effects == ["resume"]
    assert queue.get_reservation(before["key"]).agent_info == before["agent_info"]


def test_unknown_current_control_receipt_never_sends_or_rekeys(restoring: Any) -> None:
    assigned, before, _, effects, state, _ = restoring
    state["unknown"] = True
    with pytest.raises(ordinary_bridge.BridgeCarriedSessionBusy, match="receipt is unknown"):
        _qualified_start(assigned)
    retained = copy.deepcopy(assigned[0].get_reservation(before["key"]).agent_info)
    with pytest.raises(ordinary_bridge.BridgeCarriedSessionBusy, match="receipt is unknown"):
        _qualified_start(assigned)
    assert effects == ["resume"]
    assert assigned[0].get_reservation(before["key"]).agent_info == retained


def test_current_control_rollback_cannot_erase_the_new_operation(restoring: Any) -> None:
    assigned, before, _, _, _, _ = restoring
    queue = assigned[0]
    _qualified_start(assigned)
    retained = copy.deepcopy(queue.get_reservation(before["key"]).agent_info)
    replacement = {key: value for key, value in retained.items() if key != CONTROL}
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="control invocation is immutable"):
        conn.execute(
            "UPDATE spawn_reservations SET agent_info=? WHERE key=?",
            (json.dumps(replacement, sort_keys=True), before["key"]),
        )
    assert queue.get_reservation(before["key"]).agent_info == retained


@pytest.mark.parametrize("changed", ["reservation", "generation", "startup-revision"])
def test_carried_control_receipt_cannot_complete_a_different_admission(restoring: Any, changed: str) -> None:
    from agent_dispatch.ordinary_resume_control import deliver

    assigned, before, _, _, _, _ = restoring
    queue, task, client, _, _, _ = assigned
    _qualified_start(assigned)
    retained = asdict(queue.get_reservation(before["key"]))
    task = dict(task)
    if changed == "reservation":
        retained["key"] = "different-reservation"
    elif changed == "generation":
        task["generation"] += 1
    else:
        retained["startup_revision"] += 1

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a prior admission's receipt must not be queried")

    with pytest.raises(TaskError):
        deliver(forbidden, client, task, retained, retained["agent_info"], observe_only=True)


@pytest.mark.parametrize("unsupported", ["cli", "daemon", "storage", "present", "native"])
def test_recovery_refuses_unsupported_or_changed_owner_before_resume(restoring: Any, monkeypatch: Any, unsupported: str) -> None:
    assigned, before, _, effects, state, run = restoring
    queue, task, client, record, _, _ = assigned
    if unsupported == "storage":
        contract = client.pool_placement_contract()
        contract["ordinary_control_delivery_versions"] = []
        monkeypatch.setattr(client, "pool_placement_contract", lambda: contract)
    elif unsupported == "native":
        record["last_session_id"] = "foreign"
    elif unsupported == "present":
        state["absent"] = False
    else:
        def altered(argv: Any, **kwargs: Any) -> Any:
            result = run(argv, **kwargs)
            value = json.loads(result.stdout)
            if unsupported == "cli" and argv[2] == "version":
                value["contracts"]["agent-bridge.resume"]["capabilities"] = []
            if unsupported == "daemon" and argv[2] == "status" and len(argv) == 3:
                value["capabilities"].pop("retained_worktree_resume")
            return subprocess.CompletedProcess(argv, result.returncode, json.dumps(value), "")
        monkeypatch.setattr(ordinary_bridge.subprocess, "run", altered)
    with pytest.raises(ordinary_bridge.BridgeCarriedSessionBusy):
        _qualified_start(assigned)
    assert not effects
    assert queue.get_reservation(before["key"]).agent_info == before["agent_info"]
    assert queue.get(task["id"]).status == "queued"
