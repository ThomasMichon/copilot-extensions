"""Use owner-produced shapes for identity, presence and current routing."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge.bridge_inventory import inventory, project_bridges
from agent_bridge.routes.worktree_entry import parse_worktree_list
from test_bridge_inventory import registration, sources, workspace
from test_worktree_metadata import metadata_boundary as metadata_boundary
from test_worktree_native_resume import boundary as boundary


@pytest.mark.parametrize("fresh", [False, True])
def test_actual_owner_hint_survives_parsing_but_not_its_expired_window(fresh: bool) -> None:
    raw = {
        "version": 1,
        "worktrees": [
            {
                "id": "workspace-a",
                "machine": "host-a",
                "path": "/work/host-a/workspace-a",
                "session_count": 1,
                "session_bound_live": True,
                "kind": "managed",
                "activity": "Checking results",
                "last_session_id": "native-owner",
            }
        ],
    }
    (entry,) = parse_worktree_list(json.dumps(raw), "demo@host-a")
    evidence = sources()
    evidence["worktrees"]["freshness"] = "fresh" if fresh else "stale"
    (row,) = project_bridges(
        [
            {
                "kind": "worktree",
                "handle": entry.id,
                "project": "demo",
                "worktree": entry.to_dict(),
            }
        ],
        evidence,
        project="demo",
    )["bridges"]
    assert row["live"] is (True if fresh else None)
    assert row["status"] == ("running" if fresh else "retained")
    assert row["session_id"] == "native-owner"
    assert row["disposition"]["activity"] == "Checking results"


def test_attached_host_observation_keeps_an_idle_managed_anchor_visible() -> None:
    managed = {
        "kind": "managed",
        "handle": "control-a",
        "sources": ["managed", "session_host"],
        "session": {
            "session_id": "control-a",
            "acp_session_id": "native-a",
            "durable_session_id": "native-a",
            "project": "demo",
            "target_type": "local",
            "target_dir": "/work/demo",
            "status": "idle",
            "liveness": None,
            "mode": "acp",
        },
        "registration": {
            "session_id": "control-a",
            "boundary": "local",
            "liveness": "observed",
        },
    }
    (row,) = project_bridges([managed], sources(), project="demo", live=True)["bridges"]
    assert row["hosting"] == "acp"
    assert row["status"] == "running"
    assert row["managed_session_id"] == "control-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("same_native", [False, True])
async def test_interactive_resume_routes_to_registration_not_terminal_control(
    monkeypatch: pytest.MonkeyPatch,
    same_native: bool,
) -> None:
    from agent_bridge import target_inventory

    item = workspace()
    native = registration(item)
    old = {
        "kind": "managed",
        "handle": "old-control",
        "sources": ["managed"],
        "session": {
            "session_id": "old-control",
            "acp_session_id": "native-a" if same_native else "previous-native",
            "project": "demo",
            "worktree_id": "workspace-a",
            "target_type": "local",
            "target_dir": item["worktree"]["path"],
            "status": "stopped",
            "mode": "acp",
        },
    }

    async def current(_request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"version": 1, "targets": [item, old, native], "sources": sources()}

    monkeypatch.setattr(target_inventory, "inventory", current)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                session_manager=SimpleNamespace(list_sessions=lambda: []),
                resolver=SimpleNamespace(_local_machine=SimpleNamespace(key="host-a")),
            )
        )
    )
    (row,) = (await inventory(request, project="demo"))["bridges"]
    assert row["route"] == {"host": "host-a", "handle": "native-a", "kind": "live"}
    assert row["managed_session_id"] is None
    assert row["hosting"] == "mux"
    assert row["session_id"] == "native-a"


def test_sequential_ended_conversations_do_not_claim_simultaneous_ownership() -> None:
    item = workspace(count=2, live=False)
    item["worktree"]["last_session_id"] = "native-latest"
    ended = [
        {
            "kind": "managed",
            "handle": native,
            "sources": ["managed"],
            "session": {
                "session_id": "control-" + native,
                "acp_session_id": native,
                "project": "demo",
                "worktree_id": "workspace-a",
                "target_type": "local",
                "target_dir": item["worktree"]["path"],
                "status": "ended",
            },
        }
        for native in ("native-earlier", "native-latest")
    ]
    for history in (ended, list(reversed(ended))):
        result = project_bridges([item, *history], sources(), project="demo")
        (row,) = result["bridges"]
        assert row["session_id"] == "native-latest"
        assert row["control_reason"] != "identity_ambiguous"
        assert result["complete"] is True


@pytest.mark.asyncio
async def test_alias_is_normalized_before_join_and_machine_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import target_inventory

    item = workspace(machine="execution-b")
    managed = {
        "kind": "managed",
        "handle": "control-a",
        "sources": ["managed"],
        "session": {
            "session_id": "control-a",
            "acp_session_id": "native-a",
            "project": "demo",
            "worktree_id": "workspace-a",
            "target_type": "ssh",
            "target_host": "transport-alias",
            "target_dir": item["worktree"]["path"],
            "status": "running",
            "liveness": "active",
        },
    }
    original = deepcopy(managed)

    async def current(_request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"version": 1, "targets": [item, managed], "sources": sources()}

    monkeypatch.setattr(target_inventory, "inventory", current)
    resolver = SimpleNamespace(
        _local_machine=SimpleNamespace(key="observer-a"),
        resolve_ssh_environment=lambda _address: (SimpleNamespace(key="execution-b"), None),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=resolver,
                session_manager=SimpleNamespace(list_sessions=lambda: []),
            )
        )
    )
    (row,) = (await inventory(request, project="demo", machine="execution-b"))["bridges"]
    assert row["managed_session_id"] == "control-a"
    assert row["transport_host"] == "transport-alias"
    assert managed == original


@pytest.mark.asyncio
async def test_local_only_reads_completed_cache_without_warming_unselected_hosts(
    metadata_boundary: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = metadata_boundary
    state = bound.request.app.state
    state.resolver._local_machine = SimpleNamespace(key=bound.entry.machine)
    config = state.resolver.agents["example"]
    config.worktree_discovery, config.host, config.provider = True, None, None
    cache = state.worktree_discovery_cache
    local = replace(
        bound.entry, session_count=1, session_bound_live=True, last_session_id="native-a"
    )
    remote = replace(local, id="remote-workspace", machine="unselected-machine")
    cache._cache = {"example": [local], "unselected": [remote]}
    monkeypatch.setattr(cache, "crawl_if_empty", lambda: pytest.fail("unscoped crawl"))
    monkeypatch.setattr(cache, "render_snapshot", lambda: pytest.fail("unscoped warm"))
    monkeypatch.setattr(cache, "_ensure_warm", lambda: pytest.fail("unselected source warm"))
    (row,) = (
        await inventory(
            bound.request,
            local_only=True,
            project="example-project",
            live=True,
        )
    )["bridges"]
    assert row["session_id"] == "native-a"
    assert not bound.calls
    bound.manager.start_session.assert_not_called()
    bound.manager.resume_session.assert_not_called()


@pytest.mark.asyncio
async def test_real_failed_serializer_fallback_does_not_create_native_history(
    metadata_boundary: Any,
) -> None:
    from agent_bridge.routes.sessions import _session_info
    from agent_bridge.session_status import SessionStatus

    bound = metadata_boundary
    state = bound.request.app.state
    target = replace(state.resolver.resolve("example"), worktree_id=bound.entry.id)
    failed = bound.manager.import_native_session(
        target, "discarded-fixture-id", agent_name="example"
    )
    failed.acp_session_id = None
    failed.status = SessionStatus.FAILED
    serialized = _session_info(failed, bound.manager).model_dump(mode="json")
    assert serialized["durable_session_id"] == failed.session_id
    state.worktree_discovery_cache._cache = {"example": [replace(bound.entry, session_count=0)]}
    assert (await inventory(bound.request, project="example-project"))["bridges"] == []


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("unavailable", [False, True])
def test_local_catalog_availability_is_independent_of_cached_row_count(
    metadata_boundary: Any,
    empty: bool,
    unavailable: bool,
) -> None:
    from agent_bridge.bridge_observations import cached_local_worktrees

    state = metadata_boundary.request.app.state
    resolver = state.resolver
    resolver._local_machine = SimpleNamespace(key=metadata_boundary.entry.machine)
    resolver.repository_inventory_unavailable = unavailable
    if empty:
        resolver.agents = {}
        state.worktree_discovery_cache._cache = {}
    else:
        config = resolver.agents["example"]
        config.worktree_discovery, config.host, config.provider = True, None, None
    result = cached_local_worktrees(metadata_boundary.request)
    assert result["discovery"]["completeness"] == ("incomplete" if unavailable else "complete")
    assert result["discovery"]["repository_inventory"]["availability"] == (
        "unavailable" if unavailable else "available"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [None, False, True])
async def test_idle_ssh_presence_uses_attached_client_through_real_serialization(
    metadata_boundary: Any,
    monkeypatch: pytest.MonkeyPatch,
    connected: bool | None,
) -> None:
    from agent_bridge import target_inventory
    from agent_bridge.routes import worktrees
    from agent_bridge.session_host.host_index import HostRecord
    from agent_bridge.session_manager import Session
    from agent_bridge.session_status import SessionStatus

    bound = metadata_boundary
    state = bound.request.app.state
    state.resolver._local_machine = SimpleNamespace(key="observer-a")
    state.resolver.resolve_ssh_environment = lambda _address: (
        SimpleNamespace(key="execution-b"),
        None,
    )
    target = replace(
        state.resolver.resolve("example"),
        type="ssh",
        host="transport-alias",
        worktree_id=None,
    )
    session: Any = Session("control-idle", "Idle conversation", target, agent_name="example")
    session.acp_session_id = "native-idle"
    session.status = SessionStatus.IDLE
    bound.db.create_session(
        session_id=session.session_id,
        name=session.name,
        agent_name=session.agent_name,
        caller_id=None,
        target_dir=target.cwd,
        target_type=target.type,
        status=session.status.value,
        now=session.created_at,
        target_json=target.to_json(),
        acp_session_id=session.acp_session_id,
    )
    bound.manager._sessions[session.session_id] = session
    session.client = (
        None if connected is None else SimpleNamespace(is_running=connected, pid=12345)
    )
    bound.manager._host_index.register(
        HostRecord(session.session_id, 12345, 1, 2, boundary="ssh", extra={"cwd": target.cwd})
    )
    monkeypatch.setattr(bound.manager, "_rec_host_alive", lambda _record: True)

    async def cached(_request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"groups": {}, "discovery": sources()["worktrees"]}

    monkeypatch.setattr(worktrees, "list_worktrees", cached)
    catalog = await target_inventory.inventory(bound.request)
    (item,) = [item for item in catalog["targets"] if item.get("kind") == "managed"]
    assert item["session"]["session_live"] is connected
    result = await inventory(bound.request, project="example-project", live=True)
    assert len(result["bridges"]) == int(connected is True)
    if connected:
        (row,) = result["bridges"]
        assert row["machine"] == "execution-b"
        assert row["managed_session_id"] == session.session_id
        assert row["session_id"] == "native-idle"
