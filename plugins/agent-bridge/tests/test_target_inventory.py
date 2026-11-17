"""The target projection is a union, never a managed-record inner join."""

from __future__ import annotations

from dataclasses import replace
import time
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import worktree_native_resume
from agent_bridge.session_status import SessionStatus
from agent_bridge.routes import worktrees
from agent_bridge.session_host.host_index import HostRecord
from test_worktree_metadata import metadata_boundary as metadata_boundary  # noqa: F401
from test_worktree_native_resume import boundary as boundary  # noqa: F401

pytestmark = pytest.mark.contract("agent_bridge.target_inventory")


@pytest.mark.asyncio
async def test_union_keeps_registration_only_and_every_workspace_without_hydrating(
    metadata_boundary: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = metadata_boundary
    cache = worktrees.WorktreeDiscoveryCache()
    second = replace(bound.entry, machine="other-machine", path=bound.entry.path + "-other")
    cache._cache = {"example": [bound.entry], "other": [second]}
    bound.request.app.state.worktree_discovery_cache = cache
    bound.request.app.state.resolver.agents["other"] = SimpleNamespace(project="other-project")
    bound.db.register_live_session(
        "extension-only", machine="registered-machine", cwd=None, worktree_id=None,
        repo=None, branch=None, pid=None, role=None, now=time.time(),
    )
    bound.manager._host_index.register(HostRecord("host-only", 12345, 1, 2, extra={"cwd": "/other"}))
    monkeypatch.setattr(bound.manager, "_rec_host_alive", lambda _record: True)
    before = len(bound.manager.list_sessions())
    result = await worktree_native_resume.list_targets(bound.request)
    assert {(item["kind"], item["handle"]) for item in result["targets"]} == {
        ("live", "extension-only"), ("session_host", "host-only"), ("worktree", bound.entry.id),
    }
    workspaces = [item for item in result["targets"] if item["kind"] == "worktree"]
    assert len(workspaces) == 2
    assert {item["project"] for item in workspaces} == {"example-project", "other-project"}
    assert all(item["worktree"]["session_id"] is None for item in workspaces)
    assert len(bound.manager.list_sessions()) == before == 0
    assert not bound.calls
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_same_control_host_and_managed_registration_share_one_representation(
    metadata_boundary: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = metadata_boundary
    cache = worktrees.WorktreeDiscoveryCache()
    cache._cache = {"example": [bound.entry]}
    bound.request.app.state.worktree_discovery_cache = cache
    target = replace(
        bound.request.app.state.resolver.resolve("example"), worktree_id=bound.entry.id,
    )
    session = bound.manager.import_native_session(target, "native-original", agent_name="example")
    session.status = SessionStatus.IDLE
    bound.manager._host_index.register(HostRecord(
        session.session_id, 12345, 1, 2, extra={"cwd": target.cwd},
    ))
    monkeypatch.setattr(bound.manager, "_rec_host_alive", lambda _record: True)
    result = await worktree_native_resume.list_targets(bound.request)
    represented = [item for item in result["targets"] if item["handle"] == session.session_id]
    assert len(represented) == 1
    assert represented[0]["sources"] == ["managed", "session_host"]
    assert any(item["kind"] == "worktree" for item in result["targets"])


@pytest.mark.asyncio
async def test_failed_inventory_is_visible_without_losing_other_sources(
    metadata_boundary: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = metadata_boundary
    cache = worktrees.WorktreeDiscoveryCache()
    cache._cache = {"example": [bound.entry]}
    bound.request.app.state.worktree_discovery_cache = cache
    monkeypatch.setattr(cache._refresh, "metadata", lambda: {
        "state": "stale", "error": "owner_unavailable",
    })
    monkeypatch.setattr(bound.manager._host_index, "locked_inventory", lambda: (_ for _ in ()).throw(
        ValueError("controlled incomplete host inventory"),
    ))
    result = await worktree_native_resume.list_targets(bound.request)
    assert result["sources"]["session_host"]["availability"] == "unavailable"
    assert result["sources"]["worktrees"]["error"] == "owner_unavailable"
    assert len(result["targets"]) == 1 and result["targets"][0]["kind"] == "worktree"
    assert not bound.manager.list_sessions()
