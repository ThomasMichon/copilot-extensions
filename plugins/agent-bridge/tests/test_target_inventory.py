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
from agent_bridge.session_manager import Session, SessionManager
from test_worktree_metadata import metadata_boundary as metadata_boundary  # noqa: F401
from test_worktree_native_resume import boundary as boundary  # noqa: F401

pytestmark = pytest.mark.contract("agent_bridge.target_inventory")


@pytest.mark.asyncio
async def test_first_target_lookup_and_restart_after_index_loss(metadata_boundary: Any, tmp_path: Any) -> None:
    from agent_bridge.client import BridgeClientError
    from agent_bridge.target_resolution_cli import resolve_workspace

    root = tmp_path / "new-hosts"
    state = metadata_boundary.request.app.state
    for restarting in (False, True):
        state.session_manager = SessionManager(metadata_boundary.db, session_host_state_dir=str(root))
        observed = await worktree_native_resume.list_targets(metadata_boundary.request)
        client = SimpleNamespace(list_targets=lambda: observed)
        if restarting:
            assert observed["sources"]["session_host"]["reason"] == "inventory_missing"
            with pytest.raises(BridgeClientError) as refused:
                resolve_workspace(client, "new-project")
            assert refused.value.status == 503
            assert not (root / "index.json").exists()
        else:
            assert observed["sources"]["session_host"]["availability"] == "available"
            assert resolve_workspace(client, "new-project") is None
            (root / "index.json").unlink()


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
@pytest.mark.parametrize("mismatch", [None, "owner", "project", "path", "transport"])
async def test_duplicate_handle_linkage_requires_the_complete_workspace_scope(
    metadata_boundary: Any, mismatch: str | None,
) -> None:
    bound = metadata_boundary
    cache = worktrees.WorktreeDiscoveryCache()
    other = replace(
        bound.entry, agent_name="other", machine="other-machine", path=bound.entry.path + "-other",
    )
    cache._cache = {"example": [bound.entry], "other": [other]}
    bound.request.app.state.worktree_discovery_cache = cache
    resolver = bound.request.app.state.resolver
    resolver.agents["other"] = SimpleNamespace(project="other-project", host="other-host")
    target = replace(resolver.resolve("example"), worktree_id=bound.entry.id)
    if mismatch == "project":
        target = replace(target, project="different-project")
    elif mismatch == "path":
        target = replace(target, cwd=bound.entry.path + "-different")
    elif mismatch == "transport":
        target = replace(target, type="ssh", host="different-host")
    agent = "foreign-owner" if mismatch == "owner" else "example"
    session = Session("managed-linkage", "existing controller", target, agent_name=agent)
    session.acp_session_id = "native-original"
    session.status = SessionStatus.IDLE
    bound.db.create_session(
        session_id=session.session_id, name=session.name, agent_name=agent, caller_id=None,
        target_dir=target.cwd, target_type=target.type, status=session.status.value,
        now=time.time(), target_json=target.to_json(), acp_session_id=session.acp_session_id,
    )
    bound.manager._sessions[session.session_id] = session
    result = await worktree_native_resume.list_targets(bound.request)
    rows = {item["owner"]: item["worktree"] for item in result["targets"] if item["kind"] == "worktree"}
    assert rows["other"]["session_id"] is None
    assert rows["other"]["acp_session_id"] is None and rows["other"]["session_live"] is False
    assert rows["example"]["session_id"] == (session.session_id if mismatch is None else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("predecessor_status", [SessionStatus.STOPPED, SessionStatus.IDLE])
async def test_real_handoff_projection_prefers_live_successor_and_honors_exclusion(
    metadata_boundary: Any, predecessor_status: SessionStatus,
) -> None:
    from agent_bridge.client import BridgeClientError
    from agent_bridge.routes.sessions import _session_info
    from agent_bridge.session_start_cli import _resolve_read_worktree_session
    from agent_bridge.session_targeting_cli import _resolve_target

    bound = metadata_boundary
    target = replace(bound.request.app.state.resolver.resolve("example"), worktree_id=bound.entry.id)
    now = time.time()
    for sid, status, age, selected in [
        ("predecessor", predecessor_status, 1, target),
        ("successor", SessionStatus.IDLE, 0, target),
        ("foreign", SessionStatus.IDLE, 2, replace(target, type="ssh", host="foreign-host")),
    ]:
        session = Session(sid, sid, selected, agent_name="example")
        session.status, session.acp_session_id, session.updated_at = status, "native-" + sid, now + age
        bound.db.create_session(
            session_id=sid, name=sid, agent_name="example", caller_id=None,
            target_dir=selected.cwd, target_type=selected.type, status=status.value,
            now=now + age, target_json=selected.to_json(), acp_session_id=session.acp_session_id,
        )
        bound.manager._sessions[sid] = session
    inventory = await worktree_native_resume.list_targets(bound.request)
    workspace = next(item for item in inventory["targets"] if item["kind"] == "worktree")
    if predecessor_status == SessionStatus.STOPPED:
        assert workspace["worktree"]["session_id"] == "successor"

    def get_session(sid: str) -> dict[str, Any]:
        existing = bound.manager.get_session(sid)
        if existing is None:
            raise BridgeClientError(404, "missing")
        return _session_info(existing, bound.manager).model_dump(mode="json")

    def no_head_probe(_target: str) -> Any:
        raise AssertionError("a qualified successor must not require metadata/head hydration")

    client = SimpleNamespace(
        list_targets=lambda: inventory, get_session=get_session,
        list_sessions=lambda: [get_session(session.session_id) for session in bound.manager.list_sessions()],
        hydrate_worktree=no_head_probe,
    )
    assert _resolve_read_worktree_session(
        client, bound.entry.id, exclude_session_id="predecessor",
    ) == "successor"
    if predecessor_status == SessionStatus.STOPPED:
        assert _resolve_target(client, bound.entry.id) == "successor"
    assert not bound.calls
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()


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
