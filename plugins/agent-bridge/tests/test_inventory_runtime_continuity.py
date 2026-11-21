"""Inventory-only repositories survive ordinary startup and private snapshots."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
import pytest
from zdd.private_protocol import PrivateIPCError

from agent_bridge import agent_registry, target_inventory, topology
from agent_bridge.agent_registry_common import AgentConfig
from agent_bridge.agent_registry_resolver import AgentResolver
from agent_bridge.app_worktree_cache import start_worktree_cache
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.local_session_catalog import REPOSITORY_INVENTORY_UNAVAILABLE
from agent_bridge.models import ServiceConfig
from agent_bridge.resolver_runtime import capture_resolver, restore_resolver
from agent_bridge.routes import worktrees
from agent_bridge.routes import worktree_inventory
from agent_bridge.routes.worktree_entry import WorktreeEntry
from agent_bridge.routes.worktree_refresh import DiscoveryReadError, WorktreeRefreshOwner
from agent_bridge.session_host import registered_inventory
from agent_bridge.worktree_refresh_runtime import FrozenRefresh, capture_refresh
from agent_bridge.worktree_runtime import capture_worktrees, restore_worktrees


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_available", [True, False])
async def test_inventory_crawl_uses_the_attributable_owner_not_a_project_path_command(
    monkeypatch: pytest.MonkeyPatch, owner_available: bool,
) -> None:
    from agent_bridge import session_lifecycle_cli

    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    monkeypatch.setattr(worktree_inventory, "os", SimpleNamespace(name="nt"))
    key = "inventory:source"
    config = AgentConfig(key, project="source")
    resolver = AgentResolver(
        {}, {}, resolved_local_identity=(None, "linux"), inventory_sources={key: config},
    )
    calls: list[Any] = []

    def prefix(**kwargs: Any) -> list[str] | None:
        assert kwargs == {"allow_path_fallback": False}
        return ["selected-owner"] if owner_available else None

    async def spawn(*args: str, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        async def communicate() -> tuple[bytes, bytes]:
            return b'[{"id":"workspace","path":"/source"}]', b""
        return SimpleNamespace(communicate=communicate, returncode=0), SimpleNamespace(
            close=lambda: calls.append("closed"),
        )

    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", prefix)
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_environment", lambda: {"OWNER": "selected"})
    monkeypatch.setattr(worktree_inventory, "spawn_in_kill_on_close_job", spawn)
    monkeypatch.setattr(worktrees, "_resolve_local_binstub", lambda project: pytest.fail("project PATH lookup"))
    cache = worktrees.WorktreeDiscoveryCache()
    if not owner_available:
        with pytest.raises(DiscoveryReadError, match="source-read-failed"):
            await cache._crawl_agent(key, config, resolver)
        assert calls == []
        return
    (row,) = await cache._crawl_agent(key, config, resolver)
    assert row.id == "workspace"
    assert calls[0][0] == ("selected-owner", "--project", "source", "list", "--json", "--passive")
    assert calls[0][1]["env"] == {"OWNER": "selected"}
    assert calls[0][1]["require_job"] is True and calls[-1] == "closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_passive_inventory_timeout_and_cancellation_settle_the_owned_child(
    monkeypatch: pytest.MonkeyPatch, cancelled: bool,
) -> None:
    from agent_bridge import session_lifecycle_cli

    monkeypatch.setattr(worktree_inventory, "os", SimpleNamespace(name="nt"))
    calls: list[Any] = []
    async def communicate() -> tuple[bytes, bytes]:
        if calls:
            return b"", b""
        raise asyncio.CancelledError() if cancelled else asyncio.TimeoutError()
    proc = SimpleNamespace(communicate=communicate)
    async def spawn(*args: str, **kwargs: Any) -> Any:
        return proc, SimpleNamespace(close=lambda: calls.append("job-closed"))

    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda **kwargs: ["owner"])
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_environment", lambda: {})
    monkeypatch.setattr(worktree_inventory, "spawn_in_kill_on_close_job", spawn)
    monkeypatch.setattr(worktrees, "_kill_process_tree", lambda owned: calls.append(owned))
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await worktree_inventory.read("source", ["list", "--json", "--passive"], timeout=1)
    else:
        assert await worktree_inventory.read("source", ["list"], timeout=1) == (
            None, "worktree_owner_timeout",
        )
    assert calls == ["job-closed", proc]


@pytest.mark.asyncio
async def test_passive_inventory_selected_owner_runs_with_a_real_captured_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import session_lifecycle_cli

    program = (
        "import json,os,sys;"
        "print(json.dumps({'argv':sys.argv[1:],'owner':os.environ.get('INVENTORY_OWNER'),"
        "'foreign':os.environ.get('AGENT_RT_ROOT')}))"
    )
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_launch_prefix",
        lambda **kwargs: [sys.executable, "-I", "-c", program],
    )
    monkeypatch.setenv("INVENTORY_OWNER", "selected")
    monkeypatch.setenv("AGENT_RT_ROOT", "foreign-runtime")
    raw, stderr = await worktree_inventory.read(
        "source-only", ["list", "--json", "--passive"], timeout=10,
    )
    assert raw is not None and stderr == ""
    assert json.loads(raw) == {
        "argv": ["--project", "source-only", "list", "--json", "--passive"],
        "owner": "selected", "foreign": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("inventory_error", [False, True])
async def test_inventory_only_startup_snapshot_and_forwarded_catalog_preserve_rows_without_agent_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_error: bool,
) -> None:
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    key = "inventory:example"
    resolver = AgentResolver(
        {}, {}, resolved_local_identity=(None, "linux"),
        inventory_sources={key: AgentConfig(key, project="example", cwd=str(tmp_path))},
        repository_inventory_unavailable=inventory_error,
    )
    app = FastAPI()
    cache = worktrees.WorktreeDiscoveryCache()
    app.state.worktree_discovery_cache = cache
    governance = LoopGovernance(helper=lambda checkpoint: {"status": "ready"})
    start_worktree_cache(app, resolver, ServiceConfig(), governance)
    assert cache._resolver is resolver and key in cache._refresh.sources
    cache._cache = {key: [
        WorktreeEntry("workspace", key, "local", str(tmp_path), "dev", "active"),
    ]}
    expiry = time.monotonic() + 30
    cache._refresh.published(
        cache._refresh.epoch, {key: None},
        observed={key: time.monotonic()}, valid_until={key: expiry},
    )
    resolver_payload = capture_resolver(resolver)
    payload: Any = capture_worktrees(cache, resolver, governance)
    restored = restore_resolver(resolver_payload)
    frozen = restore_worktrees(payload, restored)
    assert capture_resolver(restored) == resolver_payload
    assert frozen.private_payload() == payload
    assert frozen._refresh.sources[key].valid_until == expiry
    assert restored.agents == {} and restored.canonical_agent_name(key) is None
    app.state.resolver = restored
    app.state.worktree_discovery_cache = frozen
    app.state.session_manager = SimpleNamespace(
        list_sessions=lambda: [], _host_index=None,
        db=SimpleNamespace(list_live_sessions=lambda **kwargs: []),
    )
    monkeypatch.setattr(registered_inventory, "registered_hosts", lambda: (
        [], {"availability": "available"},
    ))
    result = await target_inventory.inventory(SimpleNamespace(app=app))
    (workspace,) = result["targets"]
    assert workspace["kind"] == "worktree" and workspace["handle"] == "workspace"
    assert workspace["owner"] is None and workspace["project"] == "example"
    evidence = result["sources"]["worktrees"]
    assert evidence["completeness"] == ("incomplete" if inventory_error else "complete")
    for render in (False, True):
        response = await worktrees.list_worktrees(
            Request({"type": "http", "app": app}), render=render, refresh=False,
        )
        assert response["discovery"]["completeness"] == evidence["completeness"]
        if inventory_error:
            assert response["discovery"]["repository_inventory"] == {
                "availability": "unavailable", "reason": REPOSITORY_INVENTORY_UNAVAILABLE,
            }
    await cache.stop()


def test_legacy_empty_resolver_and_populated_refresh_snapshots_keep_their_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    resolver = AgentResolver({}, {}, resolved_local_identity=(None, "linux"))
    payload = capture_resolver(resolver)
    assert "inventory_sources" not in payload
    assert "repository_inventory_unavailable" not in payload
    restored = restore_resolver(payload)
    assert restored.inventory_sources == {}
    assert capture_resolver(restored) == payload
    with pytest.raises(PrivateIPCError):
        restore_resolver({**payload, "inventory_sources": {}})
    with pytest.raises(PrivateIPCError):
        restore_resolver({**payload, "repository_inventory_unavailable": False})
    owner = WorktreeRefreshOwner()
    owner.bind(("fixture",), ["source"], available=True)
    owner.published(owner.epoch, {"source": None})
    refresh = capture_refresh(owner)
    assert set(refresh["sources"]["source"]) == {"generation", "updated", "diagnostic"}
    frozen = FrozenRefresh(refresh)
    assert capture_refresh(frozen) == refresh
    frozen.verify()


def test_machine_keyed_local_agent_covers_its_project_in_the_actual_builder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    machine = topology.MachineConfig(
        "local", "Local", execution_platform="linux",
        ssh_environments=[topology.SshEnvironment("linux", "local-transport")],
    )
    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({
        "worker": {"host": "local", "project": "example", "ssh_environment": "linux"},
    }), encoding="utf-8")
    monkeypatch.setattr(topology, "load_machines_yaml", lambda *args, **kwargs: {"local": machine})
    monkeypatch.setattr(topology, "load_control_plane_project", lambda path: None)
    monkeypatch.setattr(agent_registry, "_detect_local_machine", lambda *args: (machine, "linux"))
    monkeypatch.setattr(agent_registry, "_effective_spawn_defaults", lambda *args: ([], {}))
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: [{
        "name": "example", "class": "worktree", "agent": False,
        "paths": {"linux": str(tmp_path)},
    }])
    profile = SimpleNamespace(
        machines_yaml=str(tmp_path / "machines.yaml"), agents_config=str(agents_file),
    )
    result = agent_registry.build_resolver(SimpleNamespace(topologies={"example": profile}))
    assert result is not None
    assert result._is_local_loopback_agent(result.agents["worker"])
    assert result.inventory_sources == {}


def test_snapshot_refuses_an_inventory_name_that_is_an_applied_agent_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    key = "inventory:example"
    resolver = AgentResolver(
        {"reader": AgentConfig("reader", project="other", aliases=[key])},
        {}, resolved_local_identity=(None, "linux"),
        inventory_sources={key: AgentConfig(key, project="example")},
    )
    with pytest.raises(PrivateIPCError):
        capture_resolver(resolver)


@pytest.fixture
def startup_owner(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from agent_bridge import session_lifecycle_cli

    calls: list[Any] = []
    def prefix(**kwargs: Any) -> list[str]:
        assert kwargs == {"allow_path_fallback": False}
        return ["selected-owner"]

    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", prefix)
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_environment", lambda: {"OWNER": "selected"})
    monkeypatch.setattr(agent_registry, "_detect_platform", lambda: "linux")
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda: {})
    monkeypatch.setattr(agent_registry, "_register_namespace_resolvers", lambda resolver: None)
    monkeypatch.setattr(
        agent_registry, "load_local_repos", lambda: pytest.fail("legacy provisioning shim"),
    )
    return calls


@pytest.mark.parametrize("with_topology", [False, True])
def test_daemon_startup_uses_one_strict_selected_registry_read(
    startup_owner: list[Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    with_topology: bool,
) -> None:
    from agent_bridge import provider_process

    repos = [
        {"name": name, "class": kind, "agent": False, "paths": {"linux": str(tmp_path)}}
        for name, kind in (("source", "worktree"), ("notes", "knowledge"))
    ]
    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        startup_owner.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"inventory_authority": "complete", "repos": repos}), "",
        )

    monkeypatch.setattr(provider_process, "run_captured", run)
    cfg: Any = ServiceConfig()
    if with_topology:
        monkeypatch.setattr(topology, "load_machines_yaml", lambda *args, **kwargs: {})
        monkeypatch.setattr(topology, "load_control_plane_project", lambda path: None)
        monkeypatch.setattr(agent_registry, "_effective_spawn_defaults", lambda *args: ([], {}))
        cfg = SimpleNamespace(
            topologies={"example": SimpleNamespace(
                machines_yaml=str(tmp_path / "machines.yaml"), agents_config=None,
            )},
            remote_acp=cfg.remote_acp, remote_acp_bindings=cfg.remote_acp_bindings,
            remote_acp_auth_sources=cfg.remote_acp_auth_sources,
        )
    resolver = agent_registry.daemon_resolver(cfg)
    assert startup_owner == [(
        ["selected-owner", "repos", "list", "--json", "--strict"],
        {"timeout": 15.0, "env": {"OWNER": "selected"}},
    )]
    assert {row.project for row in resolver.inventory_sources.values()} == {"source", "notes"}
    assert resolver.agents == {} and resolver._topology_errors == []


@pytest.mark.parametrize("failure", [
    "owner-absent", "timeout", "nonzero", "partial", "malformed-json", "malformed-repos",
    "malformed-row", "launcher", "empty",
])
def test_daemon_registry_failure_is_not_an_empty_complete_inventory(
    startup_owner: list[Any], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    from agent_bridge import provider_process, session_lifecycle_cli

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        startup_owner.append(argv)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 15)
        if failure == "launcher":
            raise RuntimeError("Windows descendant Job assignment failed before child resume")
        document: Any = {"inventory_authority": "complete", "repos": []}
        if failure == "partial":
            document["inventory_authority"] = "partial"
        elif failure == "malformed-repos":
            document["repos"] = {}
        elif failure == "malformed-row":
            document["repos"] = [None]
        return subprocess.CompletedProcess(
            argv, 1 if failure == "nonzero" else 0,
            "{" if failure == "malformed-json" else json.dumps(document), "",
        )

    monkeypatch.setattr(provider_process, "run_captured", run)
    if failure == "owner-absent":
        monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda **kwargs: None)
    resolver = agent_registry.daemon_resolver(ServiceConfig())
    assert len(startup_owner) == (0 if failure == "owner-absent" else 1)
    assert resolver.agents == {} and resolver.inventory_sources == {}
    assert resolver.topology_errors == []
    assert resolver.repository_inventory_unavailable is (failure != "empty")
    restored = restore_resolver(capture_resolver(resolver))
    assert restored.repository_inventory_unavailable is resolver.repository_inventory_unavailable


def test_strict_repository_reader_uses_a_real_captured_selected_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import local_session_catalog, session_lifecycle_cli

    program = (
        "import json,os,sys;"
        "assert sys.argv[1:]==['repos','list','--json','--strict'];"
        "assert os.environ.get('AGENT_RT_ROOT') is None;"
        "print(json.dumps({'inventory_authority':'complete','repos':[]}))"
    )
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_launch_prefix",
        lambda **kwargs: [sys.executable, "-I", "-c", program],
    )
    monkeypatch.setenv("AGENT_RT_ROOT", "foreign-runtime")
    assert local_session_catalog.read_repository_inventory() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("subscribe", [False, True])
async def test_unavailable_registry_does_not_break_healthy_agent_stream_or_initial_subscription(
    startup_owner: list[Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subscribe: bool,
) -> None:
    from agent_bridge import inventory_cli, session_lifecycle_cli
    from agent_bridge.routes import agents

    agents_file = tmp_path / "agents.json"
    agents_file.write_text(json.dumps({"worker": {"project": "source", "cwd": str(tmp_path)}}))
    defaults = ServiceConfig()
    cfg = SimpleNamespace(
        topologies={"configured": SimpleNamespace(machines_yaml=None, agents_config=str(agents_file))},
        remote_acp=defaults.remote_acp, remote_acp_bindings=defaults.remote_acp_bindings,
        remote_acp_auth_sources=defaults.remote_acp_auth_sources,
    )
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda **kwargs: None)
    resolver = restore_resolver(capture_resolver(agent_registry.daemon_resolver(cfg)))
    assert resolver.repository_inventory_unavailable
    app = FastAPI()
    app.state.resolver = resolver
    result = await agents.list_agents(Request({"type": "http", "app": app}))
    assert [row["name"] for row in result["agents"]] == ["worker"]
    calls: list[dict[str, Any]] = []

    def fetch(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return result["agents"], result["topology_errors"], result["incomplete_namespaces"], True

    monkeypatch.setattr(inventory_cli, "_core", lambda: SimpleNamespace(
        _get_client=lambda: SimpleNamespace(list_agents_with_incomplete=fetch),
        _PROJECT_OVERRIDE=None,
    ))
    def finish_subscription(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(inventory_cli, "time", SimpleNamespace(sleep=finish_subscription))
    output = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", output)
    args = argparse.Namespace(all_projects=True, json=True, subscribe=subscribe, interval=1)
    assert inventory_cli._run_agents_stream(args) == 0
    frames = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [frame["type"] for frame in frames] == ["begin", "row", "done"]
    assert frames[1]["entry"]["name"] == "worker"
    assert calls == [{"force_refresh": False, "require_complete": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_available", [False, True])
async def test_empty_daemon_inventory_is_qualified_through_public_listings_and_snapshot(
    startup_owner: list[Any], monkeypatch: pytest.MonkeyPatch, owner_available: bool,
) -> None:
    from agent_bridge import provider_process, session_lifecycle_cli

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        startup_owner.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, '{"inventory_authority":"complete","repos":[]}', "",
        )

    monkeypatch.setattr(provider_process, "run_captured", run)
    if not owner_available:
        monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda **kwargs: None)
    cfg = ServiceConfig(worktree_discovery_interval=0)
    resolver = agent_registry.daemon_resolver(cfg)
    assert not (resolver.agents or resolver.machines or resolver.inventory_sources)
    app = FastAPI()
    app.state.resolver = resolver
    app.state.session_manager = SimpleNamespace(
        list_sessions=lambda: [], _host_index=None,
        db=SimpleNamespace(list_live_sessions=lambda **kwargs: []),
    )
    monkeypatch.setattr(registered_inventory, "registered_hosts", lambda: (
        [], {"availability": "available"},
    ))
    cache = worktrees.WorktreeDiscoveryCache()
    app.state.worktree_discovery_cache = cache
    governance = LoopGovernance(helper=lambda checkpoint: {"status": "ready"})
    start_worktree_cache(app, resolver, cfg, governance)
    request = Request({"type": "http", "app": app})
    result = await worktrees.list_worktrees(request, render=False, refresh=False)
    catalog = await target_inventory.inventory(request)
    assert result["groups"] == {} and catalog["targets"] == []
    for evidence in (result["discovery"], catalog["sources"]["worktrees"]):
        assert evidence["completeness"] == ("complete" if owner_available else "incomplete")
        if owner_available:
            assert evidence["freshness"] == "fresh" and "repository_inventory" not in evidence
        else:
            assert evidence["repository_inventory"]["availability"] == "unavailable"
    if owner_available:
        await asyncio.gather(*tuple(cache._backfill_tasks))
        restored = restore_resolver(capture_resolver(resolver))
        frozen = restore_worktrees(capture_worktrees(cache, resolver, governance), restored)
        app.state.resolver = restored
        app.state.worktree_discovery_cache = frozen
        forwarded = await target_inventory.inventory(request)
        assert forwarded["targets"] == []
        assert forwarded["sources"]["worktrees"]["completeness"] == "complete"
        assert forwarded["sources"]["worktrees"]["freshness"] == "fresh"
    await cache.stop()
