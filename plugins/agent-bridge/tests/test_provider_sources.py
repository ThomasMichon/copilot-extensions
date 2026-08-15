"""Tests for declarative namespace-provider discovery (providers.d registry)."""

from __future__ import annotations

import json
import sys

import pytest

from agent_bridge.agent_registry import (
    AgentResolver,
    CliNamespaceResolver,
    RestrictedCliNamespaceResolver,
)
from agent_bridge.provider_sources import (
    ManifestError,
    discover_provider_manifests,
    parse_manifest,
    providers_dir,
)


# -- providers_dir resolution --------------------------------------------------


def test_providers_dir_honors_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path / "pd"))
    assert providers_dir() == tmp_path / "pd"


def test_providers_dir_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert providers_dir() == tmp_path / "cfg" / "providers.d"


# -- parse_manifest ------------------------------------------------------------


def test_parse_manifest_valid():
    m = parse_manifest(
        {
            "namespace": "codespace:",
            "command": ["/abs/agent-codespaces"],
            "restricted": True,
            "description": "GitHub Codespaces",
        },
        source_path="/x.json",
    )
    assert m.namespace == "codespace"  # trailing ':' stripped
    assert m.command == ("/abs/agent-codespaces",)
    assert m.restricted is True
    assert m.description == "GitHub Codespaces"


@pytest.mark.parametrize(
    "data",
    [
        [],  # not an object
        {"command": ["x"]},  # missing namespace
        {"namespace": "", "command": ["x"]},  # empty namespace
        {"namespace": "cs"},  # missing command
        {"namespace": "cs", "command": []},  # empty command
        {"namespace": "cs", "command": "x"},  # command not a list
        {"namespace": "cs", "command": [""]},  # empty element
        {"namespace": "cs", "command": [1]},  # non-string element
        {"namespace": "cs", "command": ["x"], "description": 5},  # bad desc
        {"namespace": "cs", "command": ["x"], "restricted": "false"},  # str, not bool
        {"namespace": "cs", "command": ["x"], "restricted": 1},  # int, not bool
    ],
)
def test_parse_manifest_rejects_bad(data):
    with pytest.raises(ManifestError):
        parse_manifest(data, source_path="/x.json")


# -- discover_provider_manifests -----------------------------------------------


def _write(dir_, name, obj):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(json.dumps(obj), encoding="utf-8")


def test_discover_missing_dir_returns_empty(tmp_path):
    assert discover_provider_manifests(tmp_path / "nope") == {}


def test_discover_reads_valid_and_skips_bad(tmp_path):
    _write(tmp_path, "codespaces.json", {"namespace": "codespace", "command": ["cs"]})
    _write(tmp_path, "containers.json", {"namespace": "container", "command": ["ct"]})
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    _write(tmp_path, "invalid.json", {"namespace": "x"})  # missing command

    found = discover_provider_manifests(tmp_path)

    assert set(found) == {"codespace", "container"}
    assert found["codespace"].command == ("cs",)


def test_discover_dedups_namespace_keeps_first(tmp_path):
    # "a.json" sorts before "b.json"; both claim "codespace".
    _write(tmp_path, "a.json", {"namespace": "codespace", "command": ["first"]})
    _write(tmp_path, "b.json", {"namespace": "codespace", "command": ["second"]})
    found = discover_provider_manifests(tmp_path)
    assert found["codespace"].command == ("first",)


# -- CliNamespaceResolver explicit-command override ----------------------------


def _fake_list_command(payload: list[dict]) -> list[str]:
    """An argv that prints ``payload`` as JSON regardless of appended argv."""
    return [sys.executable, "-c", f"import sys; sys.stdout.write({json.dumps(json.dumps(payload))})"]


@pytest.mark.asyncio
async def test_cli_resolver_uses_explicit_command(tmp_path):
    payload = [
        {"name": "cs-1", "display_name": "cs one", "state": "available",
         "aliases": ["one"]},
    ]
    resolver = CliNamespaceResolver(
        "codespace", "agent-codespaces", command=_fake_list_command(payload),
    )
    infos = await resolver.list()
    assert [i.name for i in infos] == ["cs-1"]
    assert infos[0].aliases == ["one"]


@pytest.mark.asyncio
async def test_cli_resolver_missing_command_no_fallback_raises():
    resolver = CliNamespaceResolver(
        "codespace", "agent-codespaces",
        command=[str("this-binary-does-not-exist-xyz")],
    )
    with pytest.raises(RuntimeError):
        await resolver.list()


# -- AgentResolver.refresh_provider_resolvers ----------------------------------


def _bridge_providers_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path))
    return tmp_path


def test_refresh_registers_from_manifest(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": ["/abs/cs"]})
    _write(tmp_path, "containers.json",
           {"namespace": "container", "command": ["/abs/ct"], "restricted": True})

    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)

    resolvers = resolver.namespace_resolvers
    assert set(resolvers) == {"codespace", "container"}
    assert isinstance(resolvers["codespace"], CliNamespaceResolver)
    assert isinstance(resolvers["container"], RestrictedCliNamespaceResolver)


def test_refresh_is_additive_and_idempotent(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": ["/abs/cs"]})

    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)
    first = resolver.namespace_resolvers["codespace"]

    # A second manifest appears; a forced refresh adds it without replacing the
    # already-registered one.
    _write(tmp_path, "containers.json",
           {"namespace": "container", "command": ["/abs/ct"]})
    resolver.refresh_provider_resolvers(force=True)

    assert resolver.namespace_resolvers["codespace"] is first
    assert "container" in resolver.namespace_resolvers


def test_refresh_throttled_without_force(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)  # sets scan timestamp

    # Drop a manifest AFTER the throttle window opened; a non-forced refresh
    # within the TTL must not pick it up yet.
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": ["/abs/cs"]})
    resolver.refresh_provider_resolvers(force=False)
    assert "codespace" not in resolver.namespace_resolvers

    # Forcing re-scans immediately.
    resolver.refresh_provider_resolvers(force=True)
    assert "codespace" in resolver.namespace_resolvers


def test_refresh_missing_dir_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path / "absent"))
    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)
    assert resolver.namespace_resolvers == {}


# -- daemon_resolver: golden path (no topology) --------------------------------


def test_daemon_resolver_registers_providers_without_topology(monkeypatch, tmp_path):
    # The golden path: a box with NO machines.yaml/topology. build_resolver
    # returns None, but the daemon must still stand up a resolver so a
    # providers.d manifest can register the codespace: namespace resolver.
    from agent_bridge import agent_registry

    monkeypatch.setattr(agent_registry, "build_resolver", lambda cfg: None)
    _bridge_providers_dir(monkeypatch, tmp_path)
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": ["/abs/cs"]})

    resolver = agent_registry.daemon_resolver(cfg=None)

    assert resolver is not None
    assert "codespace" in resolver.namespace_resolvers
    # The built-in admin: modifier is registered alongside declarative providers.
    assert "admin" in resolver.namespace_resolvers


def test_daemon_resolver_uses_topology_when_present(monkeypatch, tmp_path):
    from agent_bridge import agent_registry

    sentinel = AgentResolver({}, {})
    monkeypatch.setattr(agent_registry, "build_resolver", lambda cfg: sentinel)
    resolver = agent_registry.daemon_resolver(cfg=None)
    assert resolver is sentinel

