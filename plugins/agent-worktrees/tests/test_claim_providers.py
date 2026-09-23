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


def test_parse_manifest_rejects_boolean_schema_version():
    """Regression: bool is a subclass of int in Python, so
    `schema_version: true` == 1 would otherwise silently pass."""
    with pytest.raises(cp.ManifestError, match="schema_version"):
        cp.parse_manifest({"schema_version": True, "namespace": "codespace", "status_command": ["x"]})


def test_parse_manifest_rejects_a_float_schema_version():
    """Regression: JSON 1.0 decodes to a Python float, which compares
    equal to the integer 1 but is not one -- the manifest contract
    requires the integer 1 specifically."""
    with pytest.raises(cp.ManifestError, match="schema_version"):
        cp.parse_manifest({"schema_version": 1.0, "namespace": "codespace", "status_command": ["x"]})


def test_parse_manifest_rejects_a_nul_byte_in_a_command_component():
    """Regression: an embedded NUL previously reached _payload_command's
    Path.is_file(), which can raise ValueError -- a class scan_directory's
    own OSError-only catch does not convert to a finding, breaking the
    documented never-raises discovery boundary."""
    with pytest.raises(cp.ManifestError, match="status_command"):
        cp.parse_manifest({"schema_version": 1, "namespace": "codespace", "status_command": ["agent-codespaces\x00"]})
    with pytest.raises(cp.ManifestError, match="status_command"):
        cp.parse_manifest(
            {"schema_version": 1, "namespace": "codespace", "status_command": ["agent-codespaces", "claim-status\x00"]}
        )


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


def test_parse_manifest_rejects_a_namespace_that_is_only_a_colon():
    with pytest.raises(cp.ManifestError, match="namespace"):
        cp.parse_manifest({"schema_version": 1, "namespace": ":", "status_command": ["x"]})


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


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevation on Windows")
def test_symlinked_binstub_degrades_to_target_unusable(tmp_path):
    """A manifest that points bin/<command> at a symlink (potentially
    escaping the identity-verified plugin root) must be rejected outright,
    never resolved-through -- regression for the pre-resolve lstat check."""
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    outside_target = tmp_path / "outside-the-plugin-root.sh"
    outside_target.write_text("#!/bin/sh\nexit 0\n")
    outside_target.chmod(0o755)
    bin_dir = plugin_root / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "agent-codespaces").symlink_to(outside_target)
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
    assert findings[0].reason == "target-unusable"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs elevation on Windows")
def test_symlinked_bin_directory_degrades_to_target_unusable(tmp_path):
    """Regression: even when bin/<command> itself is an ordinary regular
    file (passes the pre-resolve lstat check), an ANCESTOR directory (here
    bin/ itself) being a symlink must still be caught by the post-resolve
    containment check -- resolve() would otherwise silently escape the
    identity-verified plugin root through the symlinked directory."""
    plugin_root, plugins_root = _installed_plugins_tree(tmp_path, "agent-codespaces")
    outside_bin = tmp_path / "outside-bin"
    outside_bin.mkdir()
    real_command = outside_bin / "agent-codespaces"
    real_command.write_text("#!/bin/sh\nexit 0\n")
    real_command.chmod(0o755)
    (plugin_root / "bin").symlink_to(outside_bin, target_is_directory=True)
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
    assert findings[0].reason == "target-unusable"


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
        # Regression: cmd.exe metacharacters (and other shell-significant
        # characters) in either half must be rejected outright, not passed
        # through to a callback command line.
        ("codespace:foo&whoami", None),
        ("codespace:foo|whoami", None),
        ("codespace:foo;rm -rf", None),
        ("codespace:foo`id`", None),
        ("codespace:foo$(id)", None),
        ("codespace:foo^whoami", None),
        ("codespace:foo%PATH%", None),
        ('codespace:foo"bar', None),
        ("codespace:foo bar", None),
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
    assert result == {"available": False, "reason": "ref has no namespace prefix, or contains unsafe characters"}


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


def test_resolve_claim_status_envelope_available_wins_over_callback_supplied_key(tmp_path, monkeypatch):
    """Regression: a provider that happens to emit its own "available" key
    must never be able to shadow the envelope's own True/False verdict."""
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            status_command=(
                "python", "-c",
                "import json;print(json.dumps({'exists': True, 'available': False}))",
            ),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path)
    assert result == {"available": True, "exists": True}


def test_resolve_claim_status_degrades_when_required_field_missing(tmp_path, monkeypatch):
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            status_command=("python", "-c", "import json;print(json.dumps({'state': 'running'}))"),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path)
    assert result["available"] is False
    assert "callback failed" in result["reason"]


def test_resolve_claim_status_degrades_when_required_field_wrong_type(tmp_path, monkeypatch):
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            status_command=("python", "-c", "import json;print(json.dumps({'exists': 'yes'}))"),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path)
    assert result["available"] is False


def test_resolve_claim_status_degrades_when_optional_field_wrong_type(tmp_path, monkeypatch):
    """Regression: "state"/"detail" are documented as optional STRINGS --
    a wrong-typed value (e.g. an object) must not be silently passed
    through as if the result were well-shaped."""
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            status_command=("python", "-c", "import json;print(json.dumps({'exists': True, 'state': {}}))"),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path)
    assert result["available"] is False


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

    def fake_run_callback(command, *, timeout, required_bool_field):
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

    def fake_run_callback(command, *, timeout, required_bool_field):
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
    """Regression: a provider that exists but only declares status_command
    must be distinguished from an absent provider entirely -- the reason
    must name the actual configuration gap, not falsely claim no provider
    is registered at all."""
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
    assert "no claim provider registered" not in result["reason"]
    assert "declares no reclaim_command" in result["reason"]


def test_resolve_claim_status_degrades_when_no_status_command_declared(tmp_path, monkeypatch):
    def fake_discover(_plugins_root):
        provider = cp.ClaimProviderManifest(
            namespace="codespace",
            plugin="agent-codespaces@copilot-extensions",
            plugin_root=str(tmp_path),
            reclaim_command=("agent-codespaces",),
        )
        return {"codespace": provider}, ()

    monkeypatch.setattr(cp, "discover_claim_providers", fake_discover)
    result = cp.resolve_claim_status("codespace:my-box-1", plugins_root=tmp_path)
    assert result["available"] is False
    assert "no claim provider registered" not in result["reason"]
    assert "declares no status_command" in result["reason"]


# --- _windows_batch_argv --------------------------------------------------


def test_windows_batch_argv_routes_cmd_shims_through_comspec(monkeypatch):
    """Regression: CreateProcess (what subprocess.run(shell=False) uses)
    cannot execute a .cmd file directly -- every real Windows claim-provider
    callback would otherwise fail to launch. Exercised on any platform by
    forcing os.name to "nt", since the routing logic itself is pure.

    The batch path and each argument must be SEPARATE argv elements (never
    pre-joined into one already-quoted string) -- subprocess.run() quotes
    each element itself when building the real CreateProcess command line,
    so pre-quoting the whole thing first would double-quote it and break
    cmd.exe's parsing (regression for an earlier revision of this helper
    that used list2cmdline() to build one combined string)."""
    monkeypatch.setattr(cp.os, "name", "nt")
    monkeypatch.setenv("ComSpec", r"C:\Windows\System32\cmd.exe")
    argv = cp._windows_batch_argv(("C:\\plugins\\agent-codespaces\\bin\\agent-codespaces.cmd", "claim-status", "my box"))
    assert argv == [
        r"C:\Windows\System32\cmd.exe",
        "/d", "/s", "/c",
        "C:\\plugins\\agent-codespaces\\bin\\agent-codespaces.cmd",
        "claim-status",
        "my box",
    ]


def test_windows_batch_argv_leaves_non_batch_commands_untouched(monkeypatch):
    monkeypatch.setattr(cp.os, "name", "nt")
    command = ("C:\\plugins\\agent-codespaces\\bin\\agent-codespaces.exe", "claim-status", "ref")
    assert cp._windows_batch_argv(command) == list(command)


def test_windows_batch_argv_is_a_noop_off_windows(monkeypatch):
    monkeypatch.setattr(cp.os, "name", "posix")
    command = ("/opt/plugin/bin/agent-codespaces.cmd", "claim-status", "ref")
    assert cp._windows_batch_argv(command) == list(command)
