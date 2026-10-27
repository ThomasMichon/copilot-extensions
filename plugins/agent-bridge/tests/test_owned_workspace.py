"""Owned SSH allocation uses the origin command, then only an existing child."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge import owned_workspace as owned, transport
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.spawn_target import SpawnTarget
from test_session_manager import _patch_acp as _patch_acp, _patch_spawn as _patch_spawn


def target() -> SpawnTarget:
    return SpawnTarget(
        type="ssh", host="receiver-alias", execution_space_key="receiver",
        project="destination", caller_worktree="parent",
        caller_owner_ref="origin/control/parent#session",
    )


def capabilities() -> dict:
    return {
        "schema": "copilot-extensions.worktree-allocation", "version": 1, "role": "origin",
        "creator": "origin/control/parent",
        "ordinary_create": {
            "version": 1, "destination_selectors": ["machine", "repo"],
            "creation_key": "opaque-v1", "result": "ordinary-worktree+allocation-v1",
            "kinds": ["session", "bridge"], "caller_worktree": True, "creator_identity": True,
            "claim_linkage": "non-blocking", "agent_launch": False,
        },
    }


def result(key: str) -> dict:
    return {
        "creation": {"version": 1, "status": "complete"},
        "worktree": {
            "id": "actual-child", "repo": "destination", "path": "/worktrees/actual-child",
            "creation_nonce": "original-nonce", "owner_ref": "origin/control/parent",
            "execution_machine": "receiver",
            "kind": "bridge", "caller_worktree": "parent",
        },
        "allocation": {
            "version": 1, "creator": "origin/control/parent", "creation_key": key,
            "request_id": "original-request", "linkage": "pending", "receiving_status": "allocated",
            "target": {
                "machine": "receiver", "project": "destination",
                "options": {
                    "kind": "bridge", "interface": "acp", "origin": "delegate",
                    "caller_worktree": "parent", "no_pair": False,
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_origin_local_command_then_existing_child_without_link_ack(monkeypatch):
    session = SimpleNamespace(session_id="bridge-session", target=owned.arm(target(), "bridge-session"))
    key = owned._intent(session.target)["creation_key"]
    command = AsyncMock(side_effect=[capabilities(), result(key)])
    monkeypatch.setattr(owned, "worktree_command", command)
    db = Mock()
    await owned.prepare_session(session, db)
    calls = command.await_args_list
    assert calls[0].args == ([
        "worktree-allocation", "capabilities", "--owner-ref", "origin/control/parent#session", "--json",
    ], "control")
    args, project = calls[1].args
    assert project == "control" and args[0] == "create" and "--no-owner" not in args
    assert args[args.index("--owner-ref") + 1] == "origin/control/parent#session"
    assert args[args.index("--machine") + 1] == "receiver"
    assert calls[1].kwargs == {"timeout": 210}
    db.update_session_target.assert_called_once()
    persisted = SpawnTarget.from_json(db.update_session_target.call_args.args[1])
    assert persisted.worktree_id == "actual-child" and persisted.cwd == "/worktrees/actual-child"
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=SimpleNamespace(
        timed_out=False, exit_code=0, stdout=json.dumps({
            "launch": {"worktree_id": "actual-child", "work_dir": persisted.cwd},
        }), stderr="",
    )))
    await transport._resolve_worktree_remote(manager, persisted)
    remote_command = manager.exec_command.await_args.args[1]
    assert "--worktree-id actual-child" in remote_command and "--new" not in remote_command
    await owned.prepare_session(session, db)
    assert command.await_count == 2


@pytest.mark.asyncio
async def test_lost_create_capture_recovers_same_durable_intent(monkeypatch):
    session = SimpleNamespace(session_id="bridge-session", target=owned.arm(target(), "bridge-session"))
    persisted = session.target.to_json()
    key = owned._intent(session.target)["creation_key"]
    effects = set()

    async def command(args, project, **kwargs):
        if args[0] == "worktree-allocation":
            return capabilities()
        effects.add(args[args.index("--creation-key") + 1])
        if len(calls) == 0:
            calls.append("lost")
            raise AcquisitionRefused("owner_operation_pending", "capture lost")
        return result(key)

    calls = []
    monkeypatch.setattr(owned, "worktree_command", command)
    db = Mock()
    with pytest.raises(AcquisitionRefused, match="capture lost"):
        await owned.prepare_session(session, db)
    db.update_session_target.assert_not_called()
    session.target = SpawnTarget.from_json(persisted)
    await owned.prepare_session(session, db)
    assert effects == {key} and session.target.worktree_id == "actual-child"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_missing_capability_or_legacy_intent_never_allocates(monkeypatch, legacy):
    session = SimpleNamespace(
        session_id="bridge-session", target=target() if legacy else owned.arm(target(), "bridge-session"),
    )
    command = AsyncMock(return_value={})
    monkeypatch.setattr(owned, "worktree_command", command)
    with pytest.raises(AcquisitionRefused):
        await owned.prepare_session(session, Mock())
    assert command.await_count == (0 if legacy else 1)
    manager = SimpleNamespace(exec_command=AsyncMock())
    with pytest.raises(RuntimeError, match="retained allocation"):
        await transport._resolve_worktree_remote(manager, session.target)
    manager.exec_command.assert_not_called()


@pytest.mark.asyncio
async def test_existing_native_resume_is_not_reclassified_as_owned_allocation(monkeypatch):
    selected = target()
    selected.native_resume_id = "original-native"
    command = AsyncMock(side_effect=AssertionError("existing native resume must not allocate"))
    monkeypatch.setattr(owned, "worktree_command", command)
    session = SimpleNamespace(session_id="existing-session", target=selected)
    await owned.prepare_session(session, Mock())
    manager = SimpleNamespace(exec_command=AsyncMock(return_value=SimpleNamespace(
        timed_out=False, exit_code=0, stdout=json.dumps({
            "launch": {"worktree_id": "original-child", "work_dir": "/worktrees/original-child"},
        }), stderr="",
    )))
    await transport._resolve_worktree_remote(manager, selected)
    assert "--native-resume-session original-native" in manager.exec_command.await_args.args[1]
    command.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("creator", "other/control/parent"), ("linkage", "closed"),
    ("receiving_status", "unknown"), ("creation_key", "different"),
])
async def test_changed_or_unknown_result_never_becomes_an_embodiment(monkeypatch, field, value):
    session = SimpleNamespace(session_id="bridge-session", target=owned.arm(target(), "bridge-session"))
    response = result(owned._intent(session.target)["creation_key"])
    response["allocation"][field] = value
    monkeypatch.setattr(owned, "worktree_command", AsyncMock(side_effect=[capabilities(), response]))
    db = Mock()
    with pytest.raises(AcquisitionRefused):
        await owned.prepare_session(session, db)
    assert session.target.worktree_id is None
    db.update_session_target.assert_not_called()


@pytest.mark.asyncio
async def test_actual_creator_probe_normalizes_legacy_machine_without_rebinding_owner(monkeypatch):
    selected = target()
    selected.caller_owner_ref = "legacy-origin/control/parent#session"
    session = SimpleNamespace(session_id="bridge-session", target=owned.arm(selected, "bridge-session"))
    key = owned._intent(session.target)["creation_key"]
    command = AsyncMock(side_effect=[capabilities(), result(key)])
    monkeypatch.setattr(owned, "worktree_command", command)
    await owned.prepare_session(session, Mock())
    create_args = command.await_args_list[1].args[0]
    assert create_args[create_args.index("--owner-ref") + 1] == "legacy-origin/control/parent#session"
    assert session.target.worktree_id == "actual-child"


@pytest.mark.asyncio
async def test_foreign_or_missing_actual_origin_proof_refuses_before_create(monkeypatch):
    session = SimpleNamespace(session_id="bridge-session", target=owned.arm(target(), "bridge-session"))
    capability = capabilities()
    capability["creator"] = "receiver/control/parent"
    command = AsyncMock(return_value=capability)
    monkeypatch.setattr(owned, "worktree_command", command)
    with pytest.raises(AcquisitionRefused, match="actual local creator"):
        await owned.prepare_session(session, Mock())
    assert command.await_count == 1


@pytest.mark.asyncio
async def test_real_session_manager_persists_intent_and_child_before_native_start(
    session_manager, tmp_db, monkeypatch, _patch_spawn, _patch_acp,
):
    monkeypatch.setattr("agent_bridge.hosting_policy.require_target_hosting", AsyncMock())
    operations = []

    async def command(args, project, **kwargs):
        rows = tmp_db.execute_read("SELECT target_json FROM sessions")
        assert len(rows) == 1
        retained = SpawnTarget.from_json(rows[0]["target_json"])
        intent = owned._intent(retained)
        assert retained.worktree_id is None
        operations.append(args[0])
        return capabilities() if args[0] == "worktree-allocation" else result(intent["creation_key"])

    monkeypatch.setattr(owned, "worktree_command", command)

    async def spawn(selected, **kwargs):
        row = tmp_db.get_session(kwargs["session_id"])
        retained = SpawnTarget.from_json(row["target_json"])
        assert retained.worktree_id == selected.worktree_id == "actual-child"
        assert operations == ["worktree-allocation", "create"]
        return _patch_spawn.return_value

    _patch_spawn.side_effect = spawn
    session = await session_manager.start_session(target())
    assert session.acp_session_id == "acp-test-123"
    assert session.target.worktree_id == "actual-child"
    assert owned._intent(session.target)["creator"] == "origin/control/parent"
