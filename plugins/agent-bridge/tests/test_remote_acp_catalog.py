"""Synthetic contribution, binding, roster and endpoint-policy contracts."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from agent_bridge.agent_registry import AgentResolver
from agent_bridge.models import ServiceConfig, StartSessionRequest
from agent_bridge.remote_acp_catalog import RemoteAcpCatalog
from agent_bridge.remote_acp_config import RemoteAcpBinding, RemoteAcpTarget, canonical_endpoint
from agent_bridge.remote_acp_sources import (
    RemoteContribution,
    RemoteContributionReport,
    discover_remote_contributions,
)
from agent_bridge.routes.agents import get_agent, list_agents
from agent_bridge.routes.sessions import start_session
from agent_bridge.session_manager import session_manager_from_config


def configured(**changes):
    return RemoteAcpTarget.model_validate({
        "endpoint": "https://example.com/acp",
        "transport": "streamable-http",
        "auth": {"source": "git-credential", "identity": "synthetic"},
        **changes,
    })


@pytest.fixture
def contribution(tmp_path, monkeypatch):
    from agent_bridge import remote_acp_sources

    root = tmp_path / "payload"
    root.mkdir()
    folder = root / ".agent-bridge"
    folder.mkdir()
    providers = folder / "providers.d"
    providers.mkdir()
    (root / "plugin.json").write_text(json.dumps({
        "name": "synthetic-harness",
        "bridgeConfig": ".agent-bridge/config.yaml",
        "bridgeProviders": ".agent-bridge/providers.d",
    }))
    (folder / "config.yaml").write_text(json.dumps({
        "remote_acp": {"assistant": {
            "transport": "streamable-http", "description": "Synthetic remote agent",
        }},
    }))
    (providers / "assistant.json").write_text(json.dumps({
        "schema_version": 1, "kind": "remote-acp", "name": "assistant",
        "target": "assistant",
    }))
    plugin = ActivePlugin(
        source="synthetic-harness@example", name="synthetic-harness",
        marketplace="example", root=root, scopes=("project:demo",),
    )
    activation = ActivationReport(
        ScanAuthority.COMPLETE, {plugin.source: EntryDecision.active(plugin)},
    )
    monkeypatch.setattr(remote_acp_sources, "resolve_active_plugins", lambda: activation)
    return root, plugin, activation


def binding(**changes):
    return RemoteAcpBinding.model_validate({
        "plugin": "synthetic-harness@example",
        "endpoint": "https://example.com/acp",
        "identity": "synthetic",
        **changes,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_roster_poll_binding_reads_never_run_on_event_loop(contribution, cached):
    import threading

    from agent_bridge.agent_registry_cache import AgentRosterCache

    loop_thread = threading.get_ident()
    calls = []

    def project_bindings(project):
        calls.append((project, threading.get_ident()))
        assert threading.get_ident() != loop_thread
        return {"assistant": binding()}

    catalog = RemoteAcpCatalog({}, project_bindings=project_bindings)
    catalog.apply(discover_remote_contributions())
    resolver = AgentResolver({}, {})
    resolver.remote_acp_catalog = catalog
    cache = AgentRosterCache(resolver, refresh_interval=60)
    try:
        for _ in range(2):
            rows = (await cache.get_snapshot()).rows if cached else await resolver.list_agents_async()
            remote = next(row for row in rows if row["name"] == "remote-acp:assistant")
            assert remote["state"] == "bound" and remote["project"] == "demo"
        assert len(calls) == 2 and all(project == "demo" for project, _ in calls)
    finally:
        await cache.stop()


def test_unbound_contribution_is_visible_without_connection_or_credential_lookup(contribution):
    report = discover_remote_contributions()
    assert report.errors == []
    assert list(report.targets) == ["assistant"]
    catalog = RemoteAcpCatalog({}, discover=lambda: report)
    row = catalog.rows()[0]
    assert row["name"] == "remote-acp:assistant"
    assert row["target_type"] == "remote-acp"
    assert row["state"] == "requires-binding" and not row["spawnable"]
    assert row["project"] == "demo" and row["plugin"] == "synthetic-harness@example"
    assert catalog.rows("other") == []
    with pytest.raises(ValueError, match="binding"):
        catalog.resolve("remote-acp:assistant", project="demo")


@pytest.mark.asyncio
@pytest.mark.parametrize("require_complete", [False, True])
@pytest.mark.parametrize("change", ["replacement", "removal", "remote-withdrawal", "remote-error"])
async def test_project_route_reconciles_provider_replaced_during_binding_read(
    contribution, monkeypatch, require_complete, change,
):
    import threading

    import httpx
    from fastapi import FastAPI

    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.agent_registry_cache import AgentRosterCache
    from agent_bridge.routes.agents import router

    catalog = RemoteAcpCatalog({}, project_bindings=lambda project: {"assistant": binding()})
    catalog.apply(discover_remote_contributions())
    resolver = AgentResolver({}, {})
    resolver.remote_acp_catalog = catalog
    resolver.register_namespace_resolver(SimpleNamespace(
        prefix="ns", list=AsyncMock(return_value=[NamespaceAgentInfo(name="old")]),
        invalidate_list_cache=lambda: None,
    ))
    cache = AgentRosterCache(resolver, refresh_interval=60)
    assert (await cache.get_snapshot()).complete
    started, release = threading.Event(), threading.Event()
    original = catalog.rows

    def rows(project=None, *, refresh=True):
        composed = original(project, refresh=refresh)
        if project == "demo":
            started.set()
            assert release.wait(2)
        return composed

    monkeypatch.setattr(catalog, "rows", rows)
    app = FastAPI()
    app.state.resolver, app.state.agent_roster_cache = resolver, cache
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.invalid",
    ) as api:
        pending = asyncio.create_task(api.get("/api/v1/agents", params={
            "project": "demo", "require_complete": str(require_complete).lower(),
        }))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            if change.startswith("remote-"):
                catalog.apply(RemoteContributionReport(
                    errors=["synthetic contribution unavailable"] if change == "remote-error" else [],
                ))
            else:
                resolver.unregister_namespace_resolver("ns")
                if change == "replacement":
                    resolver.register_namespace_resolver(SimpleNamespace(
                        prefix="ns", list=AsyncMock(return_value=[NamespaceAgentInfo(name="new")]),
                        invalidate_list_cache=lambda: None,
                    ))
            release.set()
            response = await pending
            if require_complete and change != "removal":
                assert response.status_code == 503
            else:
                assert response.status_code == 200
                data = response.json()
                assert len(data["incomplete_namespaces"]) == len(set(data["incomplete_namespaces"]))
                if change.startswith("remote-"):
                    assert "remote-acp" in data["incomplete_namespaces"]
                    assert not any(row["target_type"] == "remote-acp" for row in data["agents"])
                else:
                    assert ("ns" in data["incomplete_namespaces"]) == (change == "replacement")
                    assert not any(row["name"] == "ns:old" for row in data["agents"])
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await cache.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [None, "demo"])
@pytest.mark.parametrize("phase", ["composition", "namespace"])
@pytest.mark.parametrize("topology_ready", [False, True])
async def test_uncached_fallback_fences_remote_withdrawal_after_final_await(
    contribution, monkeypatch, project, phase, topology_ready,
):
    import threading

    import httpx
    from fastapi import FastAPI

    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.routes.agents import router

    catalog = RemoteAcpCatalog({}, project_bindings=lambda project: {"assistant": binding()})
    catalog.apply(discover_remote_contributions())
    resolver = AgentResolver({}, {})
    resolver.remote_acp_catalog = catalog
    started, release = threading.Event(), threading.Event()
    original = catalog.rows

    def rows(project=None, *, refresh=True):
        composed = original(project, refresh=refresh)
        if phase == "composition":
            started.set()
            assert release.wait(2)
        return composed

    async def namespace_rows():
        if phase == "namespace":
            started.set()
            assert await asyncio.to_thread(release.wait, 2)
        return [NamespaceAgentInfo(name="healthy")]

    monkeypatch.setattr(catalog, "rows", rows)
    resolver.register_namespace_resolver(SimpleNamespace(
        prefix="ns", list=namespace_rows, invalidate_list_cache=lambda: None,
    ))
    app = FastAPI()
    app.state.resolver, app.state.topology_ready = resolver, topology_ready
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://bridge.invalid",
    ) as api:
        pending = asyncio.create_task(api.get(
            "/api/v1/agents", params={"project": project} if project else {},
        ))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            catalog.apply(RemoteContributionReport())
            release.set()
            response = await pending
            assert response.status_code == 200
            data = response.json()
            assert data["incomplete_namespaces"] == ["remote-acp"]
            assert [row["name"] for row in data["agents"]] == ["ns:healthy"]
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)


def test_binding_is_owned_project_qualified_and_url_uses_same_policy(contribution):
    catalog = RemoteAcpCatalog({}, bindings={"assistant": binding()})
    row = catalog.rows()[0]
    assert row["state"] == "bound" and row["spawnable"]
    name, target = catalog.resolve("https://EXAMPLE.com:443/acp", project="demo")
    assert name == "assistant" and target.auth.identity == "synthetic"
    assert catalog.resolve("remote-acp:assistant", project="demo") == (name, target)
    for project in (None, "other"):
        with pytest.raises(ValueError, match="project"):
            catalog.resolve("remote-acp:assistant", project=project)
    catalog.bindings["assistant"] = binding(plugin="foreign@example")
    assert catalog.rows()[0]["unavailable_reason"] == "binding-owner-mismatch"
    with pytest.raises(ValueError):
        catalog.resolve("https://example.com/acp", project="demo")


@pytest.mark.parametrize("address", ["remote-acp:assistant", "https://example.com/acp"])
def test_global_and_project_activation_does_not_infer_project_binding(contribution, address):
    from dataclasses import replace

    report = discover_remote_contributions()
    report.targets["assistant"] = replace(
        report.targets["assistant"], scopes=("global", "project:demo"),
    )
    contexts = []

    def project_bindings(project):
        contexts.append(project)
        return {"assistant": binding()} if project == "demo" else {}

    catalog = RemoteAcpCatalog(
        {}, discover=lambda: report, project_bindings=project_bindings,
    )
    catalog.apply(report)
    cached = catalog.contribution_report
    with pytest.raises(ValueError):
        catalog.resolve(address)
    row = catalog.rows(refresh=False)[0]
    assert row["global_scope"] and row["state"] == "requires-binding"
    assert not row["spawnable"]
    assert catalog.detail("remote-acp:assistant") == row
    assert contexts == []
    assert catalog.resolve(address, project="demo")[0] == "assistant"
    assert contexts == ["demo"]
    with pytest.raises(ValueError):
        catalog.resolve(address, project="other")
    with pytest.raises(ValueError):
        catalog.resolve(address)
    assert catalog.contribution_report is cached


@pytest.mark.parametrize("address", ["remote-acp:assistant", "https://service.example.com/acp"])
def test_global_and_project_activation_accepts_explicit_service_binding(contribution, address):
    from dataclasses import replace

    report = discover_remote_contributions()
    report.targets["assistant"] = replace(
        report.targets["assistant"], scopes=("global", "project:demo"),
    )

    def project_bindings(project):
        raise AssertionError("unscoped service binding must not read project bindings")

    catalog = RemoteAcpCatalog(
        {}, bindings={"assistant": binding(endpoint="https://service.example.com/acp")},
        discover=lambda: report, project_bindings=project_bindings,
    )
    name, target = catalog.resolve(address)
    assert name == "assistant" and target.endpoint == "https://service.example.com/acp"
    row = catalog.rows()[0]
    assert row["global_scope"] and row["spawnable"]
    assert catalog.detail("remote-acp:assistant") == row


def test_project_only_activation_still_infers_binding_for_roster(contribution):
    contexts = []

    def project_bindings(project):
        contexts.append(project)
        return {"assistant": binding()}

    catalog = RemoteAcpCatalog({}, project_bindings=project_bindings)
    row = catalog.rows()[0]
    assert row["spawnable"] and row["projects"] == ["demo"] and not row["global_scope"]
    assert contexts == ["demo"]
    for address in ("remote-acp:assistant", "https://example.com/acp"):
        with pytest.raises(ValueError):
            catalog.resolve(address)
        assert catalog.resolve(address, project="demo")[0] == "assistant"


@pytest.mark.parametrize("endpoint", [
    "https://example.com/other", "https://example.com/acp?query=1",
    "https://example.com/acp#fragment", "http://example.com/acp",
    "https://other.example.com/acp", "https://example.com/acp\n",
    "https://user@example.com/acp", "https://example.com\\other/acp",
])
def test_arbitrary_url_does_not_select_ambient_policy(endpoint):
    catalog = RemoteAcpCatalog({"one": configured()}, discover=RemoteContributionReport)
    with pytest.raises(ValueError):
        catalog.resolve(endpoint)


def test_url_ambiguity_is_refused_and_websocket_selection_is_explicit():
    catalog = RemoteAcpCatalog(
        {"one": configured(), "two": configured()}, discover=RemoteContributionReport,
    )
    with pytest.raises(ValueError, match="unique"):
        catalog.resolve("https://example.com/acp")
    del catalog.targets["two"]
    with pytest.raises(ValueError):
        catalog.resolve("wss://example.com/acp")
    catalog.targets["one"] = configured(transport="websocket")
    assert catalog.resolve("wss://example.com:443/acp")[0] == "one"
    assert canonical_endpoint("https://[::1]:443/acp") == "https://[::1]/acp"


def test_url_client_refuses_named_only_daemon_before_sending_any_request():
    from agent_bridge.client import BridgeClient, BridgeClientError
    from agent_bridge.protocol import REMOTE_ACP_PROTOCOL_VERSION

    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda version: version <= REMOTE_ACP_PROTOCOL_VERSION
    calls = []
    client._request = lambda *args, **kwargs: calls.append((args, kwargs)) or {}
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(agent="https://example.com/acp")
    assert refused.value.status == 426 and calls == []
    client.start_session(agent="remote-acp:configured")
    assert len(calls) == 1


def test_inactive_plugin_does_not_confer_binding_authority(contribution):
    _root, plugin, _activation = contribution
    catalog = RemoteAcpCatalog({}, bindings={"assistant": binding()})
    assert catalog.rows()[0]["spawnable"]
    report = discover_remote_contributions(ActivationReport(ScanAuthority.COMPLETE, {}))
    catalog._discover = lambda: report
    assert catalog.rows() == []
    with pytest.raises(ValueError):
        catalog.resolve("remote-acp:assistant", project="demo")
    # Uncertain activation retains the known row, but never its authority.
    catalog._discover = discover_remote_contributions
    assert catalog.rows()[0]["spawnable"]
    report = discover_remote_contributions(ActivationReport(
        ScanAuthority.INDETERMINATE, {plugin.source: EntryDecision.active(plugin)},
    ))
    catalog._discover = lambda: report
    row = catalog.rows()[0]
    assert not row["spawnable"] and row["unavailable_reason"] == "contribution-unavailable"


def test_duplicate_owners_and_malformed_requirements_refuse_only_affected_target(contribution):
    root, plugin, activation = contribution
    other = root.parent / "other"
    other.mkdir()
    (other / "plugin.json").write_text(json.dumps({
        "name": "other-harness", "bridgeConfig": "config.json",
    }))
    (other / "config.json").write_text(json.dumps({
        "remote_acp": {
            "assistant": {"transport": "websocket"},
            "healthy": {"transport": "streamable-http"},
            "invalid": {"transport": "streamable-http", "endpoint": "https://example.com/acp"},
        },
    }))
    foreign = ActivePlugin(
        source="other-harness@example", name="other-harness", marketplace="example",
        root=other, scopes=("global",),
    )
    report = discover_remote_contributions(ActivationReport(
        ScanAuthority.COMPLETE,
        {**activation.decisions, foreign.source: EntryDecision.active(foreign)},
    ))
    assert report.targets["assistant"].reason == "conflicting-contributions"
    assert report.targets["healthy"].requirements is not None
    assert report.targets["invalid"].reason == "invalid-requirements"
    assert plugin.source not in report.unavailable_owners


@pytest.mark.parametrize("path", ["../outside.json", "/outside.json", "C:\\outside.json"])
def test_payload_config_cannot_escape_verified_root(contribution, path):
    root, plugin, _activation = contribution
    (root / "plugin.json").write_text(json.dumps({"name": plugin.name, "bridgeConfig": path}))
    report = discover_remote_contributions()
    assert not report.targets
    assert plugin.source in report.unavailable_owners
    assert report.errors


@pytest.mark.parametrize("selected", ["config.yaml", "providers.d"])
def test_payload_reparse_components_are_refused_before_read(contribution, monkeypatch, selected):
    from pathlib import Path

    root, plugin, _activation = contribution
    original = Path.lstat
    refused = root / ".agent-bridge" / selected

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == refused:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    report = discover_remote_contributions()
    assert plugin.source in report.unavailable_owners
    assert report.errors


def test_project_binding_uses_existing_config_layers_then_service_override(contribution, monkeypatch, tmp_path):
    from agent_bridge import related_plugins

    project = tmp_path / "project"
    legacy = project / ".agent-bridge"
    canonical = project / ".copilot-extensions" / "agent-bridge"
    legacy.mkdir(parents=True)
    canonical.mkdir(parents=True)
    monkeypatch.setattr(related_plugins, "_registry_anchor", lambda name: project if name == "demo" else None)
    (legacy / "config.yaml").write_text(json.dumps({
        "remote_acp_bindings": {"assistant": binding(endpoint="https://legacy.example.com/acp").model_dump()},
    }))
    (canonical / "config.yaml").write_text(json.dumps({
        "remote_acp_bindings": {"assistant": binding(endpoint="https://canonical.example.com/acp").model_dump()},
    }))
    catalog = RemoteAcpCatalog({})
    assert catalog.resolve("remote-acp:assistant", project="demo")[1].endpoint == "https://canonical.example.com/acp"
    catalog.bindings["assistant"] = binding(endpoint="https://service.example.com/acp")
    assert catalog.resolve("remote-acp:assistant", project="demo")[1].endpoint == "https://service.example.com/acp"
    (canonical / "config.yaml").write_text("[invalid]")
    assert catalog.rows("demo")[0]["unavailable_reason"] == "project-binding-unavailable"
    with pytest.raises(ValueError):
        catalog.resolve("remote-acp:assistant", project="demo")


def test_fresh_resolve_does_not_publish_over_background_catalog_generation(contribution):
    catalog = RemoteAcpCatalog({})
    catalog.apply(catalog.discover())
    cached = catalog.contribution_report
    catalog.bindings["assistant"] = binding()
    assert catalog.resolve("remote-acp:assistant", project="demo")[0] == "assistant"
    assert catalog.contribution_report is cached
    catalog._discover = lambda: (_ for _ in ()).throw(AssertionError("static roster must not discover"))
    assert catalog.rows(refresh=False)[0]["name"] == "remote-acp:assistant"


@pytest.mark.parametrize("changes,reason", [
    ({"project": "other"}, "binding-project-mismatch"),
    ({"project": "other", "plugin": "foreign@example"}, "binding-owner-mismatch"),
])
def test_invalid_binding_cannot_move_activation_scope(contribution, changes, reason):
    catalog = RemoteAcpCatalog({}, bindings={"assistant": binding(**changes)})
    row = catalog.rows("demo")[0]
    assert row["projects"] == ["demo"] and not row["global_scope"]
    assert row["unavailable_reason"] == reason and not row["spawnable"]
    assert catalog.detail("remote-acp:assistant", "demo") == row
    assert catalog.rows("other") == []
    assert catalog.detail("remote-acp:assistant", "other") is None


@pytest.mark.parametrize("publish_unavailable", [False, True])
def test_fresh_detail_retains_known_unavailable_without_publishing(contribution, publish_unavailable):
    _root, plugin, _activation = contribution
    catalog = RemoteAcpCatalog({}, bindings={"assistant": binding()})
    assert catalog.rows("demo")[0]["spawnable"]
    report = discover_remote_contributions(ActivationReport(
        ScanAuthority.INDETERMINATE, {plugin.source: EntryDecision.active(plugin)},
    ))
    catalog._discover = lambda: report
    if publish_unavailable:
        catalog.apply(report)
    cached = catalog.contribution_report
    row = catalog.detail("remote-acp:assistant", "demo")
    assert row is not None
    assert row["unavailable_reason"] == "contribution-unavailable" and not row["spawnable"]
    assert catalog.detail("remote-acp:assistant", "other") is None
    assert catalog.contribution_report is cached
    assert report.targets == {}
    with pytest.raises(ValueError):
        catalog.resolve("remote-acp:assistant", project="demo")
    catalog._discover = RemoteContributionReport
    assert catalog.detail("remote-acp:assistant", "demo") is None
    assert catalog.contribution_report is cached
    assert catalog.rows("demo") == []


@pytest.mark.parametrize("declarations", [False, True])
def test_alternate_manifest_matches_activation_without_false_unavailability(contribution, declarations):
    root, plugin, _activation = contribution
    manifest = root / "plugin.json"
    alternate = root / ".claude-plugin"
    alternate.mkdir()
    data = json.loads(manifest.read_text()) if declarations else {"name": plugin.name}
    (alternate / "plugin.json").write_text(json.dumps(data))
    manifest.unlink()
    report = discover_remote_contributions()
    assert report.errors == [] and report.unavailable_owners == set()
    assert list(report.targets) == (["assistant"] if declarations else [])


def test_native_manifest_precedes_alternate_manifest(contribution):
    root, plugin, _activation = contribution
    alternate = root / ".claude-plugin"
    alternate.mkdir()
    (alternate / "plugin.json").write_text(json.dumps({
        "name": plugin.name, "bridgeConfig": "../outside.json",
    }))
    report = discover_remote_contributions()
    assert report.errors == [] and list(report.targets) == ["assistant"]


def test_alternate_manifest_reparse_parent_is_refused(contribution, monkeypatch):
    from pathlib import Path

    root, plugin, _activation = contribution
    alternate = root / ".claude-plugin"
    alternate.mkdir()
    (root / "plugin.json").rename(alternate / "plugin.json")
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == alternate:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    report = discover_remote_contributions()
    assert plugin.source in report.unavailable_owners
    assert report.errors and not report.targets


@pytest.mark.asyncio
async def test_malformed_remote_contribution_does_not_block_real_roster_startup(
    contribution, monkeypatch, tmp_path,
):
    from agent_bridge import agent_registry_discovery, app_agent_roster
    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.provider_sources import effective_config_dir, scan_provider_registry

    root, plugin, _activation = contribution
    catalog = RemoteAcpCatalog({"healthy": configured()})
    catalog.apply(catalog.discover())
    (root / ".agent-bridge" / "config.yaml").write_text("[invalid]")
    directory = effective_config_dir() / "providers.d"
    directory.mkdir()
    monkeypatch.setattr(
        agent_registry_discovery, "scan_provider_registry",
        lambda **kwargs: scan_provider_registry(
            directory, activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
            **kwargs,
        ),
    )
    resolver = AgentResolver({}, {})
    resolver.remote_acp_catalog = catalog
    healthy = SimpleNamespace(
        prefix="healthy",
        list=AsyncMock(return_value=[NamespaceAgentInfo(
            name="local", display_name="Local", description="", state="available",
        )]),
        invalidate_list_cache=lambda: None,
    )
    resolver.register_namespace_resolver(healthy)
    app = SimpleNamespace(state=SimpleNamespace())
    try:
        await asyncio.wait_for(app_agent_roster.start_agent_roster_cache(
            app, resolver, SimpleNamespace(agent_roster_cache_interval=60.0),
        ), timeout=2.0)
        cache = app.state.agent_roster_cache
        assert cache.discovery_ever_ok
        assert await cache.wait_until_warm(timeout=0.1)
        assert healthy.list.await_count >= 1
        rows = {row["name"]: row for row in catalog.rows("demo", refresh=False)}
        assert rows["remote-acp:healthy"]["spawnable"]
        assert rows["remote-acp:assistant"]["unavailable_reason"] == "contribution-unavailable"
        assert not rows["remote-acp:assistant"]["spawnable"]
        assert plugin.source in catalog.contribution_report.unavailable_owners
        assert catalog.errors == [f"{plugin.source}:contribution-unavailable"]
    finally:
        await app_agent_roster.stop_agent_roster_cache(app)


@pytest.mark.asyncio
async def test_normal_roster_detail_and_resolution_expose_same_bound_policy(contribution, tmp_db):
    manager = session_manager_from_config(
        tmp_db, ServiceConfig(remote_acp_bindings={"assistant": binding()}),
    )
    resolver = AgentResolver({}, {})
    manager.set_resolver(resolver)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_manager=manager, resolver=resolver, ready=True,
    )))
    rows = (await list_agents(request, project="demo"))["agents"]
    assert [row["name"] for row in rows] == ["remote-acp:assistant"]
    assert await get_agent("remote-acp:assistant", request, project="demo") == rows[0]
    assert (await list_agents(request, project="other"))["agents"] == []
    target = await resolver.resolve_async("https://example.com/acp", sender_repo="demo")
    assert target.type == "remote-acp" and target.worktree_id is None
    assert target.project is None


@pytest.mark.asyncio
async def test_unknown_url_route_refuses_before_local_resolution_or_spawn(tmp_db):
    manager = session_manager_from_config(tmp_db, ServiceConfig())
    resolver = SimpleNamespace(
        canonical_agent_name=lambda value: None,
        resolve_async=AsyncMock(side_effect=AssertionError("local resolution")),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_manager=manager, resolver=resolver, ready=True,
    )))
    with pytest.raises(Exception) as refused:
        await start_session(StartSessionRequest(agent="https://example.com/acp"), request)
    assert refused.value.status_code == 422
    resolver.resolve_async.assert_not_called()
    assert manager.list_sessions() == []


@pytest.mark.parametrize("scopes", [("global",), ("project:demo", "project:other")])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.asyncio
async def test_project_roster_composes_bindings_before_filtering(contribution, scopes, cached):
    from dataclasses import replace

    report = discover_remote_contributions()
    report.targets["assistant"] = replace(report.targets["assistant"], scopes=scopes)
    catalog = RemoteAcpCatalog(
        {}, discover=lambda: report,
        project_bindings=lambda project: {"assistant": binding()} if project == "demo" else {},
    )
    catalog.apply(report)
    resolver = AgentResolver({}, {})
    resolver.remote_acp_catalog = catalog
    stale_rows = resolver.list_agents()
    assert stale_rows[0]["state"] == "requires-binding"
    state = SimpleNamespace(resolver=resolver, topology_ready=True)
    cache = None
    if cached:
        from agent_bridge.agent_registry_cache import AgentRosterCache

        cache = state.agent_roster_cache = AgentRosterCache(resolver, refresh_interval=60)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    try:
        rows = (await list_agents(request, project="demo"))["agents"]
        assert rows[0]["state"] == "bound"
        assert await get_agent("remote-acp:assistant", request, project="demo") == rows[0]
        assert (await list_agents(request, project="other"))["agents"][0]["state"] == "requires-binding"
        assert stale_rows[0]["state"] == "requires-binding"
    finally:
        if cache is not None:
            await cache.stop()


def test_url_uniqueness_only_counts_policies_enabled_in_requested_project(contribution):
    from dataclasses import replace

    report = discover_remote_contributions()
    report.targets["foreign"] = RemoteContribution(
        "foreign", "foreign@example", ("project:other",),
        report.targets["assistant"].requirements,
    )
    catalog = RemoteAcpCatalog(
        {}, bindings={"assistant": binding(), "foreign": binding(plugin="foreign@example")},
        discover=lambda: report,
    )
    assert catalog.resolve("https://example.com/acp", project="demo")[0] == "assistant"
    assert catalog.resolve("https://example.com/acp", project="other")[0] == "foreign"
    with pytest.raises(ValueError):
        catalog.resolve("https://example.com/acp")
    report.targets["foreign"] = replace(report.targets["foreign"], scopes=("project:demo",))
    with pytest.raises(ValueError, match="unique"):
        catalog.resolve("https://example.com/acp", project="demo")
