"""Original-journal writer observations are read-only and selection-bound."""

from __future__ import annotations

import argparse
import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from zdd import routing

from agent_bridge import acquisition_authority, acquisition_cli, acquisition_writer_inspection as module
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest
from agent_bridge.acquisition_runtime import AcquisitionRuntime
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.routes.acquisitions import router

pytestmark = pytest.mark.contract("agent_bridge.acquisition.runtime")


@pytest.fixture
def rig(tmp_db, tmp_path, monkeypatch):
    request = AcquisitionRequest.model_validate({
        "version": 3, "target": "worker", "mode": "acp", "task_id": "task-a",
        "task_generation": 1, "worktree_id": "worktree-a", "allocation": "reused",
        "reservation_key": "reservation-a", "delivery_id": "delivery-a",
        "prompt": "Continue the assigned task", "project": "test-project",
        "worktree_path": str(tmp_path / "workspace"),
        "worktree_writer": {
            "identity": {
                "installation_root": str(tmp_path / "owner"), "runtime_generation": "a" * 32,
            },
            "creation_nonce": "original-incarnation",
        },
    })
    state = SimpleNamespace(ready=True)
    manager = SimpleNamespace(is_draining=False)
    runtime = AcquisitionRuntime(manager, None, tmp_db, state)
    runtime.store.journal.claim(
        request, worktree_writer={"version": 1, "prefix": ["original-owner", "original-build"]},
    )
    root = tmp_path / "bridge"
    monkeypatch.setattr(module, "config_dir", lambda: root)
    routing.publish_active(root, bind="127.0.0.1", port=12345, pid=os.getpid(), version="fixture")
    record = {
        "id": request.worktree_id, "repo": request.project, "path": request.worktree_path,
        "status": "active", "creation_nonce": request.worktree_writer.creation_nonce,
        "session_count": 1, "last_session_id": "native-a", "execution_leg": None,
        "execution_leg_opaque": False, "session_backend": None,
        "session_backend_opaque": False, "sessions_indexed": True,
    }
    support = {
        "schema": "copilot-extensions.resource-relationship", "version": 1,
        "expected_creation_nonce": True, "expected_writer_identity": True,
        "writer_scope": "executing-process", "retained_session_writes": 1,
        "writer_identity": request.worktree_writer.identity.model_dump(),
        "writer_contract": {
            "schema": "copilot-extensions.worktree-record-writer", "version": 1,
            "retained_relationships": 1, "creation_nonce": 1,
        },
    }
    calls = []

    async def command(args, project, *, owner_prefix=None):
        assert project == request.project
        assert owner_prefix == ("original-owner", "original-build")
        calls.append(args)
        if args == ["resource-relationship", "capabilities", "--json"]:
            return copy.deepcopy(support)
        assert args == ["record", "--worktree-id", request.worktree_id, "--json"]
        return {"ok": True, "version": 1, "worktree": dict(record)}

    monkeypatch.setattr(acquisition_authority, "worktree_command", command)
    from agent_bridge import session_lifecycle_cli
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_pinned_selection",
        Mock(side_effect=AssertionError("inspection must not select a replacement")),
    )
    return SimpleNamespace(
        runtime=runtime, request=request, db=tmp_db, manager=manager, state=state,
        record=record, support=support, calls=calls, root=root, command=command,
    )


def snapshot(db):
    return {
        name: [dict(row) for row in db.execute_read(f"SELECT * FROM {name}")]
        for name in (
            "acquisition_attempts", "acquisition_effects", "acquisition_bindings",
            "acquisition_launch_gates", "sessions", "live_messages",
        )
    }


@pytest.mark.asyncio
async def test_live_observation_recovers_original_writer_without_writes_or_reselection(rig, monkeypatch):
    before = snapshot(rig.db)
    monkeypatch.setattr(rig.db, "execute_write", Mock(side_effect=AssertionError("inspection wrote")))
    legacy = await rig.runtime.inspect_pending(rig.request.delivery_id)
    assert not rig.calls and "worktree_writer_observation" not in legacy
    for _ in range(2):
        result = await rig.runtime.inspect_pending(rig.request.delivery_id, worktree_writer=True)
        observed = result["worktree_writer_observation"]
        assert observed["schema"] == module.SCHEMA and observed["version"] == 1
        assert observed["worktree_writer"] == rig.request.worktree_writer.model_dump()
        assert observed["worktree_path"] == rig.request.worktree_path
        assert observed["task_id"] == rig.request.task_id
        assert observed["reservation_key"] == rig.request.reservation_key
        assert observed["bridge_instance"]["pid"] == os.getpid()
        assert observed["bridge_instance"]["generation"] > 0
        assert observed["writer_contract"] == rig.support["writer_contract"]
        assert "prefix" not in json.dumps(observed)
        assert result["recorded_result"] is None and result["delivery_admitted"] is False
    assert len(rig.calls) == 4 and snapshot(rig.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["creation_nonce", "repo", "path", "id"])
async def test_changed_original_workspace_never_yields_writer_evidence(rig, changed):
    before = snapshot(rig.db)
    rig.record[changed] = "different"
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.inspect_pending(rig.request.delivery_id, worktree_writer=True)
    assert snapshot(rig.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["identity", "contract", "scope", "session-writes", "unavailable"])
async def test_old_unknown_or_changed_executing_serializer_refuses_before_record(rig, monkeypatch, changed):
    before = snapshot(rig.db)
    if changed == "identity":
        rig.support["writer_identity"]["runtime_generation"] = "b" * 32
    elif changed == "contract":
        rig.support.pop("writer_contract")
    elif changed == "scope":
        rig.support["writer_scope"] = "installed-payload"
    elif changed == "session-writes":
        rig.support.pop("retained_session_writes")
    else:
        async def unavailable(*args, **kwargs):
            raise AcquisitionRefused("owner_operation_pending", "original writer did not answer")
        monkeypatch.setattr(acquisition_authority, "worktree_command", unavailable)
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.inspect_pending(rig.request.delivery_id, worktree_writer=True)
    assert not any(args[0] == "record" for args in rig.calls)
    assert snapshot(rig.db) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["instance", "journal", "draining", "not-ready"])
async def test_changes_during_observation_invalidate_the_result(rig, monkeypatch, changed):
    original = rig.command

    async def command(args, *a, **kwargs):
        result = await original(args, *a, **kwargs)
        if args[0] == "record":
            if changed == "instance":
                routing.publish_active(
                    rig.root, bind="127.0.0.1", port=12346, pid=os.getpid(), version="new",
                )
            elif changed == "journal":
                rig.db.execute_write(
                    "UPDATE acquisition_effects SET stage='launching' WHERE delivery_id=?",
                    (rig.request.delivery_id,),
                )
            elif changed == "draining":
                rig.manager.is_draining = True
            else:
                rig.state.ready = False
        return result

    monkeypatch.setattr(acquisition_authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.inspect_pending(rig.request.delivery_id, worktree_writer=True)
    assert len(rig.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["missing", "legacy", "refused", "passive"])
async def test_unqualified_original_owner_is_not_repaired_or_adopted(rig, changed):
    delivery = rig.request.delivery_id
    if changed == "missing":
        delivery = "unknown-delivery"
    elif changed == "legacy":
        legacy = rig.request.model_copy(update={
            "delivery_id": "legacy", "worktree_id": "legacy-worktree",
            "version": 2, "worktree_writer": None,
        })
        rig.runtime.store.journal.claim(legacy)
        delivery = legacy.delivery_id
    elif changed == "refused":
        rig.db.execute_write(
            "UPDATE acquisition_attempts SET state='refused' WHERE delivery_id=?", (delivery,),
        )
    else:
        routing.publish_active(
            rig.root, bind="127.0.0.1", port=12346, pid=os.getpid() + 1, version="new",
        )
    before = snapshot(rig.db)
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.inspect_pending(delivery, worktree_writer=True)
    assert not rig.calls and snapshot(rig.db) == before


@pytest.mark.parametrize("supported", ["current", "prior", "rollback"])
def test_cli_negotiates_and_round_trips_optional_inspection_through_http(
    rig, monkeypatch, capsys, supported,
):
    app = FastAPI()
    app.include_router(router)
    app.state.acquisition_runtime = rig.runtime

    @app.get("/health")
    def health():
        return {
            "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
            "capabilities": {module.CAPABILITY: 1} if supported != "prior" else {},
        }

    with TestClient(app) as http:
        client = BridgeClient("http://unused", "unused", connect_grace=0)

        def send(method, path, *args, **kwargs):
            if supported == "rollback":
                path = path.partition("?")[0]
            response = http.request(method, path)
            if response.status_code != 200:
                raise BridgeClientError(response.status_code, response.json()["detail"])
            return response.json()

        client._request = send
        from agent_bridge import __main__ as core
        monkeypatch.setattr(core, "_get_client", lambda: client)
        parser = argparse.ArgumentParser()
        acquisition_cli.register(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["acquisition-inspect", rig.request.delivery_id, "--worktree-writer"])
        if supported == "prior":
            with pytest.raises(BridgeClientError, match=module.CAPABILITY):
                args.func(args)
            assert not rig.calls
            return
        if supported == "rollback":
            with pytest.raises(BridgeClientError, match="observation is unconfirmed"):
                args.func(args)
            assert not rig.calls and not capsys.readouterr().out
            return
        before = snapshot(rig.db)
        args.func(args)
        observed = json.loads(capsys.readouterr().out)["worktree_writer_observation"]
        assert observed["delivery_id"] == rig.request.delivery_id
        assert observed["worktree_writer"] == rig.request.worktree_writer.model_dump()
        assert snapshot(rig.db) == before
        rig.record["creation_nonce"] = "recreated"
        refusal = http.get(f"/api/v1/acquisitions/{rig.request.delivery_id}?worktree_writer=true")
        assert refusal.status_code == 409
        assert "worktree_writer_observation" not in refusal.json()
