"""Tests for declarative cold-store-provider discovery (cold-store-providers.d)."""

from __future__ import annotations

import json
import sys

import pytest
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from agent_bridge.agent_registry import AgentResolver
from agent_bridge.cold_store import ColdStoreClient
from agent_bridge.cold_store_sources import (
    ManifestError,
    cold_store_providers_dir,
    discover_cold_store_manifests,
    parse_manifest,
    scan_cold_store_registry,
)

# -- cold_store_providers_dir resolution ---------------------------------------


def test_cold_store_providers_dir_honors_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR", str(tmp_path / "cs"))
    assert cold_store_providers_dir() == tmp_path / "cs"


def test_cold_store_providers_dir_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR", raising=False)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert cold_store_providers_dir() == tmp_path / "cfg" / "cold-store-providers.d"


# -- parse_manifest --------------------------------------------------------------


def test_parse_manifest_valid():
    m = parse_manifest(
        {
            "capability": "session-fetch",
            "command": ["/abs/agent-logger"],
            "description": "agent-logger cold-store retrieval",
        },
        source_path="/x.json",
    )
    assert m.capability == "session-fetch"
    assert m.command == ("/abs/agent-logger",)
    assert m.description == "agent-logger cold-store retrieval"


def test_parse_manifest_v1_requires_attribution(tmp_path):
    m = parse_manifest(
        {
            "schema_version": 1,
            "plugin": "agent-logger@copilot-extensions",
            "plugin_root": str(tmp_path),
            "capability": "session-fetch",
            "command": [sys.executable],
        }
    )
    assert m.schema_version == 1
    assert m.plugin == "agent-logger@copilot-extensions"
    with pytest.raises(ManifestError, match="plugin"):
        parse_manifest(
            {
                "schema_version": 1,
                "plugin_root": str(tmp_path),
                "capability": "session-fetch",
                "command": [sys.executable],
            }
        )


@pytest.mark.parametrize(
    "data",
    [
        [],  # not an object
        {"command": ["x"]},  # missing capability
        {"capability": "", "command": ["x"]},  # empty capability
        {"capability": "session-fetch"},  # missing command
        {"capability": "session-fetch", "command": []},  # empty command
        {"capability": "session-fetch", "command": "x"},  # command not a list
        {"capability": "session-fetch", "command": [""]},  # empty element
        {"capability": "session-fetch", "command": [1]},  # non-string element
        {"capability": "session-fetch", "command": ["x"], "description": 5},
    ],
)
def test_parse_manifest_rejects_bad(data):
    with pytest.raises(ManifestError):
        parse_manifest(data, source_path="/x.json")


# -- discover_cold_store_manifests ------------------------------------------------


def _write(dir_, name, obj):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / name).write_text(json.dumps(obj), encoding="utf-8")


def _activation(root, *, source="agent-logger@copilot-extensions"):
    name, marketplace = source.split("@", 1)
    decision = EntryDecision.active(
        ActivePlugin(
            source=source,
            name=name,
            marketplace=marketplace,
            root=root.resolve(),
            scopes=("global",),
        )
    )
    return ActivationReport(authority=ScanAuthority.COMPLETE, decisions={source: decision})


def _write_v1_provider(directory, plugin_root, *, name="provider.json"):
    source = "agent-logger@copilot-extensions"
    _write(
        directory,
        name,
        {
            "schema_version": 1,
            "plugin": source,
            "plugin_root": str(plugin_root),
            "capability": "session-fetch",
            "command": [sys.executable],
        },
    )
    return source


def test_discover_missing_dir_returns_empty(tmp_path):
    assert discover_cold_store_manifests(tmp_path / "nope") == {}


def test_discover_reads_valid_and_skips_bad(tmp_path):
    _write(
        tmp_path,
        "logger.json",
        {"capability": "session-fetch", "command": [sys.executable]},
    )
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    _write(tmp_path, "invalid.json", {"capability": "x"})  # missing command

    found = discover_cold_store_manifests(tmp_path)

    assert set(found) == {"session-fetch"}
    assert found["session-fetch"].command == (sys.executable,)
    report = scan_cold_store_registry(tmp_path)
    assert {finding.reason for finding in report.findings} >= {
        "invalid-entry",
        "legacy-unattributed",
    }


def test_discover_dedups_capability_keeps_first(tmp_path):
    _write(tmp_path, "a.json", {"capability": "session-fetch", "command": [sys.executable, "a"]})
    _write(tmp_path, "b.json", {"capability": "session-fetch", "command": [sys.executable, "b"]})
    found = discover_cold_store_manifests(tmp_path)
    assert found["session-fetch"].command == (sys.executable, "a")
    report = scan_cold_store_registry(tmp_path)
    assert any(finding.reason == "duplicate" for finding in report.findings)


def test_discover_missing_command_is_inactive(tmp_path):
    missing = tmp_path / "gone"
    _write(
        tmp_path,
        "stale.json",
        {"capability": "session-fetch", "command": [str(missing)]},
    )
    report = scan_cold_store_registry(tmp_path)
    assert report.manifests == {}
    assert report.findings[0].reason == "missing-target"


def test_discover_rejects_foreign_installation_registry(monkeypatch, tmp_path):
    current = tmp_path / "cell-a"
    foreign = tmp_path / "cell-b"
    registry = foreign / "cold-store-providers.d"
    current.mkdir()
    registry.mkdir(parents=True)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(current))

    _write(
        registry,
        "logger.json",
        {"capability": "session-fetch", "command": [sys.executable]},
    )

    report = scan_cold_store_registry(registry)

    assert report.manifests == {}
    assert report.findings[0].reason == "bridge-install-mismatch"


def test_v1_provider_requires_current_exact_plugin_root(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    _write_v1_provider(tmp_path / "providers", plugin_root)

    active = scan_cold_store_registry(
        tmp_path / "providers",
        activation_report=_activation(plugin_root),
    )
    assert set(active.manifests) == {"session-fetch"}

    other_root = tmp_path / "other"
    other_root.mkdir()
    mismatch = scan_cold_store_registry(
        tmp_path / "providers",
        activation_report=_activation(other_root),
    )
    assert mismatch.manifests == {}
    assert mismatch.findings[0].reason == "identity-mismatch"


def test_disabled_v1_provider_withdraws_prior_entry(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    providers = tmp_path / "providers"
    _write_v1_provider(providers, plugin_root)
    first = scan_cold_store_registry(
        providers,
        activation_report=_activation(plugin_root),
    )
    disabled = ActivationReport(authority=ScanAuthority.COMPLETE, decisions={})
    second = scan_cold_store_registry(
        providers,
        previous=first.entries,
        activation_report=disabled,
    )
    assert second.manifests == {}
    assert second.findings[0].reason == "not-enabled"


# -- AgentResolver.cold_store (ColdStoreProviderRegistry) ------------------------


def _bridge_cold_store_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR", str(tmp_path))
    return tmp_path


def test_refresh_registers_from_manifest(monkeypatch, tmp_path):
    _bridge_cold_store_dir(monkeypatch, tmp_path)
    _write(tmp_path, "logger.json",
           {"capability": "session-fetch", "command": [sys.executable]})

    resolver = AgentResolver({}, {})
    resolver.cold_store.refresh(force=True)

    client = resolver.cold_store.get_client("session-fetch")
    assert isinstance(client, ColdStoreClient)
    assert client.capability == "session-fetch"


def test_get_client_returns_none_when_unregistered(monkeypatch, tmp_path):
    _bridge_cold_store_dir(monkeypatch, tmp_path)
    resolver = AgentResolver({}, {})
    assert resolver.cold_store.get_client("session-fetch") is None


def test_refresh_removes_deleted_provider(monkeypatch, tmp_path):
    _bridge_cold_store_dir(monkeypatch, tmp_path)
    _write(tmp_path, "logger.json",
           {"capability": "session-fetch", "command": [sys.executable]})
    resolver = AgentResolver({}, {})
    resolver.cold_store.refresh(force=True)
    assert resolver.cold_store.get_client("session-fetch") is not None

    (tmp_path / "logger.json").unlink()
    resolver.cold_store.refresh(force=True)
    assert resolver.cold_store.get_client("session-fetch") is None


def test_refresh_throttled_without_force(monkeypatch, tmp_path):
    _bridge_cold_store_dir(monkeypatch, tmp_path)
    resolver = AgentResolver({}, {})
    resolver.cold_store.refresh(force=True)

    _write(tmp_path, "logger.json",
           {"capability": "session-fetch", "command": [sys.executable]})
    # Without force, the throttle keeps the just-created manifest invisible.
    resolver.cold_store.refresh()
    assert resolver.cold_store.get_client("session-fetch") is None


# -- `agent-bridge doctor` includes cold-store-providers.d -----------------------


def test_doctor_reports_cold_store_registry(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from agent_bridge import __main__ as cli

    missing = tmp_path / "gone"
    _write(tmp_path, "stale.json",
           {"capability": "session-fetch", "command": [str(missing)]})
    monkeypatch.setenv("AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli._cmd_doctor(SimpleNamespace(json=True))
    assert exc.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["cold_store_registry"] == "cold-store-providers.d"
    assert payload["cold_store_findings"][0]["reason"] == "missing-target"


def test_doctor_ok_when_cold_store_registry_empty(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace

    from agent_bridge import __main__ as cli

    monkeypatch.setenv("AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR", str(tmp_path))
    cli._cmd_doctor(SimpleNamespace(json=True))
    payload = json.loads(capsys.readouterr().out)
    assert payload["cold_store_active"] == []
    assert payload["cold_store_findings"] == []

