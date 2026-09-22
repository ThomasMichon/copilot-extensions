"""Direct coverage for the claim-provider drop-in registry."""

from __future__ import annotations

import json
import os

import pytest
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin

from agent_worktrees import claim_providers as cp


def _active_report(source: str, root):
    active = ActivePlugin(
        source=source,
        name=source.split("@", 1)[0],
        marketplace=source.split("@", 1)[1],
        root=root.resolve(),
        scopes=("global",),
    )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={source: EntryDecision.active(active)},
    )


def _write_manifest(plugin_root, namespace: str, **fields):
    path = plugin_root / "claim-providers" / f"{namespace}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": 1, "namespace": namespace, **fields}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_binstub(plugin_root, name: str, *, script: str = "print('{}')"):
    suffix = ".cmd" if os.name == "nt" else ""
    binstub = plugin_root / "bin" / f"{name}{suffix}"
    binstub.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        binstub.write_text(f"@echo off\r\n{script}\r\n")
    else:
        binstub.write_text(f"#!/bin/sh\n{script}\n")
        binstub.chmod(0o755)
    return binstub


def _installed_plugins_tree(tmp_path, plugin: str, marketplace: str = "copilot-extensions"):
    root = tmp_path / "installed-plugins" / marketplace / plugin
    root.mkdir(parents=True)
    return root, tmp_path / "installed-plugins"


# --- parse_manifest -----------------------------------------------------


def test_parse_manifest_requires_schema_version_1():
    with pytest.raises(cp.ManifestError, match="schema_version"):
        cp.parse_manifest({"namespace": "codespace", "status_command": ["x"]})


def test_parse_manifest_requires_at_least_one_command():
    with pytest.raises(cp.ManifestError, match="status_command"):
        cp.parse_manifest({"schema_version": 1, "namespace": "codespace"})


def test_parse_manifest_accepts_status_only():
    manifest = cp.parse_manifest(
        {"schema_version": 1, "namespace": "codespace:", "status_command": ["agent-codespaces"]}
    )
    assert manifest.namespace == "codespace"
    assert manifest.status_command == ("agent-codespaces",)
    assert manifest.reclaim_command is None


# --- discover_claim_providers --------------------------------------------


def test_discovers_an_active_provider_and_resolves_its_payload_local_binstub(tmp_path):
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    _write_binstub(plugin_root, "agent-codespaces")
    _write_manifest(
        plugin_root, "codespace",
        status_command=["agent-codespaces"], reclaim_command=["agent-codespaces"],
    )
    source = "agent-codespaces@copilot-extensions"

    import agent_worktrees.claim_providers as mod
    real_resolver = mod.resolve_active_plugins
    mod.resolve_active_plugins = lambda: _active_report(source, plugin_root)  # type: ignore[assignment]
    try:
        providers, findings = cp.discover_claim_providers(plugins_root)
    finally:
        mod.resolve_active_plugins = real_resolver

    assert set(providers) == {"codespace"}
    provider = providers["codespace"]
    assert provider.plugin == source
    assert provider.status_command[0].endswith(("agent-codespaces", "agent-codespaces.cmd"))
    assert findings == ()


def test_absent_plugins_root_yields_nothing_and_never_raises(tmp_path):
    providers, findings = cp.discover_claim_providers(tmp_path / "does-not-exist")
    assert providers == {}
    assert findings == ()


def test_malformed_manifest_is_skipped_with_a_finding(tmp_path):
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    path = plugin_root / "claim-providers" / "codespace.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")

    import agent_worktrees.claim_providers as mod
    real_resolver = mod.resolve_active_plugins
    mod.resolve_active_plugins = lambda: _active_report(  # type: ignore[assignment]
        "agent-codespaces@copilot-extensions", plugin_root
    )
    try:
        providers, findings = cp.discover_claim_providers(plugins_root)
    finally:
        mod.resolve_active_plugins = real_resolver

    assert providers == {}
    assert len(findings) == 1
    assert findings[0].reason == "invalid-entry"


def test_inactive_plugin_degrades_to_no_provider(tmp_path):
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    _write_binstub(plugin_root, "agent-codespaces")
    _write_manifest(plugin_root, "codespace", status_command=["agent-codespaces"])

    import agent_worktrees.claim_providers as mod
    real_resolver = mod.resolve_active_plugins
    # No decision recorded for this source at all -- plugin is simply not enabled.
    mod.resolve_active_plugins = lambda: ActivationReport(  # type: ignore[assignment]
        authority=ScanAuthority.COMPLETE, decisions={}
    )
    try:
        providers, findings = cp.discover_claim_providers(plugins_root)
    finally:
        mod.resolve_active_plugins = real_resolver

    assert providers == {}
    assert findings[0].reason == "not-enabled"


def test_missing_payload_binstub_degrades_to_missing_target(tmp_path):
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    # No binstub written -- manifest declares a command that doesn't exist.
    _write_manifest(plugin_root, "codespace", status_command=["agent-codespaces"])

    import agent_worktrees.claim_providers as mod
    real_resolver = mod.resolve_active_plugins
    mod.resolve_active_plugins = lambda: _active_report(  # type: ignore[assignment]
        "agent-codespaces@copilot-extensions", plugin_root
    )
    try:
        providers, findings = cp.discover_claim_providers(plugins_root)
    finally:
        mod.resolve_active_plugins = real_resolver

    assert providers == {}
    assert findings[0].reason == "missing-target"


def test_duplicate_namespace_keeps_first_and_records_a_finding(tmp_path):
    root_a, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    root_b, _ = _installed_plugins_tree(tmp_path, "agent-containers")
    _write_binstub(root_a, "agent-codespaces")
    _write_binstub(root_b, "agent-containers")
    _write_manifest(root_a, "codespace", status_command=["agent-codespaces"])
    _write_manifest(root_b, "codespace", status_command=["agent-containers"])

    import agent_worktrees.claim_providers as mod
    real_resolver = mod.resolve_active_plugins
    mod.resolve_active_plugins = lambda: ActivationReport(  # type: ignore[assignment]
        authority=ScanAuthority.COMPLETE,
        decisions={
            "agent-codespaces@copilot-extensions": EntryDecision.active(
                ActivePlugin(
                    source="agent-codespaces@copilot-extensions", name="agent-codespaces",
                    marketplace="copilot-extensions", root=root_a.resolve(), scopes=("global",),
                )
            ),
            "agent-containers@copilot-extensions": EntryDecision.active(
                ActivePlugin(
                    source="agent-containers@copilot-extensions", name="agent-containers",
                    marketplace="copilot-extensions", root=root_b.resolve(), scopes=("global",),
                )
            ),
        },
    )
    try:
        providers, findings = cp.discover_claim_providers(plugins_root)
    finally:
        mod.resolve_active_plugins = real_resolver

    # Deterministic sorted-plugin-path order: agent-codespaces < agent-containers.
    assert set(providers) == {"codespace"}
    assert providers["codespace"].plugin == "agent-codespaces@copilot-extensions"
    assert any(f.reason == "duplicate" for f in findings)


# --- split_namespaced_ref -------------------------------------------------


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("codespace:my-box-1", ("codespace", "my-box-1")),
        ("dispatch-task:abc123", ("dispatch-task", "abc123")),
        ("legacy-unnamespaced-ref", None),
        ("codespace:", None),
        (":my-box-1", None),
    ],
)
def test_split_namespaced_ref(ref, expected):
    assert cp.split_namespaced_ref(ref) == expected


# --- resolve_claim_status / resolve_claim_reclaim -------------------------


def test_resolve_claim_status_degrades_when_no_provider_registered(tmp_path):
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path / "empty")
    assert result == {
        "available": False,
        "reason": "no claim provider registered for namespace 'codespace:'",
    }


def test_resolve_claim_status_degrades_on_unnamespaced_ref(tmp_path):
    result = cp.resolve_claim_status("legacy-ref", plugins_root=tmp_path)
    assert result == {"available": False, "reason": "ref has no namespace prefix"}


def test_resolve_claim_status_invokes_the_callback_and_parses_json(tmp_path, monkeypatch):
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    _write_binstub(plugin_root, "agent-codespaces")
    _write_manifest(plugin_root, "codespace", status_command=["agent-codespaces"])

    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(plugin_root.resolve()),
            status_command=("python", "-c", "import json,sys;print(json.dumps({'exists': True, 'state': 'running'}))"),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=plugins_root)
    assert result == {"available": True, "exists": True, "state": "running"}


def test_resolve_claim_status_degrades_when_callback_exits_nonzero(tmp_path, monkeypatch):
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            status_command=("python", "-c", "import sys;sys.exit(1)"),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path)
    assert result["available"] is False
    assert "claim-status callback failed" in result["reason"]


def test_resolve_claim_reclaim_passes_apply_flag(tmp_path, monkeypatch):
    captured = {}

    def fake_run_callback(command, *, timeout):
        captured["command"] = command
        return {"reclaimed": True}

    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            reclaim_command=("agent-codespaces",),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    monkeypatch.setattr(cp, "_run_callback", fake_run_callback)
    result = cp.resolve_claim_reclaim("codespace:my-box-1", apply=True, plugins_root=tmp_path)
    assert result == {"available": True, "reclaimed": True}
    assert captured["command"] == ("agent-codespaces", "claim-reclaim", "my-box-1", "--apply")


def test_resolve_claim_reclaim_omits_apply_flag_in_dry_run(tmp_path, monkeypatch):
    captured = {}

    def fake_run_callback(command, *, timeout):
        captured["command"] = command
        return {"reclaimed": True, "detail": "would delete"}

    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            reclaim_command=("agent-codespaces",),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    monkeypatch.setattr(cp, "_run_callback", fake_run_callback)
    result = cp.resolve_claim_reclaim("codespace:my-box-1", apply=False, plugins_root=tmp_path)
    assert result == {"available": True, "reclaimed": True, "detail": "would delete"}
    assert captured["command"] == ("agent-codespaces", "claim-reclaim", "my-box-1")


def test_resolve_claim_reclaim_degrades_when_no_reclaim_command_declared(tmp_path, monkeypatch):
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            status_command=("agent-codespaces",),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_reclaim("codespace:my-box-1", apply=True, plugins_root=tmp_path)
    assert result["available"] is False
    assert "no claim provider registered" in result["reason"]
