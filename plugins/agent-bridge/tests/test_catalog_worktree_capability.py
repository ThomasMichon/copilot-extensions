"""Passive catalog capability survives the actual declaration/response seams."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.agent_registry import (
    AgentResolver,
    CliNamespaceResolver,
    NamespaceAgentInfo,
    parse_agent_registry,
)
from agent_bridge.agent_registry_cache import AgentRosterCache
from agent_bridge.agent_registry_discovery import DiscoveryResult
from agent_bridge.client import BridgeClient
from agent_bridge.remote_acp_catalog import RemoteTargetEntry
from agent_bridge.routes.agents import router


@pytest.mark.parametrize("support", [True, False, None])
@pytest.mark.asyncio
async def test_declared_capability_reaches_real_cached_http_and_alias_detail(
    support: bool | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agents = parse_agent_registry({
        "demo@remote": {
            "supports_worktrees": support, "project": "demo",
            "display_name": "Configured project name", "aliases": ["chosen"],
        },
    })
    resolver = AgentResolver(agents, {})
    monkeypatch.setattr(
        resolver, "_scan_provider_report",
        lambda **_kwargs: DiscoveryResult(ok=True),
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("passive catalog must not resolve or launch a target")

    monkeypatch.setattr(resolver, "resolve", forbidden)
    monkeypatch.setattr(resolver, "resolve_async", forbidden)
    cache = AgentRosterCache(resolver, refresh_interval=60)
    app = FastAPI()
    app.include_router(router)
    app.state.resolver = resolver
    app.state.agent_roster_cache = cache
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://catalog.test",
        ) as client:
            response = await client.get("/api/v1/agents")
            assert response.status_code == 200
            row = response.json()["agents"][0]
            assert row["supports_worktrees"] is support
            assert row["display_name"] == "Configured project name"
            assert row["aliases"] == ["chosen"]
            detail = await client.get("/api/v1/agents/chosen")
            assert detail.status_code == 200
            assert detail.json()["supports_worktrees"] is support
            assert detail.json()["name"] == row["name"]
            assert detail.json()["spawnable"] == row["spawnable"]
    finally:
        await cache.stop()


@pytest.mark.parametrize("support", [True, False, None])
@pytest.mark.asyncio
async def test_provider_declaration_survives_namespace_json_and_cache(
    support: bool | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CliNamespaceResolver("fixture", "fixture-provider")

    async def output(*_args: Any, **_kwargs: Any) -> tuple[int, str, str]:
        return 0, json.dumps([{
            "name": "demo", "display_name": "Demo project", "aliases": ["alias"],
            "supports_worktrees": support,
        }]), ""

    monkeypatch.setattr(provider, "_run", output)
    resolver = AgentResolver({}, {})
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(
        resolver, "_scan_provider_report",
        lambda **_kwargs: DiscoveryResult(ok=True),
    )
    cache = AgentRosterCache(resolver, refresh_interval=60)
    try:
        for _ in range(2):
            snapshot = await cache.get_snapshot()
            row = next(row for row in snapshot.rows if row["name"] == "fixture:demo")
            assert row["supports_worktrees"] is support
            assert row["display_name"] == "Demo project"
            assert row["aliases"] == ["fixture:alias"]
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_failed_provider_retains_last_good_then_rollback_becomes_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CliNamespaceResolver("fixture", "fixture-provider")
    declaration: dict[str, Any] = {"name": "demo", "supports_worktrees": True}

    async def output(*_args: Any, **_kwargs: Any) -> tuple[int, str, str]:
        return 0, json.dumps([declaration]), ""

    monkeypatch.setattr(provider, "_run", output)
    resolver = AgentResolver({}, {})
    resolver.register_namespace_resolver(provider)
    monkeypatch.setattr(
        resolver, "_scan_provider_report",
        lambda **_kwargs: DiscoveryResult(ok=True),
    )
    cache = AgentRosterCache(resolver, refresh_interval=60)
    try:
        initial = await cache.get_snapshot()
        assert initial.rows[0]["supports_worktrees"] is True
        declaration["supports_worktrees"] = "invalid"
        cache._entries["fixture"].last_refreshed_monotonic = None
        failed = await cache.get_snapshot(force_refresh=True)
        assert "fixture" in failed.incomplete_namespaces
        assert failed.rows == []
        assert cache._entries["fixture"].rows[0]["supports_worktrees"] is True
        del declaration["supports_worktrees"]
        rolled_back = await cache.get_snapshot(force_refresh=True)
        assert rolled_back.complete
        assert rolled_back.rows[0]["supports_worktrees"] is None
    finally:
        await cache.stop()


@pytest.mark.parametrize("invalid", [0, 1, "true", "false", [], {}])
def test_invalid_declarations_are_not_coerced(invalid: Any) -> None:
    with pytest.raises(ValueError, match="supports_worktrees"):
        parse_agent_registry({"demo": {"supports_worktrees": invalid}})
    with pytest.raises(ValueError, match="supports_worktrees"):
        NamespaceAgentInfo(name="demo", supports_worktrees=invalid)


@pytest.mark.parametrize("fields", [
    {"project": "demo", "worktree_root": "/example/worktrees"},
    {"host": "remote", "project": "demo", "spawnable_as_target": True},
    {"managed": True, "worktree_discovery": False},
])
def test_legacy_routing_fields_cannot_establish_support(fields: dict[str, Any]) -> None:
    config = parse_agent_registry({"demo": fields})["demo"]
    row = AgentResolver({"demo": config}, {})._agent_to_dict(config)
    assert row["supports_worktrees"] is None


def test_legacy_namespace_row_remains_unknown() -> None:
    resolver = AgentResolver({}, {})
    provider = CliNamespaceResolver("fixture", "fixture-provider")
    row = resolver._format_namespace_agent(
        "fixture", NamespaceAgentInfo(name="old"), provider,
    )
    assert row["supports_worktrees"] is None


@pytest.mark.parametrize("support", [True, False, None])
def test_unchanged_client_keeps_selection_across_additive_rows_and_rollback(
    support: bool | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = parse_agent_registry({
        "demo": {"supports_worktrees": support, "aliases": ["chosen"]},
    })["demo"]
    row = AgentResolver({"demo": config}, {})._agent_to_dict(config)
    assert row["supports_worktrees"] is support
    response = {"agents": [row]}
    client = BridgeClient.__new__(BridgeClient)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: response)
    for payload in (row, {key: value for key, value in row.items()
                          if key != "supports_worktrees"}, row):
        response["agents"] = [payload]
        observed = client.list_agents()
        assert observed[0]["name"] == "demo"
        assert observed[0]["aliases"] == ["chosen"]
        assert observed[0].get("supports_worktrees") is payload.get("supports_worktrees")


@pytest.mark.asyncio
async def test_remote_acp_reports_the_actual_no_worktree_contract() -> None:
    from fastapi import HTTPException

    from agent_bridge.routes.relationships import start_remote_session
    from agent_bridge.session_manager import SessionManager
    from agent_bridge.session_start_models import StartSessionRequest

    with pytest.raises(HTTPException) as refused:
        await start_remote_session(
            SessionManager.__new__(SessionManager),
            StartSessionRequest(
                agent="remote-acp:demo", worktree_id="example-worktree",
            ),
            "remote-acp:demo",
        )
    assert refused.value.status_code == 422
    assert RemoteTargetEntry("demo", None).row()["supports_worktrees"] is False
