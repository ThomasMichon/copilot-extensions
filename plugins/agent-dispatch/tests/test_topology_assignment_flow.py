"""Join the real topology CLI to native standing admission without live workers."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from agent_dispatch import assignment_relationship_owner as module
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import TaskError, TaskQueue
from tests.test_standing_admission import (
    admission as admission,
    cancel,
    materialization as materialization,
)

AT = "2026-01-01T00:01:00Z"
pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


@pytest.fixture
def topology_flow(admission, materialization, tmp_path):
    command, _ = materialization
    skill = Path(command[1]).parents[1]
    document = json.loads((skill / "assets" / "topology.example.json").read_text(encoding="utf-8"))
    owner = {
        key: value for key, value in asdict(admission.workspace).items()
        if key in {"machine", "project", "worktree"}
    }
    document["assignments"][0].update(
        nativeTask=admission.task.id, nativeWorktree=owner, state="proposed", effects=[],
    )
    document["observations"]["owners"][0].update(
        owner=owner, head=admission.head["session_id"], headRevision=admission.head["revision"],
        assignmentId=None, role=None, scope=None, acceptedCharterRevision=None, acceptanceRef=None,
    )
    path = tmp_path / "topology.json"

    def inspect(verb="flow"):
        relationship = admission.queue.task_relationship(admission.task.id)
        document["observations"]["dispatch"] = [{
            "observedAt": AT, "sourceRef": "synthetic:native-dispatch-read",
            "task": asdict(admission.queue.get(admission.task.id)),
            "relationship": {
                "request": relationship["request"], "state": relationship["state"],
                "receipt": {"ref": relationship["receipt"]["ref"]} if relationship["receipt"] else None,
            },
        }]
        path.write_text(json.dumps(document), encoding="utf-8")
        before = path.read_bytes()
        arguments = ["--request", document["assignments"][0]["requestId"]] if verb == "flow" else []
        result = module.run_background_capture(
            [*command, verb, str(path), "--at", AT, *arguments], input_text="", timeout=15,
        )
        assert result is not None and result.returncode == 0, result
        assert result.stderr == ""
        assert path.read_bytes() == before
        return json.loads(result.stdout)

    return document, inspect


def persist_launch(document, step, tmp_path):
    assignment = document["assignments"][0]
    path = tmp_path / "saved-launch-seed.json"
    path.write_text(json.dumps(step["seed"]), encoding="utf-8")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["assignment"]["charter"] == assignment["charter"]
    assert saved["assignment"]["nativeWorktree"] == assignment["nativeWorktree"]
    effect = {
        "id": "synthetic-launch", "kind": "launch", "state": "prepared",
        "charterRevision": step["charterRevision"], "assignmentGeneration": step["assignmentGeneration"],
        "target": copy.deepcopy(step["workspace"]), "nativeRef": None,
        "nativeRequest": {
            "mode": "bridge-existing-worktree-create", "envelopeRef": "synthetic:immutable-launch-envelope",
        },
    }
    assignment["effects"].append(effect)
    return path, effect


def native_accept(admission, step, **changes):
    return admission.queue.accept_standing_charter(step["nativeTask"], **{
        "workspace": AssignmentWorkspace(**step["workspace"]),
        "expected_revision": step["expectedRevision"],
        "expected_generation": step["expectedGeneration"],
        "owner_session_id": step["ownerSessionId"],
        "expected_head_revision": step["expectedHeadRevision"],
        **changes,
    })


def native_reconcile(admission, step):
    task = admission.queue.get(admission.task.id)
    relationship = admission.queue.task_relationship(task.id)
    materialization = step["materialization"]
    admission.path.write_text(json.dumps(materialization), encoding="utf-8")
    admission.declaration = replace(
        admission.declaration,
        source_revision=materialization["sourceRevision"],
        expected_materialization_revision=materialization["materializationRevision"],
    )
    reconciled = admission.queue.reconcile_standing_charter(
        task.title, admission.declaration, repo=task.repo, expected_revision=step["expectedRevision"],
    )
    assert reconciled.id == task.id
    assert reconciled.standing_revision == task.standing_revision + 1
    assert (reconciled.status, reconciled.owner, reconciled.generation) == (
        task.status, task.owner, task.generation,
    )
    assert reconciled.owner_session_id == task.owner_session_id
    assert admission.queue.task_relationship(task.id) == relationship


@pytest.mark.parametrize("advance_revision", [False, True])
def test_saved_full_seed_reaches_exact_native_acceptance(admission, topology_flow, tmp_path, advance_revision):
    document, inspect = topology_flow
    assert admission.queue.get(admission.task.id).status == "claimed"
    result = inspect()
    assert result["phase"] == "persist-launch" and result["holds"] == []
    path, effect = persist_launch(document, result["nextStep"], tmp_path)
    saved = path.read_bytes()
    if advance_revision:
        document["revision"] += 1
        result = inspect()
        assert result["phase"] == "reconcile" and result["holds"] == []
        assert result["nextStep"]["sourceOnly"] and not result["nextStep"]["replayAllowed"]
        assert result["nextStep"]["retainedEffects"] == [effect]
        native_reconcile(admission, result["nextStep"])
    result = inspect()
    assert result["phase"] == "launch"
    assert result["nextStep"]["effect"] == effect
    assert result["nextStep"]["workspace"] == document["assignments"][0]["nativeWorktree"]
    assert inspect("overview")["coverage"][0]["qualifiedCount"] == 0

    effect.update(state="consumed", nativeRef="synthetic:launch-consumption")
    if advance_revision:
        document["revision"] += 1
        result = inspect()
        assert result["phase"] == "reconcile" and result["holds"] == []
        assert result["nextStep"]["sourceOnly"] and not result["nextStep"]["replayAllowed"]
        assert result["nextStep"]["retainedEffects"] == [effect]
        native_reconcile(admission, result["nextStep"])
    result = inspect()
    assert result["phase"] == "accept"
    accepted = native_accept(admission, result["nextStep"])
    assert accepted.status == "started" and accepted.owner_session_id == admission.head["session_id"]
    assert accepted.standing_charter["accepted_materialization"]["assignment_generation"] == (
        effect["assignmentGeneration"]
    )
    result = inspect()
    assert result["phase"] == "settle-launch"
    assert result["nextStep"]["acceptance"] == accepted.standing_charter["accepted_materialization"]
    effect["state"] = "accepted"
    result = inspect()
    assert result["phase"] == "accepted" and result["nextStep"] is None
    assert inspect("overview")["coverage"][0]["qualifiedCount"] == 1
    assert path.read_bytes() == saved
    assert any("inspect-materialization" in call for call in admission.calls)
    assert any("--resident-only" in call for call in admission.calls)


def test_replaced_reservation_refuses_saved_launch_generation(admission, topology_flow, tmp_path):
    document, inspect = topology_flow
    path, effect = persist_launch(document, inspect()["nextStep"], tmp_path)
    saved = path.read_bytes()
    effect.update(state="consumed", nativeRef="synthetic:old-launch-consumption")
    old_step = inspect()["nextStep"]
    cancelled = cancel(admission)
    queue = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.owner, relationship_authority="local",
    )
    reserved = queue.reserve_standing_charter(
        admission.task.id, admission.workspace, expected_revision=cancelled.standing_revision,
    )
    assert reserved.generation > old_step["expectedGeneration"]
    result = inspect()
    assert result["phase"] == "held" and result["nextStep"] is None
    assert "launch-assignment-generation-conflict" in result["holds"]
    events = queue.events(reserved.id)
    relationship = queue.task_relationship(reserved.id)
    calls = len(admission.calls)
    with pytest.raises(TaskError, match="ownership incarnation changed"):
        native_accept(admission, old_step, expected_revision=reserved.standing_revision)
    assert queue.get(reserved.id) == reserved
    assert queue.events(reserved.id) == events
    assert queue.task_relationship(reserved.id) == relationship
    assert len(admission.calls) == calls
    assert inspect("overview")["coverage"][0]["qualifiedCount"] == 0
    assert path.read_bytes() == saved
