"""Tracking-only acquisition reads retain independent native-head authority."""

from unittest.mock import AsyncMock

import pytest

from agent_bridge import acquisition_authority as authority
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest

pytestmark = pytest.mark.contract("agent_bridge.acquisition.worktree_owner")

@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "conflict", "terminal"])
async def test_ground_keyed_creation_preserves_only_typed_pending(monkeypatch, status):
    import json
    from types import SimpleNamespace

    process = SimpleNamespace(
        returncode=3, communicate=AsyncMock(return_value=(
            json.dumps({"creation": {"version": 1, "status": status}}).encode(), b"",
        )),
    )
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(authority.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(AcquisitionRefused) as refused:
        await authority.worktree_command(["create", "--creation-key", "logical-work", "--json"], "project-a")
    assert refused.value.code == ("owner_operation_pending" if status == "pending" else "owner_refused")


@pytest.fixture
def acquisition_request():
    return AcquisitionRequest.model_validate({
        "target": "task-worker", "mode": "cli", "task_id": "task-a",
        "task_generation": 0, "worktree_id": "worktree-a", "allocation": "created",
        "conversation_retired": False, "reservation_key": "reservation-a",
        "delivery_id": "delivery-a", "prompt": "Perform the assigned task",
        "project": "project-a",
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {}, [], None, {"worktrees": []},
    {"worktrees": [{"id": "worktree-a", "repo": "project-a", "path": "/fixture",
                    "status": "finalized"}], "projection": {"quality": "current"}},
    {"version": True, "ok": True, "worktree": {}},
    {"version": 2, "ok": True, "worktree": {}},
    {"version": 1, "ok": 1, "worktree": {}},
    {"version": 1, "ok": True, "worktree": None},
])
async def test_incompatible_record_response_refuses_before_head(
    acquisition_request, monkeypatch, payload,
):
    command = AsyncMock(return_value=payload)
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "owner_response_invalid"
    assert error.value.effect is None
    command.assert_awaited_once_with(
        ["record", "--worktree-id", "worktree-a", "--json"], "project-a",
    )


def record_payload(**changes):
    return {"version": 1, "ok": True, "worktree": {
        "id": "worktree-a", "repo": "project-a", "path": "/fixture", "status": "active",
        "session_count": 0, "last_session_id": None, "creation_nonce": "original-allocation",
        "execution_leg": None, "sessions_indexed": True,
        "execution_leg_opaque": False, "session_backend": None, "session_backend_opaque": False,
        **changes,
    }}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "id", "repo", "path", "status", "creation_nonce", "session_count", "last_session_id",
    "execution_leg", "sessions_indexed", "execution_leg_opaque", "session_backend",
    "session_backend_opaque",
])
async def test_incomplete_record_never_supplies_absent_holder_proof(
    acquisition_request, monkeypatch, field,
):
    payload = record_payload()
    payload["worktree"].pop(field)
    command = AsyncMock(return_value=payload)
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as failure:
        await authority.verify_worktree(acquisition_request)
    assert failure.value.code == "owner_response_invalid"
    assert failure.value.effect is None
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"id": "other"}, {"repo": "other-project"}, {"repo": None},
    {"path": None}, {"path": True}, {"path": "relative"}, {"path": "/fixture/../other"},
    {"status": None}, {"status": {}}, {"status": "completed"},
    {"session_count": False}, {"session_count": -1}, {"last_session_id": []},
    {"creation_nonce": True}, {"sessions_indexed": "false"}, {"execution_leg_opaque": 1},
    {"session_backend_opaque": None},
])
async def test_malformed_or_foreign_record_refuses_before_head(
    acquisition_request, monkeypatch, changes,
):
    command = AsyncMock(return_value=record_payload(**changes))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "owner_response_invalid"
    assert error.value.effect is None
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["finalized", "complete", "archived"])
async def test_positive_terminal_record_refuses_before_head(
    acquisition_request, monkeypatch, status,
):
    command = AsyncMock(return_value=record_payload(status=status))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "worktree_terminal"
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["record-missing", "record-malformed", "record-unreadable", "project-unavailable"])
async def test_explicit_record_failure_is_not_terminal(acquisition_request, monkeypatch, code):
    command = AsyncMock(return_value={
        "version": 1, "ok": False, "worktree": None,
        "error": {"code": code, "message": "synthetic owner failure"},
    })
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "owner_refused"
    assert error.value.effect is None
    assert code in str(error.value)
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode", [1, 2])
async def test_record_nonzero_or_unsupported_command_never_falls_back(
    acquisition_request, monkeypatch, returncode,
):
    from types import SimpleNamespace

    process = SimpleNamespace(
        returncode=returncode,
        communicate=AsyncMock(return_value=(b'{"worktrees":[]}', b"record command unavailable")),
    )
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda: ["worktrees"])
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: {})
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(authority.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "owner_refused" and error.value.effect is None
    assert spawn.await_count == 1
    assert spawn.await_args.args == (
        "worktrees", "--project", "project-a", "record", "--worktree-id", "worktree-a", "--json",
    )


@pytest.mark.asyncio
async def test_missing_record_executable_is_explicit_owner_failure(acquisition_request, monkeypatch):
    monkeypatch.setattr(authority, "_agent_worktrees_launch_prefix", lambda: ["missing-owner"])
    monkeypatch.setattr(authority, "_agent_worktrees_environment", lambda: {})
    spawn = AsyncMock(side_effect=FileNotFoundError("synthetic missing executable"))
    monkeypatch.setattr(authority.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "worktrees_unavailable" and error.value.effect is None
    assert spawn.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("head,code", [
    ({"tracked": False}, "head_unknown"),
    ({"tracked": True, "head_session": "native-a", "active": True, "state": "pending"},
     "transition_pending"),
])
async def test_exact_metadata_never_overrides_native_head(
    acquisition_request, monkeypatch, head, code,
):
    command = AsyncMock(side_effect=[
        record_payload(),
        head,
    ])
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == code
    assert command.await_args_list[1].args == (
        ["head-session", "--worktree-id", "worktree-a", "--json"], "project-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("project,path", [
    ("queue-repo", "/workspace-path"), (None, "/workspace-path"),
    ("execution-repo", "other-path"), ("execution-repo", None),
])
async def test_workspace_owner_identity_must_match_request(
    acquisition_request, monkeypatch, project, path,
):
    request = acquisition_request.model_copy(update={
        "version": 2, "project": "execution-repo", "worktree_path": "/workspace-path",
    })
    command = AsyncMock(return_value=record_payload(repo=project, path=path))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(request)
    assert error.value.code in {"owner_response_invalid", "workspace_identity_conflict"}
    assert command.await_count == 1


@pytest.mark.asyncio
async def test_exact_record_read_does_not_depend_on_roster_publication(acquisition_request, monkeypatch):
    calls = []

    async def command(args, project):
        calls.append(args)
        if args[0] == "list":
            return {"worktrees": [], "projection": {"quality": "current"}}
        if args[0] == "record":
            return record_payload()
        assert args == ["head-session", "--worktree-id", "worktree-a", "--json"]
        return {"tracked": True, "head_session": None, "active": False}

    monkeypatch.setattr(authority, "worktree_command", command)
    row, head = await authority.verify_worktree(acquisition_request)
    assert row["id"] == "worktree-a" and head is None
    assert [args[0] for args in calls] == ["record", "head-session"]
