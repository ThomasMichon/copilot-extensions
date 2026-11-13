"""Original ACP assignment metadata survives replay and mutable targeting changes."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.acquisition_journal import _digest
from agent_bridge.dispatch_origin import DispatchSessionOrigin, inspect
from agent_bridge.models import SessionStatus, StartSessionRequest
from agent_bridge.routes.sessions import _session_info, router
from agent_bridge.session_manager import SessionManager
from agent_bridge import workspace_creation
from test_acquisition_runtime import req, rig as _rig
from test_workspace_creation import creation_rig as _creation_rig

rig = _rig
creation_rig = _creation_rig
pytestmark = pytest.mark.contract("agent_bridge.dispatch_session_origin")


def origin():
    return {
        "task_id": "task-a", "reservation_key": "reservation-a",
        "cohort": {
            "schema": "dispatch-assignment-cohort", "version": 1,
            "emitter": {
                "repo": "example/project", "source": "review-source", "request_id": "source-request",
                "producer_id": "producer-a", "task_id": "task-a", "generation": 2,
            },
            "pool": {
                "task_id": "task-a", "registration_id": "pool-registration", "machine": "worker",
                "env": "linux", "reservation_key": "reservation-a", "task_generation": 0,
                "catalog_revision": 5,
            },
        },
    }


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra=True),
    lambda value: value["cohort"].update(version=True),
    lambda value: value["cohort"]["pool"].update(task_id="task-b"),
    lambda value: value["cohort"]["emitter"].update(task_id="task-b"),
    lambda value: value["cohort"]["pool"].update(reservation_key="reservation-b"),
    lambda value: value["cohort"]["pool"].update(catalog_revision="5"),
    lambda value: value["cohort"]["emitter"].update(source=" "),
    lambda value: value["cohort"]["emitter"].update(producer_id="a\0b"),
    lambda value: value.update(task_id=" task-a"),
])
def test_origin_rejects_malformed_or_cross_assignment_evidence(change):
    value = origin()
    change(value)
    with pytest.raises(ValidationError):
        DispatchSessionOrigin.model_validate(value)


def test_origin_detaches_nested_input_and_legacy_request_digest_is_unchanged():
    value = origin()
    request = req(dispatch_origin=value)
    value["cohort"]["pool"]["registration_id"] = "changed"
    assert request.model_dump()["dispatch_origin"] == origin()
    with pytest.raises(ValidationError):
        request.dispatch_origin.cohort.pool.registration_id = "changed"
    legacy = req()
    facts = legacy.model_dump(mode="json")
    assert "dispatch_origin" not in facts
    facts.pop("worktree_path")
    assert _digest(legacy) == hashlib.sha256(
        json.dumps(facts, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()


@pytest.mark.parametrize("changes", [
    {"mode": "cli"}, {"task_id": "task-b"}, {"reservation_key": "reservation-b"},
])
def test_acquisition_refuses_unqualified_modes_and_other_assignments(changes):
    with pytest.raises(ValidationError):
        req(dispatch_origin=origin(), **changes)


@pytest.mark.asyncio
async def test_acquisition_first_save_replay_restart_and_reassignment_keep_birth(rig, monkeypatch):
    connect = rig.manager._connect_via_session_host
    before_native = []

    async def observe_first_save(*args, **kwargs):
        rows = rig.db.list_sessions()
        assert len(rows) == 1 and rows[0]["status"] == "starting"
        assert inspect(rows[0]) == origin()
        before_native.append(rows[0]["id"])
        return await connect(*args, **kwargs)

    monkeypatch.setattr(rig.manager, "_connect_via_session_host", observe_first_save)
    request = req(dispatch_origin=origin())
    result = await rig.runtime.acquire(request)
    assert before_native == [result.session_id]
    row = rig.db.get_session(result.session_id)
    assert inspect(row) == origin()
    assert _session_info(rig.manager.get_session(result.session_id), rig.manager).dispatch_origin == origin()
    prepared = rig.db.execute_read(
        "SELECT data_json FROM acquisition_effects WHERE delivery_id=?", (request.delivery_id,),
    )[0]
    assert json.loads(prepared["data_json"])["_request"]["dispatch_origin"] == origin()
    assert await rig.runtime.acquire(request) == result
    changed = deepcopy(origin())
    changed["cohort"]["pool"]["registration_id"] = "another-pool"
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.acquire(req(dispatch_origin=changed))
    session = rig.manager.get_session(result.session_id)
    rig.db.update_session_target(
        session.session_id,
        replace(session.target, venue={"dispatch_origin": changed}).to_json(),
        session.target.cwd,
    )
    cold = SessionManager(rig.db)
    assert _session_info(cold.get_session(result.session_id), cold).dispatch_origin == origin()
    assert len(rig.db.list_sessions()) == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_legacy_acquisition_reuse_never_backfills_origin(rig):
    result = await rig.runtime.acquire(req())
    session = rig.manager.get_session(result.session_id)
    if session._prompt_task:
        await session._prompt_task
    session.status = SessionStatus.IDLE
    rig.db.update_session_status(session.session_id, "idle", time.time())
    rig.source["acquisition_reservation"].update(
        key="reservation-b", attempt=2, worktree_ownership="reused",
    )
    metadata = origin()
    metadata["reservation_key"] = metadata["cohort"]["pool"]["reservation_key"] = "reservation-b"
    reused = await rig.runtime.acquire(req(
        allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
        dispatch_origin=metadata,
    ))
    assert reused.session_id == result.session_id
    assert inspect(rig.db.get_session(result.session_id)) is None
    assert _session_info(rig.manager.get_session(result.session_id), rig.manager).dispatch_origin is None
    await asyncio.sleep(0)


def test_keyed_http_creation_retains_origin_and_rejects_changed_or_dropped_stamp(creation_rig):
    rig = creation_rig
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = rig.manager
    activations = []

    async def resolve(_name, **kwargs):
        activations.append(kwargs["activate"])
        return replace(rig.target)

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    body = {
        "agent": "task-worker", "worktree_id": "worktree-a",
        "creation_key": "logical-work", "dispatch_origin": origin(),
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/sessions", json=body)
        assert first.status_code == 201, first.text
        session_id = first.json()["session_id"]
        replay = client.post("/api/v1/sessions", json=body)
        assert replay.status_code == 201, replay.text
        assert replay.json()["session_id"] == session_id
        assert inspect(rig.db.get_session(session_id)) == origin()
        assert client.get(f"/api/v1/sessions/{session_id}").json()["dispatch_origin"] == origin()
        changed = deepcopy(body)
        changed["dispatch_origin"]["cohort"]["pool"]["registration_id"] = "changed"
        assert client.post("/api/v1/sessions", json=changed).status_code == 409
        assert client.post("/api/v1/sessions", json={
            key: value for key, value in body.items() if key != "dispatch_origin"
        }).status_code == 409
    assert len(rig.db.list_sessions()) == 1
    assert not any(args[0] == "create" for args, _ in rig.calls)
    assert activations[:3] == [False, False, False]


@pytest.mark.parametrize("changes", [
    {"creation_key": None}, {"worktree_id": None}, {"provider_instance": True},
    {"target_dir": "/other"}, {"remote_session_id": "remote"},
])
def test_public_origin_requires_keyed_prepared_workspace(changes):
    with pytest.raises(ValidationError):
        StartSessionRequest.model_validate({
            "agent": "task-worker", "creation_key": "logical-work", "worktree_id": "worktree-a",
            "dispatch_origin": origin(), **changes,
        })


@pytest.mark.asyncio
async def test_unsupported_target_refuses_before_worktree_effects(creation_rig):
    from fastapi import HTTPException

    rig = creation_rig
    request = StartSessionRequest(
        agent="task-worker", creation_key="logical-work", worktree_id="worktree-a",
        dispatch_origin=origin(),
    )
    with pytest.raises(HTTPException) as refused:
        await workspace_creation.start_request(
            request, rig.manager, replace(rig.target, worktree_id="worktree-a", elevated=True),
            agent_name="task-worker", env={}, source="caller-settings",
        )
    assert refused.value.status_code == 422
    assert not rig.calls
    assert not rig.db.list_sessions()
    assert not rig.db.execute_read("SELECT 1 FROM workspace_embodiments")
