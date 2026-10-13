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
@pytest.mark.parametrize("rows,code", [
    ([], "worktree_unknown"),
    ([{"id": "other", "path": "/fixture", "status": "active"}], "worktree_unknown"),
    ([{"id": "worktree-a", "status": "active"}], "worktree_unknown"),
    ([{"id": "worktree-a", "path": "/fixture"}] * 2, "worktree_unknown"),
    *[([{"id": "worktree-a", "path": "/fixture", "status": status}], "worktree_terminal")
      for status in ("finalized", "complete", "completed", "archived")],
])
async def test_invalid_tracking_record_refuses_before_head(
    acquisition_request, monkeypatch, rows, code,
):
    command = AsyncMock(return_value={"worktrees": rows})
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(acquisition_request)
    assert error.value.code == code
    command.assert_awaited_once_with(
        ["list", "--json", "--cache-only", "--worktree-id", "worktree-a"], "project-a",
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
        {"worktrees": [{
            "id": "worktree-a", "path": "/fixture", "status": "active",
            "state": "resolved", "session_count": 0,
        }]},
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
    ("queue-repo", "workspace-path"), (None, "workspace-path"),
    ("execution-repo", "other-path"), ("execution-repo", None),
])
async def test_workspace_owner_identity_must_match_request(
    acquisition_request, monkeypatch, project, path,
):
    request = acquisition_request.model_copy(update={
        "version": 2, "project": "execution-repo", "worktree_path": "workspace-path",
    })
    command = AsyncMock(return_value={"worktrees": [{
        "id": "worktree-a", "repo": project, "path": path,
    }]})
    monkeypatch.setattr(authority, "worktree_command", command)
    with pytest.raises(AcquisitionRefused) as error:
        await authority.verify_worktree(request)
    assert error.value.code in {"worktree_unknown", "workspace_identity_conflict"}
    assert command.await_count == 1
