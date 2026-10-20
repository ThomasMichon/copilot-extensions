"""Tracking-only acquisition reads retain independent native-head authority."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_bridge import acquisition_authority as authority
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest

pytestmark = pytest.mark.contract("agent_bridge.acquisition.worktree_owner")

_VECTORS = json.loads((
    Path(__file__).resolve().parents[1]
    / "contract/fixtures/http/current/workspace-exact-owner-generation-41.json"
).read_text(encoding="utf-8"))


def _owner_result(row):
    if isinstance(row, dict):
        row = {
            "repo": "project-a", "creation_nonce": "original-allocation",
            "session_count": 0, "last_session_id": None, "execution_leg": None,
            "execution_leg_opaque": False, "session_backend": None,
            "session_backend_opaque": False, "sessions_indexed": True,
            **row,
        }
    return {"version": 1, "ok": True, "worktree": row}

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
@pytest.mark.parametrize("rows,code", [
    ([], "owner_response_invalid"),
    ([{"id": "other", "path": "/fixture", "status": "active"}], "owner_response_invalid"),
    ([{"id": "worktree-a", "status": "active"}], "owner_response_invalid"),
    ([{"id": "worktree-a", "path": "/fixture"}] * 2, "owner_response_invalid"),
    *[([{"id": "worktree-a", "path": "/fixture", "status": status}], "worktree_terminal")
      for status in ("finalized", "complete", "completed", "archived")],
])
async def test_invalid_tracking_record_refuses_before_head(
    acquisition_request, monkeypatch, rows, code,
):
    command = AsyncMock(return_value=_owner_result(rows[0] if len(rows) == 1 else rows))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == code
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
    with pytest.raises(AcquisitionRefused) as failure:
        await authority.verify_worktree(acquisition_request)
    assert failure.value.code == "owner_response_invalid"
    assert failure.value.effect is None
    assert command.await_count == 1


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
    {"id": "other"}, {"repo": None},
    {"path": None}, {"path": True}, {"path": "relative"}, {"path": "/fixture/../other"},
    {"status": None}, {"status": {}}, {"status": "unsupported"},
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
@pytest.mark.parametrize("status", ["finalized", "complete", "completed", "archived"])
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
@pytest.mark.parametrize("status", ["finalizing", "completed"])
async def test_durable_and_legacy_statuses_are_valid_metadata(monkeypatch, status):
    command = AsyncMock(return_value=record_payload(status=status))
    row = await authority.read_worktree_record("worktree-a", "project-a", command=command)
    assert row["status"] == status
    assert command.await_count == 1


@pytest.mark.asyncio
async def test_finalizing_record_retryably_refuses_before_head(acquisition_request, monkeypatch):
    command = AsyncMock(return_value=record_payload(status="finalizing"))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "transition_pending"
    assert error.value.effect is None
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
        _owner_result({
            "id": "worktree-a", "path": "/fixture", "status": "active",
            "state": "resolved", "session_count": 0,
        }),
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
@pytest.mark.parametrize("project,path,code", [
    ("queue-repo", "/fixture", "workspace_identity_conflict"),
    (None, "/fixture", "owner_response_invalid"),
    ("execution-repo", "/other-path", "workspace_identity_conflict"),
    ("execution-repo", None, "owner_response_invalid"),
])
async def test_workspace_owner_identity_must_match_request(
    acquisition_request, monkeypatch, project, path, code,
):
    request = acquisition_request.model_copy(update={
        "version": 2, "project": "execution-repo", "worktree_path": "/fixture",
    })
    command = AsyncMock(return_value=_owner_result({
        "id": "worktree-a", "repo": project, "path": path, "status": "active",
    }))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(request)
    assert error.value.code == code
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repo,nonce", _VECTORS["legacy_metadata_cases"])
async def test_version_one_accepts_owner_supported_legacy_metadata(
    acquisition_request, monkeypatch, repo, nonce,
):
    assert acquisition_request.version == 1
    command = AsyncMock(side_effect=[
        _owner_result({
            "id": "worktree-a", "repo": repo, "path": "/fixture", "status": "active",
            "creation_nonce": nonce, "session_count": 1, "last_session_id": "native-a",
        }),
        {"tracked": True, "head_session": "native-a", "active": True, "state": "active"},
    ])
    monkeypatch.setattr(authority, "worktree_command", command)
    row, head = await authority.verify_worktree(acquisition_request)
    assert (row["repo"], row["creation_nonce"], head) == (repo, nonce, "native-a")
    assert command.await_count == 2
    assert all(call.args[1] == "project-a" for call in command.await_args_list)


@pytest.mark.asyncio
async def test_version_two_companion_repository_still_refuses(
    acquisition_request, monkeypatch,
):
    request = acquisition_request.model_copy(update={
        "version": 2, "worktree_path": "/fixture",
    })
    command = AsyncMock(return_value=_owner_result({
        "id": "worktree-a", "repo": "companion-repo", "path": "/fixture", "status": "active",
    }))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(request)
    assert error.value.code == "workspace_identity_conflict"
    assert command.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("last_session", ["", " ", 0, False, {}])
async def test_legacy_last_session_identity_remains_strict(
    acquisition_request, monkeypatch, last_session,
):
    command = AsyncMock(return_value=_owner_result({
        "id": "worktree-a", "path": "/fixture", "status": "active",
        "creation_nonce": None, "last_session_id": last_session,
    }))
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == "owner_response_invalid"
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
