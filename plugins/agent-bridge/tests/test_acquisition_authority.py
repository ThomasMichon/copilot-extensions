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
    command.assert_awaited_once_with(
        ["record", "--worktree-id", "worktree-a", "--json"], "project-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("head,code", [
    ({"tracked": False}, "head_unknown"),
    ({"tracked": True, "head_session": "native-a", "active": True, "state": "pending"},
     "transition_pending"),
])
async def test_cached_display_never_overrides_native_head(
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
