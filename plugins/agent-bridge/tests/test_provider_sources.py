"""Tests for declarative namespace-provider discovery (providers.d registry)."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

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
    scan_provider_registry,
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


def test_parse_manifest_v1_requires_attribution(tmp_path):
    m = parse_manifest(
        {
            "schema_version": 1,
            "plugin": "agent-codespaces@copilot-extensions",
            "plugin_root": str(tmp_path),
            "namespace": "codespace",
            "command": [sys.executable],
        }
    )
    assert m.schema_version == 1
    assert m.plugin == "agent-codespaces@copilot-extensions"
    with pytest.raises(ManifestError, match="plugin"):
        parse_manifest(
            {
                "schema_version": 1,
                "plugin_root": str(tmp_path),
                "namespace": "codespace",
                "command": [sys.executable],
            }
        )


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
    _write(
        tmp_path,
        "codespaces.json",
        {"namespace": "codespace", "command": [sys.executable]},
    )
    _write(
        tmp_path,
        "containers.json",
        {"namespace": "container", "command": [sys.executable]},
    )
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    _write(tmp_path, "invalid.json", {"namespace": "x"})  # missing command

    found = discover_provider_manifests(tmp_path)

    assert set(found) == {"codespace", "container"}
    assert found["codespace"].command == (sys.executable,)
    report = scan_provider_registry(tmp_path)
    assert {finding.reason for finding in report.findings} >= {
        "invalid-entry",
        "legacy-unattributed",
    }


def test_discover_dedups_namespace_keeps_first(tmp_path):
    # "a.json" sorts before "b.json"; both claim "codespace".
    _write(tmp_path, "a.json", {"namespace": "codespace", "command": [sys.executable, "a"]})
    _write(tmp_path, "b.json", {"namespace": "codespace", "command": [sys.executable, "b"]})
    found = discover_provider_manifests(tmp_path)
    assert found["codespace"].command == (sys.executable, "a")
    report = scan_provider_registry(tmp_path)
    assert any(finding.reason == "duplicate" for finding in report.findings)


def test_discover_missing_command_is_inactive(tmp_path):
    missing = tmp_path / "gone"
    _write(
        tmp_path,
        "stale.json",
        {"namespace": "stale", "command": [str(missing)]},
    )
    report = scan_provider_registry(tmp_path)
    assert report.manifests == {}
    assert report.findings[0].reason == "missing-target"


def test_transient_command_access_failure_retains_prior_provider(
    monkeypatch, tmp_path
):
    from agent_bridge import provider_sources

    manifest = tmp_path / "provider.json"
    _write(
        tmp_path,
        manifest.name,
        {"namespace": "stable", "command": [sys.executable]},
    )
    first = scan_provider_registry(tmp_path)
    assert "stable" in first.manifests

    def deny(_command):
        raise PermissionError("temporarily denied")

    monkeypatch.setattr(provider_sources, "_resolve_command", deny)
    second = scan_provider_registry(tmp_path, previous=first.entries)
    assert second.manifests["stable"] == first.manifests["stable"]
    assert any(finding.reason == "entry-indeterminate" for finding in second.findings)


def test_doctor_json_reports_exact_stale_entry(monkeypatch, tmp_path, capsys):
    from agent_bridge import __main__ as cli

    stale = tmp_path / "stale.json"
    missing = tmp_path / "gone"
    _write(
        tmp_path,
        stale.name,
        {"namespace": "stale", "command": [str(missing)]},
    )
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli._cmd_doctor(SimpleNamespace(json=True))
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["authority"] == "complete"
    assert payload["findings"][0]["entry"] == str(stale)
    assert payload["findings"][0]["target"] == str(missing)
    assert payload["findings"][0]["reason"] == "missing-target"


def test_doctor_parser_supports_plain_and_both_json_positions():
    from agent_bridge import __main__ as cli

    assert cli.build_parser().parse_args(["doctor"]).json is False
    assert cli.build_parser().parse_args(["doctor", "--json"]).json is True
    assert cli.build_parser().parse_args(["--json", "doctor"]).json is True


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
async def test_cli_resolver_missing_command_no_fallback_list_empty():
    # list() degrades to [] when the provider command is unavailable and there
    # is no in-process fallback -- a missing provider contributes no dynamic
    # agents (the codespace: case inside the elevated sub-daemon). It must NOT
    # raise, so agent enumeration stays clean.
    resolver = CliNamespaceResolver(
        "codespace", "agent-codespaces",
        command=[str("this-binary-does-not-exist-xyz")],
    )
    assert await resolver.list() == []
    # resolve stays strict -- you cannot spawn what you cannot resolve.
    with pytest.raises(RuntimeError):
        await resolver.resolve("cs-1")


# -- AgentResolver.refresh_provider_resolvers ----------------------------------


def _bridge_providers_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path))
    return tmp_path


def test_refresh_registers_from_manifest(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": [sys.executable]})
    _write(tmp_path, "containers.json",
           {"namespace": "container", "command": [sys.executable], "restricted": True})

    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)

    resolvers = resolver.namespace_resolvers
    assert set(resolvers) == {"codespace", "container"}
    assert isinstance(resolvers["codespace"], CliNamespaceResolver)
    assert isinstance(resolvers["container"], RestrictedCliNamespaceResolver)


def test_refresh_is_additive_and_idempotent(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": [sys.executable]})

    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)
    first = resolver.namespace_resolvers["codespace"]

    # A second manifest appears; a forced refresh adds it without replacing the
    # already-registered one.
    _write(tmp_path, "containers.json",
           {"namespace": "container", "command": [sys.executable]})
    resolver.refresh_provider_resolvers(force=True)

    assert resolver.namespace_resolvers["codespace"] is first
    assert "container" in resolver.namespace_resolvers


def test_refresh_removes_deleted_provider(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    manifest = tmp_path / "codespaces.json"
    _write(
        tmp_path,
        manifest.name,
        {"namespace": "codespace", "command": [sys.executable]},
    )
    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)
    assert "codespace" in resolver.namespace_resolvers

    manifest.unlink()
    resolver.refresh_provider_resolvers(force=True)
    assert "codespace" not in resolver.namespace_resolvers


def test_refresh_replaces_changed_provider(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    _write(
        tmp_path,
        "codespaces.json",
        {"namespace": "codespace", "command": [sys.executable, "one"]},
    )
    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)
    first = resolver.namespace_resolvers["codespace"]

    _write(
        tmp_path,
        "codespaces.json",
        {"namespace": "codespace", "command": [sys.executable, "two"]},
    )
    resolver.refresh_provider_resolvers(force=True)
    assert resolver.namespace_resolvers["codespace"] is not first


def test_refresh_throttled_without_force(monkeypatch, tmp_path):
    _bridge_providers_dir(monkeypatch, tmp_path)
    resolver = AgentResolver({}, {})
    resolver.refresh_provider_resolvers(force=True)  # sets scan timestamp

    # Drop a manifest AFTER the throttle window opened; a non-forced refresh
    # within the TTL must not pick it up yet.
    _write(tmp_path, "codespaces.json",
           {"namespace": "codespace", "command": [sys.executable]})
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
           {"namespace": "codespace", "command": [sys.executable]})

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
