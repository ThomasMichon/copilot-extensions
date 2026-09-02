"""Tests for active-plugin and user-level supplementary config providers.

An active plugin can declare its shipped CodeSpace target config directly in
plugin.json with no control-plane repo or config.d pointer. Compatibility
config.d inputs remain supported. All provider config merges at the lowest
precedence, below adopted-repo and current-working-directory config.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
    WarningTracker,
)
from plugin_activation import ActivationReport, ActivePlugin, ActivePluginRoot

import agent_codespaces.config as cfg
from agent_codespaces.config import (
    AdoptedRepo,
    ConfigDropin,
    ConfigDropinRegistryReport,
    ConfigProviderReports,
)

_SAMPLE_CONFIG = """\
credentials:
  ado_host: ado.example.com
repos:
  example-org/example-codespaces:
    machine_type: largePremiumLinux256gb
    workspace_repo: example-app
    devcontainer_path: .devcontainer/devcontainer.json
"""

_REPO_KEY = "example-org/example-codespaces"


def _plugin_config(tmp_path: Path, body: str = _SAMPLE_CONFIG) -> Path:
    p = tmp_path / "plugin" / "references" / "agent-codespaces" / "config.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, "utf-8")
    return p


def _config_d(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "config.d"
    d.mkdir()
    monkeypatch.setattr(cfg, "config_d_dir", lambda: d)
    return d


def _active_report(source: str, root: Path) -> ActivationReport:
    active = ActivePlugin(
        source=source,
        name=source.split("@")[0],
        marketplace=source.split("@")[1],
        root=root.resolve(),
        scopes=("global",),
    )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={source: EntryDecision.active(active)},
    )


def _declared_plugin(
    tmp_path: Path,
    *,
    source: str = "sample-harness@example-marketplace",
    declaration: object = "references/agent-codespaces/config.yaml",
    config_body: str | None = _SAMPLE_CONFIG,
) -> tuple[str, Path, Path]:
    root = tmp_path / "plugin"
    target = root / "references" / "agent-codespaces" / "config.yaml"
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps({
        "name": source.split("@")[0],
        cfg.PLUGIN_CONFIG_MANIFEST_FIELD: declaration,
    }), "utf-8")
    if config_body is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(config_body, "utf-8")
    return source, root, target


def _managed_entry(
    d: Path,
    *,
    source: str,
    root: Path,
    target: Path,
    name: str = "provider.json",
) -> Path:
    entry = d / name
    entry.write_text(json.dumps({
        "schema_version": 1,
        "plugin": source,
        "plugin_root": str(root.resolve()),
        "target": str(target.resolve()),
    }), "utf-8")
    return entry


def _scan(
    d: Path,
    monkeypatch,
    report: ActivationReport,
    *,
    previous: dict[str, ConfigDropin] | None = None,
) -> ConfigDropinRegistryReport:
    monkeypatch.setattr(cfg, "config_d_dir", lambda: d)
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda **_: report)
    return cfg.scan_config_dropin_registry(previous=previous)


def test_active_plugin_manifest_config_needs_no_pointer_or_adopted_repo(
    tmp_path, monkeypatch
):
    source, root, target = _declared_plugin(tmp_path)
    d = tmp_path / "config.d"
    monkeypatch.setattr(cfg, "config_d_dir", lambda: d)
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    merged = cfg.load_merged_config(include_cwd=False)

    assert merged.repos[_REPO_KEY].workspace_repo == "example-app"
    assert merged.credentials.ado_host == "ado.example.com"
    assert merged.source_paths == [target.resolve().parent]


def test_active_plugin_manifest_prefers_live_local_root_over_stale_installed_copy(
    tmp_path, monkeypatch
):
    source, local_root, target = _declared_plugin(tmp_path)
    installed_root = tmp_path / "installed" / "plugin"
    installed_root.mkdir(parents=True)
    (installed_root / "plugin.json").write_text(
        json.dumps({"name": source.split("@")[0]}),
        "utf-8",
    )
    active = ActivePlugin(
        source=source,
        name=source.split("@")[0],
        marketplace=source.split("@")[1],
        root=local_root.resolve(),
        scopes=("global", "project:sample"),
        roots=(
            ActivePluginRoot(
                root=local_root.resolve(),
                scopes=("project:sample",),
                kind="directory",
            ),
            ActivePluginRoot(
                root=installed_root.resolve(),
                scopes=("global",),
                kind="installed",
            ),
        ),
    )
    report = ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={source: EntryDecision.active(active)},
    )
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: report)
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    scanned = cfg.scan_active_plugin_config_registry()

    assert [item.target for item in scanned.active_configs] == [target.resolve()]


def test_adopted_and_cwd_configs_override_active_plugin_default(
    tmp_path, monkeypatch
):
    source, root, _target = _declared_plugin(tmp_path)
    adopted = tmp_path / "adopted"
    adopted_config = adopted / cfg.CANONICAL_CONFIG_REL
    adopted_config.parent.mkdir(parents=True)
    adopted_config.write_text(
        f"repos:\n  {_REPO_KEY}:\n    machine_type: ADOPTED\n",
        "utf-8",
    )
    cwd = tmp_path / "cwd"
    cwd_config = cwd / cfg.CANONICAL_CONFIG_REL
    cwd_config.parent.mkdir(parents=True)
    cwd_config.write_text(
        f"repos:\n  {_REPO_KEY}:\n    machine_type: CWD\n",
        "utf-8",
    )
    monkeypatch.setattr(cfg, "config_d_dir", lambda: tmp_path / "config.d")
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [AdoptedRepo(path=adopted)])
    monkeypatch.setattr(cfg, "cwd_repo_root", lambda: cwd)
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    adopted_wins = cfg.load_merged_config(include_cwd=True)
    assert adopted_wins.repos[_REPO_KEY].machine_type == "ADOPTED"

    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    cwd_wins = cfg.load_merged_config(include_cwd=True)
    assert cwd_wins.repos[_REPO_KEY].machine_type == "CWD"


def test_stale_config_d_pointer_cannot_suppress_active_declaration(
    tmp_path, monkeypatch
):
    source, root, target = _declared_plugin(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    stale = _managed_entry(
        d,
        source=source,
        root=root,
        target=tmp_path / "missing" / "config.yaml",
    )
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    reports = cfg.scan_config_providers()
    assert [item.target for item in reports.active_configs] == [target.resolve()]
    assert any(
        finding.entry == str(stale) and finding.reason == "missing-target"
        for finding in reports.config_d.findings
    )
    assert _REPO_KEY in cfg.load_merged_config(include_cwd=False).repos


def test_valid_compatibility_pointer_is_reported_but_not_merged_twice(
    tmp_path, monkeypatch
):
    source, root, target = _declared_plugin(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    pointer = _managed_entry(d, source=source, root=root, target=target)
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    reports = cfg.scan_config_providers()

    assert [item.target for item in reports.active_configs] == [target.resolve()]
    assert any(
        finding.entry == str(pointer) and finding.reason == "superseded"
        for finding in reports.config_d.findings
    )


def test_disabled_plugin_manifest_declaration_is_ignored(tmp_path, monkeypatch):
    _source, _root, _target = _declared_plugin(tmp_path)
    monkeypatch.setattr(
        cfg,
        "resolve_active_plugins",
        lambda: ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    report = cfg.scan_active_plugin_config_registry()

    assert not report.active_configs
    assert not report.findings


@pytest.mark.parametrize(
    ("declaration", "config_body", "reason", "detail"),
    [
        ("../outside.yaml", _SAMPLE_CONFIG, "identity-mismatch", None),
        ("references/agent-codespaces/missing.yaml", None, "missing-target", None),
        ([], None, "invalid-entry", "must be a non-empty relative path"),
        (
            "references/agent-codespaces/config.yaml",
            "repos: [invalid]",
            "invalid-entry",
            "repos must be a mapping",
        ),
    ],
)
def test_invalid_active_plugin_declarations_are_diagnosed(
    tmp_path, monkeypatch, declaration, config_body, reason, detail
):
    source, root, _target = _declared_plugin(
        tmp_path,
        declaration=declaration,
        config_body=config_body,
    )
    if (
        isinstance(declaration, str)
        and declaration.startswith("..")
        and config_body is not None
    ):
        (root.parent / "outside.yaml").write_text(config_body, "utf-8")
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    report = cfg.scan_active_plugin_config_registry()

    assert not report.active_configs
    assert report.findings[0].owner == source
    assert report.findings[0].entry == str(root / "plugin.json")
    assert report.findings[0].reason == reason
    if detail:
        assert detail in report.findings[0].detail


def test_invalid_active_declaration_does_not_suppress_valid_peer(
    tmp_path, monkeypatch
):
    bad_source, bad_root, _ = _declared_plugin(
        tmp_path / "bad",
        source="bad-provider@example-marketplace",
        config_body="repos: [invalid]",
    )
    good_source, good_root, good_target = _declared_plugin(
        tmp_path / "good",
        source="good-provider@example-marketplace",
    )
    report = ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={
            bad_source: _active_report(bad_source, bad_root).decisions[bad_source],
            good_source: _active_report(good_source, good_root).decisions[good_source],
        },
    )
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: report)
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})

    providers = cfg.scan_active_plugin_config_registry()

    assert [item.target for item in providers.active_configs] == [
        good_target.resolve()
    ]
    assert providers.findings[0].owner == bad_source
    assert providers.findings[0].reason == "invalid-entry"


def test_indeterminate_plugin_activation_retains_last_known_declaration(
    tmp_path, monkeypatch
):
    source, root, target = _declared_plugin(tmp_path)
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    first = cfg.scan_active_plugin_config_registry(previous={})

    monkeypatch.setattr(
        cfg,
        "resolve_active_plugins",
        lambda: ActivationReport(ScanAuthority.INDETERMINATE, {}),
    )
    retained = cfg.scan_active_plugin_config_registry(
        previous=dict(first.active_entries)
    )

    assert [item.target for item in retained.active_configs] == [target.resolve()]
    assert retained.findings[0].reason == "registry-indeterminate"


def test_discover_resolves_pointer(tmp_path, monkeypatch):
    plugin_cfg = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    (d / "sample-harness.conf").write_text(
        f"# managed by sample-harness\n{plugin_cfg}\n", "utf-8"
    )
    assert cfg.discover_dropin_configs() == [plugin_cfg]


def test_discover_skips_missing_target(tmp_path, monkeypatch):
    d = _config_d(tmp_path, monkeypatch)
    (d / "stale.conf").write_text(str(tmp_path / "gone" / "config.yaml") + "\n", "utf-8")
    assert cfg.discover_dropin_configs() == []


def test_discover_direct_yaml_entry(tmp_path, monkeypatch):
    d = _config_d(tmp_path, monkeypatch)
    frag = d / "inline.yaml"
    frag.write_text(_SAMPLE_CONFIG, "utf-8")
    assert cfg.discover_dropin_configs() == [frag]


def test_merged_config_from_dropin_without_any_repo(tmp_path, monkeypatch):
    plugin_cfg = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    (d / "sample-harness.conf").write_text(str(plugin_cfg) + "\n", "utf-8")
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])

    merged = cfg.load_merged_config(include_cwd=False)
    # Discoverable with NO control-plane repo -- the golden-path seam.
    assert _REPO_KEY in merged.repos
    assert merged.repos[_REPO_KEY].machine_type == "largePremiumLinux256gb"
    assert merged.credentials.ado_host == "ado.example.com"


def test_adopted_repo_overrides_dropin(tmp_path, monkeypatch):
    # A drop-in provides the repo at one machine_type...
    plugin_cfg = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    (d / "sample-harness.conf").write_text(str(plugin_cfg) + "\n", "utf-8")
    # ...and an ADOPTED repo declares the SAME repo key with a different value.
    repo = tmp_path / "repo"
    repo_cfg = repo / ".agent-codespaces" / "config.yaml"
    repo_cfg.parent.mkdir(parents=True)
    repo_cfg.write_text(
        f"repos:\n  {_REPO_KEY}:\n    machine_type: OVERRIDDEN\n", "utf-8"
    )
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [AdoptedRepo(path=repo)])

    merged = cfg.load_merged_config(include_cwd=False)
    # Adopted repo is merged first -> "first wins", so it beats the drop-in.
    assert merged.repos[_REPO_KEY].machine_type == "OVERRIDDEN"


def test_no_dropin_no_repo_is_empty(tmp_path, monkeypatch):
    _config_d(tmp_path, monkeypatch)  # empty config.d
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    merged = cfg.load_merged_config(include_cwd=False)
    assert merged.repos == {}
    assert merged.source_paths == []


def test_merged_config_uses_managed_registry_report(tmp_path, monkeypatch):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    _managed_entry(d, source=source, root=root, target=target)
    monkeypatch.setattr(
        cfg, "resolve_active_plugins", lambda **_: _active_report(source, root)
    )
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])

    merged = cfg.load_merged_config(include_cwd=False)
    assert _REPO_KEY in merged.repos
    assert merged.source_paths == [target.resolve().parent]


def test_managed_pointer_requires_enabled_exact_identity_root(tmp_path, monkeypatch):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    entry = _managed_entry(d, source=source, root=root, target=target)

    report = _scan(d, monkeypatch, _active_report(source, root))
    assert report.authority is ScanAuthority.COMPLETE
    assert report.active_configs[0].target == target.resolve()
    assert report.active_configs[0].entry_class == "managed-plugin"

    disabled = _scan(
        d,
        monkeypatch,
        ActivationReport(authority=ScanAuthority.COMPLETE, decisions={}),
    )
    assert not disabled.active_configs
    assert disabled.findings[0].reason == "not-enabled"

    uninstalled = _scan(
        d,
        monkeypatch,
        ActivationReport(
            authority=ScanAuthority.COMPLETE,
            decisions={
                source: EntryDecision.inactive(Finding(
                    registry="plugin-activation",
                    entry="installed-plugin",
                    status="inactive",
                    reason="missing-target",
                    owner=source,
                )),
            },
        ),
    )
    assert not uninstalled.active_configs
    assert uninstalled.findings[0].reason == "missing-target"

    _managed_entry(
        d,
        source=source,
        root=tmp_path / "other-root",
        target=target,
        name=entry.name,
    )
    mismatch = _scan(d, monkeypatch, _active_report(source, root))
    assert not mismatch.active_configs
    assert mismatch.findings[0].reason == "identity-mismatch"


def test_managed_pointer_accepts_secondary_authoritative_live_root(
    tmp_path,
    monkeypatch,
):
    source = "sample-harness@example-marketplace"
    installed = tmp_path / "installed"
    target = _plugin_config(installed)
    local = tmp_path / "local"
    local.mkdir()
    d = _config_d(tmp_path, monkeypatch)
    _managed_entry(d, source=source, root=installed, target=target)
    active = ActivePlugin(
        source=source,
        name="sample-harness",
        marketplace="example-marketplace",
        root=local.resolve(),
        scopes=("global", "project:demo"),
        roots=(
            ActivePluginRoot(local.resolve(), ("project:demo",), "directory"),
            ActivePluginRoot(installed.resolve(), ("global",), "installed"),
        ),
    )

    report = _scan(
        d,
        monkeypatch,
        ActivationReport(
            ScanAuthority.COMPLETE,
            {source: EntryDecision.active(active)},
        ),
    )

    assert report.active_configs[0].target == target.resolve()


def test_managed_pointer_activation_uses_real_home_not_agent_home(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    _managed_entry(d, source=source, root=root, target=target)
    calls: list[dict[str, object]] = []

    def activation_resolver(**kwargs):
        calls.append(kwargs)
        return _active_report(source, root)

    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-state-sandbox"))
    monkeypatch.setattr(cfg, "resolve_active_plugins", activation_resolver)
    report = cfg.scan_config_dropin_registry()

    assert report.active_configs
    assert calls == [{}]


def test_managed_pointer_rejects_target_escape_and_malformed_peer(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    escaped = _plugin_config(tmp_path / "outside")
    d = _config_d(tmp_path, monkeypatch)
    escaped_entry = _managed_entry(
        d,
        source=source,
        root=root,
        target=escaped,
    )
    escaped_pointer = json.loads(escaped_entry.read_text("utf-8"))
    escaped_pointer["target"] = str(
        root / ".." / "outside" / "plugin" / "references" /
        "agent-codespaces" / escaped.name
    )
    escaped_entry.write_text(json.dumps(escaped_pointer), "utf-8")
    (d / "bad.json").write_text("[]", "utf-8")
    valid = _managed_entry(
        d, source=source, root=root, target=target, name="valid.json"
    )

    report = _scan(d, monkeypatch, _active_report(source, root))
    assert [item.entry for item in report.active_configs] == [valid]
    assert {finding.reason for finding in report.findings} == {
        "invalid-entry", "identity-mismatch"
    }


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ("defaults: invalid\n", "defaults must be a mapping"),
        (
            "credentials:\n  sources: []\n",
            "credentials.sources must be a mapping",
        ),
        (
            "repos:\n  example-org/example-codespaces: invalid\n",
            "repos entries must have string names and mapping values",
        ),
        ("provision: invalid\n", "provision must be a mapping"),
        ("provision:\n  files: invalid\n", "provision.files must be a list"),
        (
            "provision:\n  on_connect: invalid\n",
            "provision.on_connect must be a list",
        ),
        (
            "provision:\n  on_create: invalid\n",
            "provision.on_create must be a list",
        ),
        (
            "provision:\n  files:\n  - dest: /remote/file\n",
            "provision.files[0].src must be a non-empty string",
        ),
        (
            "provision:\n  files:\n  - src: []\n    dest: /remote/file\n",
            "provision.files[0].src must be a non-empty string",
        ),
        (
            "provision:\n  files:\n  - src: source\n",
            "provision.files[0].dest must be a non-empty string",
        ),
        (
            "provision:\n  files:\n  - src: source\n    dest: []\n",
            "provision.files[0].dest must be a non-empty string",
        ),
    ],
)
def test_structurally_invalid_target_is_inactive_while_valid_peer_loads(
    tmp_path, monkeypatch, body, detail
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    invalid_target = _plugin_config(root, body)
    valid_target = _plugin_config(root / "valid")
    d = _config_d(tmp_path, monkeypatch)
    invalid_entry = _managed_entry(
        d, source=source, root=root, target=invalid_target, name="invalid.json"
    )
    valid_entry = _managed_entry(
        d, source=source, root=root, target=valid_target, name="valid.json"
    )

    report = _scan(d, monkeypatch, _active_report(source, root))

    assert [item.entry for item in report.active_configs] == [valid_entry]
    decision = report.snapshot.decisions[str(invalid_entry)]
    assert decision.status is EntryStatus.INACTIVE
    assert [finding.reason for finding in decision.findings] == ["invalid-entry"]
    assert detail in decision.findings[0].detail


@pytest.mark.parametrize(
    ("invalid_credentials", "detail"),
    [
        (
            "feed_token_env:\n"
            "  - VALID_TOKEN\n"
            "  - 42\n",
            "credentials.feed_token_env entries must be non-empty strings",
        ),
        (
            "sources:\n"
            "  shared:\n"
            "    allowed_hosts:\n"
            "      - valid.example\n"
            "      - 42\n",
            "credentials.sources.shared.allowed_hosts entries must be non-empty strings",
        ),
        (
            "sources:\n"
            "  shared:\n"
            "    allowed_resources:\n"
            "      - https://valid.example/resource\n"
            "      - 42\n",
            "credentials.sources.shared.allowed_resources entries must be non-empty strings",
        ),
    ],
)
def test_invalid_credential_member_is_inactive_while_valid_peer_merges(
    tmp_path, monkeypatch, invalid_credentials, detail
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    invalid_target = _plugin_config(
        root,
        "credentials:\n  " + invalid_credentials.replace("\n", "\n  "),
    )
    valid_target = _plugin_config(
        root / "valid",
        "credentials:\n"
        "  feed_token_env:\n"
        "    - VALID_TOKEN\n"
        "  sources:\n"
        "    shared:\n"
        "      allowed_hosts:\n"
        "        - valid.example\n"
        "      allowed_resources:\n"
        "        - https://valid.example/resource\n",
    )
    d = _config_d(tmp_path, monkeypatch)
    invalid_entry = _managed_entry(
        d, source=source, root=root, target=invalid_target, name="a-invalid.json"
    )
    valid_entry = _managed_entry(
        d, source=source, root=root, target=valid_target, name="b-valid.json"
    )
    activation = _active_report(source, root)

    report = _scan(d, monkeypatch, activation)
    assert [item.entry for item in report.active_configs] == [valid_entry]
    decision = report.snapshot.decisions[str(invalid_entry)]
    assert decision.status is EntryStatus.INACTIVE
    assert detail in decision.findings[0].detail

    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    merged = cfg.load_merged_config(include_cwd=False)
    assert merged.credentials.feed_token_env == ["VALID_TOKEN"]
    assert merged.credentials.sources["shared"].allowed_hosts == ["valid.example"]
    assert merged.credentials.sources["shared"].allowed_resources == [
        "https://valid.example/resource"
    ]


def test_direct_operator_yaml_and_known_legacy_are_active_with_correct_class(
    tmp_path, monkeypatch
):
    target = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    direct = d / "operator.yaml"
    direct.write_text(_SAMPLE_CONFIG, "utf-8")
    legacy = d / "sample-harness.conf"
    legacy.write_text(f"# managed by sample-harness\n{target}\n", "utf-8")

    report = _scan(
        d, monkeypatch, ActivationReport(ScanAuthority.COMPLETE, {})
    )
    classes = {item.entry: item.entry_class for item in report.active_configs}
    assert classes == {
        direct: "operator",
        legacy: "legacy-plugin",
    }
    assert [(f.reason, f.status) for f in report.findings] == [
        ("legacy-unattributed", "active-with-advisory")
    ]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (None, "missing-target"),
        ("not: [valid", "invalid-entry"),
    ],
)
def test_managed_target_failures_are_isolated(tmp_path, monkeypatch, body, reason):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = root / "references" / "agent-codespaces" / "config.yaml"
    root.mkdir()
    if body is not None:
        target.parent.mkdir(parents=True)
        target.write_text(body, "utf-8")
    d = _config_d(tmp_path, monkeypatch)
    _managed_entry(d, source=source, root=root, target=target)

    report = _scan(d, monkeypatch, _active_report(source, root))
    assert not report.active_configs
    assert report.findings[0].reason == reason


def test_unusable_target_and_indeterminate_eligibility_preserve_prior(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    entry = _managed_entry(d, source=source, root=root, target=target)
    active = _active_report(source, root)
    prior = _scan(d, monkeypatch, active).active_entries

    original_read_text = Path.read_text

    def unreadable_target(path: Path, *args, **kwargs):
        if path == target.resolve():
            raise OSError("sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable_target)
    unusable = _scan(d, monkeypatch, active, previous=dict(prior))
    assert unusable.active_entries == prior
    assert unusable.findings[0].reason == "target-unusable"
    assert unusable.findings[0].status == "indeterminate"
    monkeypatch.undo()

    indeterminate = ActivationReport(
        authority=ScanAuthority.INDETERMINATE,
        decisions=active.decisions,
    )
    retained = _scan(d, monkeypatch, indeterminate, previous=dict(prior))
    assert retained.active_entries == prior
    assert retained.findings[0].reason == "entry-indeterminate"
    assert retained.findings[0].status == "indeterminate"
    assert entry.is_file()


@pytest.mark.parametrize("invalidated", ["pointer", "target"])
def test_invalid_utf8_withdraws_previously_active_entry(
    tmp_path, monkeypatch, invalidated
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    entry = _managed_entry(d, source=source, root=root, target=target)
    activation = _active_report(source, root)
    prior = _scan(d, monkeypatch, activation).active_entries

    (entry if invalidated == "pointer" else target).write_bytes(b"\xff")
    report = _scan(d, monkeypatch, activation, previous=dict(prior))

    assert report.authority is ScanAuthority.COMPLETE
    assert report.active_entries == {}
    decision = report.snapshot.decisions[str(entry)]
    assert decision.status is EntryStatus.INACTIVE
    assert [finding.reason for finding in decision.findings] == ["invalid-entry"]


def test_symlink_target_is_not_followed_when_supported(tmp_path, monkeypatch):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    escaped = _plugin_config(tmp_path / "outside")
    linked_target = target.parent / "linked.yaml"
    try:
        os.symlink(escaped, linked_target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    d = _config_d(tmp_path, monkeypatch)
    entry = _managed_entry(d, source=source, root=root, target=linked_target)
    pointer = json.loads(entry.read_text("utf-8"))
    pointer["target"] = str(linked_target)
    entry.write_text(json.dumps(pointer), "utf-8")

    report = _scan(d, monkeypatch, _active_report(source, root))
    assert not report.active_entries
    assert report.findings[0].reason == "target-unusable"


def test_registry_absence_withdraws_but_indeterminate_retains_prior(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    _managed_entry(d, source=source, root=root, target=target)
    activation = _active_report(source, root)
    first = _scan(d, monkeypatch, activation)
    prior = dict(first.active_entries)

    original_iterdir = Path.iterdir

    def unreadable(path: Path):
        if path == d:
            raise OSError("permission denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    indeterminate = _scan(d, monkeypatch, activation, previous=prior)
    assert indeterminate.authority is ScanAuthority.INDETERMINATE
    assert indeterminate.active_entries == prior
    assert indeterminate.findings[0].remedy
    monkeypatch.undo()

    for child in d.iterdir():
        child.unlink()
    absent_d = tmp_path / "absent"
    absent = _scan(absent_d, monkeypatch, activation, previous=prior)
    assert absent.authority is ScanAuthority.ABSENT
    assert absent.active_entries == {}


def test_entry_read_indeterminate_retains_only_prior_entry(tmp_path, monkeypatch):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    old = _managed_entry(d, source=source, root=root, target=target, name="old.json")
    activation = _active_report(source, root)

    first = _scan(d, monkeypatch, activation)
    prior = dict(first.active_entries)
    fresh = _managed_entry(d, source=source, root=root, target=target, name="fresh.json")
    original_read_text = Path.read_text

    def intermittent_read(path: Path, *args, **kwargs):
        if path == fresh:
            raise OSError("sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", intermittent_read)
    report = _scan(d, monkeypatch, activation, previous=prior)
    assert list(report.active_entries) == [str(old)]
    assert any(
        finding.entry == str(fresh) and finding.reason == "entry-indeterminate"
        for finding in report.findings
    )


def test_post_enumeration_entry_deletion_withdraws_prior_entry(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    entry = _managed_entry(d, source=source, root=root, target=target)
    activation = _active_report(source, root)
    prior = _scan(d, monkeypatch, activation).active_entries
    original_lstat = Path.lstat

    def disappears_after_enumeration(path: Path):
        if path == entry:
            raise FileNotFoundError("entry disappeared")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", disappears_after_enumeration)
    report = _scan(d, monkeypatch, activation, previous=dict(prior))

    assert report.authority is ScanAuthority.COMPLETE
    assert report.active_entries == {}
    assert str(entry) not in report.snapshot.decisions


def test_confirmed_entry_deletion_withdraws_config(tmp_path, monkeypatch):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    entry = _managed_entry(d, source=source, root=root, target=target)
    activation = _active_report(source, root)
    previous = _scan(d, monkeypatch, activation).active_entries
    entry.unlink()

    report = _scan(d, monkeypatch, activation, previous=dict(previous))
    assert report.authority is ScanAuthority.COMPLETE
    assert report.active_entries == {}


def test_implicit_retained_state_is_scoped_to_config_d_root(tmp_path, monkeypatch):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    first_root = tmp_path / "first-config.d"
    first_root.mkdir()
    _managed_entry(first_root, source=source, root=root, target=target)
    second_root = tmp_path / "second-config.d"
    second_root.write_text("not a directory", encoding="utf-8")

    monkeypatch.setattr(cfg, "_CONFIG_D_LAST_KNOWN", {})
    monkeypatch.setattr(cfg, "_CONFIG_D_LAST_KNOWN_ROOT", None)
    monkeypatch.setattr(
        cfg, "resolve_active_plugins", lambda: _active_report(source, root)
    )
    monkeypatch.setattr(cfg, "config_d_dir", lambda: first_root)
    first = cfg.scan_config_dropin_registry()
    assert first.active_entries

    monkeypatch.setattr(cfg, "config_d_dir", lambda: second_root)
    second = cfg.scan_config_dropin_registry()
    assert second.authority is ScanAuthority.INDETERMINATE
    assert second.active_entries == {}


def test_retained_entry_and_new_entry_for_same_target_load_once(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    retained = _managed_entry(
        d, source=source, root=root, target=target, name="a-retained.json"
    )
    activation = _active_report(source, root)
    prior = _scan(d, monkeypatch, activation).active_entries
    new = _managed_entry(
        d, source=source, root=root, target=target, name="b-new.json"
    )
    original_read_text = Path.read_text

    def transient_pointer_read(path: Path, *args, **kwargs):
        if path == retained:
            raise OSError("sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", transient_pointer_read)
    report = _scan(d, monkeypatch, activation, previous=dict(prior))

    assert [item.entry for item in report.active_configs] == [retained]
    assert report.snapshot.decisions[str(retained)].status is EntryStatus.INDETERMINATE
    loser = report.snapshot.decisions[str(new)]
    assert loser.status is EntryStatus.INACTIVE
    assert [finding.reason for finding in loser.findings] == ["duplicate"]
    assert [
        finding.reason for finding in report.findings
        if finding.entry == str(new)
    ] == ["duplicate"]
    doctor = report.to_dict()
    assert [item["entry"] for item in doctor["active_entries"]] == [str(retained)]
    assert next(
        item for item in doctor["entries"] if item["entry"] == str(new)
    )["status"] == "inactive"


def test_unselected_indeterminate_duplicate_remains_cached_after_winner_withdraws(
    tmp_path, monkeypatch
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    retained = _managed_entry(
        d, source=source, root=root, target=target, name="z-retained.json"
    )
    activation = _active_report(source, root)
    monkeypatch.setattr(cfg, "_CONFIG_D_LAST_KNOWN", {})
    monkeypatch.setattr(cfg, "_CONFIG_D_LAST_KNOWN_ROOT", None)
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: activation)

    first = cfg.scan_config_dropin_registry()
    assert [item.entry for item in first.active_configs] == [retained]
    new = _managed_entry(
        d, source=source, root=root, target=target, name="a-new.json"
    )
    original_read_text = Path.read_text

    def transient_retained_read(path: Path, *args, **kwargs):
        if path == retained:
            raise OSError("sharing violation")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", transient_retained_read)
    second = cfg.scan_config_dropin_registry()
    assert [item.entry for item in second.active_configs] == [new]
    assert second.snapshot.decisions[str(retained)].status is EntryStatus.INDETERMINATE
    assert [
        finding.reason for finding in second.findings
        if finding.entry == str(retained)
    ] == ["entry-indeterminate"]
    assert set(cfg._CONFIG_D_LAST_KNOWN) == {str(new), str(retained)}

    new.unlink()
    third = cfg.scan_config_dropin_registry()
    assert [item.entry for item in third.active_configs] == [retained]
    assert third.active_configs[0].target == target.resolve()
    assert third.snapshot.decisions[str(retained)].status is EntryStatus.INDETERMINATE


def test_duplicate_legacy_pointer_loser_has_only_duplicate_finding(
    tmp_path, monkeypatch
):
    target = _plugin_config(tmp_path)
    d = _config_d(tmp_path, monkeypatch)
    winner = d / "a-harness.conf"
    loser = d / "b-harness.conf"
    winner.write_text(f"{target}\n", encoding="utf-8")
    loser.write_text(f"{target}\n", encoding="utf-8")

    report = _scan(d, monkeypatch, ActivationReport(ScanAuthority.COMPLETE, {}))

    assert [item.entry for item in report.active_configs] == [winner]
    decision = report.snapshot.decisions[str(loser)]
    assert decision.status is EntryStatus.INACTIVE
    assert [finding.reason for finding in decision.findings] == ["duplicate"]
    assert [
        finding.reason for finding in report.findings
        if finding.entry == str(loser)
    ] == ["duplicate"]
    assert [
        (finding.entry, finding.reason) for finding in report.findings
    ] == [
        (str(winner), "legacy-unattributed"),
        (str(loser), "duplicate"),
    ]
    warnings = WarningTracker().select(report.findings, now=0)
    assert [(finding.entry, finding.reason) for finding in warnings.emitted] == [
        (str(winner), "legacy-unattributed"),
        (str(loser), "duplicate"),
    ]


def test_duplicate_target_is_inactive_and_warnings_are_bounded(
    tmp_path, monkeypatch, caplog
):
    source = "sample-harness@example-marketplace"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    d = _config_d(tmp_path, monkeypatch)
    _managed_entry(d, source=source, root=root, target=target, name="one.json")
    _managed_entry(d, source=source, root=root, target=target, name="two.json")
    (d / "bad.json").write_text("[]", "utf-8")

    report = _scan(d, monkeypatch, _active_report(source, root))
    assert len(report.active_configs) == 1
    assert {finding.reason for finding in report.findings} == {
        "duplicate", "invalid-entry"
    }
    monkeypatch.setattr(
        cfg,
        "_CONFIG_D_WARNING_TRACKER",
        WarningTracker(limit=1, repeat_after_seconds=3600),
    )
    with caplog.at_level(logging.WARNING, logger="agent-codespaces"):
        cfg._warn_config_dropin_findings(report)
    assert sum("reason=" in row.message for row in caplog.records) == 1
    assert any("additional findings suppressed" in row.message for row in caplog.records)
    caplog.clear()
    cfg._warn_config_dropin_findings(report)
    assert not caplog.records


def test_doctor_human_and_json_report_same_config_findings(
    tmp_path, monkeypatch, capsys
):
    from agent_codespaces import __main__ as main

    d = _config_d(tmp_path, monkeypatch)
    (d / "bad.json").write_text("[]", "utf-8")
    report = _scan(
        d, monkeypatch, ActivationReport(ScanAuthority.COMPLETE, {})
    )
    plugin_report = ConfigDropinRegistryReport(
        snapshot=ScanSnapshot(
            registry=cfg.PLUGIN_CONFIG_REGISTRY_NAME,
            authority=ScanAuthority.COMPLETE,
            decisions={},
            findings=(),
        ),
        active_entries={},
    )
    reports = ConfigProviderReports(
        active_plugins=plugin_report,
        config_d=report,
    )
    monkeypatch.setattr(main, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(main, "_gh_auth_preflight", lambda: ["gh auth missing scope"])

    assert main._cmd_doctor() == 1
    human = capsys.readouterr()
    assert "invalid-entry" in human.err and "gh auth missing scope" in human.err

    assert main.main(["doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["gh"]["findings"] == ["gh auth missing scope"]
    assert payload["plugin_manifests"]["registry"] == "plugin-manifests"
    assert payload["plugin_manifests"]["authority"] == "complete"
    assert payload["config_d"]["registry"] == "config.d"
    assert payload["config_d"]["authority"] == "complete"
    assert [finding["reason"] for finding in payload["config_d"]["findings"]] == [
        "invalid-entry"
    ]
    assert payload["config_d"]["findings"][0]["remedy"]


def test_doctor_exposes_active_plugin_declaration_identity_path_and_reason(
    tmp_path, monkeypatch, capsys
):
    from agent_codespaces import __main__ as main

    source, root, _target = _declared_plugin(tmp_path, config_body=None)
    monkeypatch.setattr(cfg, "resolve_active_plugins", lambda: _active_report(source, root))
    monkeypatch.setattr(cfg, "config_d_dir", lambda: tmp_path / "config.d")
    monkeypatch.setattr(cfg, "_PLUGIN_CONFIG_LAST_KNOWN", {})
    reports = cfg.scan_config_providers()
    monkeypatch.setattr(main, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(main, "_gh_auth_preflight", lambda: [])

    assert main._cmd_doctor(json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    finding = payload["plugin_manifests"]["findings"][0]
    assert finding["owner"] == source
    assert finding["entry"] == str(root / "plugin.json")
    assert finding["reason"] == "missing-target"
    assert Path(finding["target"]).parts[-3:] == (
        "references",
        "agent-codespaces",
        "config.yaml",
    )


@pytest.mark.parametrize(
    ("script_name", "command"),
    [
        ("write-config-dropin.ps1", lambda script: [
            shutil.which("pwsh") or shutil.which("powershell"),
            "-NoProfile", "-File", str(script),
        ]),
        ("write-config-dropin.sh", lambda script: [
            shutil.which("bash"), str(script)
        ]),
    ],
)
def test_writer_helpers_emit_schema_v1_provenance(
    tmp_path, script_name, command
):
    executable = command(Path(script_name))[0]
    if executable is None:
        pytest.skip(f"{script_name} interpreter is unavailable")
    if script_name.endswith(".sh") and os.name == "nt":
        pytest.skip("a Windows WSL launcher cannot safely execute native paths")
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    script = Path(__file__).parents[1] / "scripts" / script_name
    env = os.environ | {"AGENT_HOME": str(tmp_path / "home")}
    result = subprocess.run(
        [
            *command(script),
            "sample-harness@example-marketplace",
            str(root),
            str(target),
        ],
        capture_output=True,
        check=True,
        text=True,
        env=env,
    )
    entry = Path(result.stdout.strip())
    assert entry.is_file()
    assert json.loads(entry.read_text("utf-8")) == {
        "schema_version": 1,
        "plugin": "sample-harness@example-marketplace",
        "plugin_root": str(root.resolve()),
        "target": str(target.resolve()),
    }


@pytest.mark.parametrize(
    ("script_name", "command"),
    [
        ("write-config-dropin.ps1", lambda script: [
            shutil.which("pwsh") or shutil.which("powershell"),
            "-NoProfile", "-File", str(script),
        ]),
        ("write-config-dropin.sh", lambda script: [
            shutil.which("bash"), str(script)
        ]),
    ],
)
def test_writer_helpers_reject_target_outside_plugin_root(
    tmp_path, script_name, command
):
    executable = command(Path(script_name))[0]
    if executable is None:
        pytest.skip(f"{script_name} interpreter is unavailable")
    if script_name.endswith(".sh") and os.name == "nt":
        pytest.skip("a Windows WSL launcher cannot safely execute native paths")
    root = tmp_path / "plugin"
    root.mkdir()
    target = _plugin_config(tmp_path / "outside")
    script = Path(__file__).parents[1] / "scripts" / script_name
    result = subprocess.run(
        [
            *command(script),
            "sample-harness@example-marketplace",
            str(root),
            str(target),
        ],
        capture_output=True,
        text=True,
        env=os.environ | {"AGENT_HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 2
    assert "target must be contained by plugin root" in result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX shell execution requires POSIX paths"
)
def test_posix_writer_fallback_requires_existing_regular_target(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "write-config-dropin.sh"
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    fallback_bin = tmp_path / "no-realpath-bin"
    fallback_bin.mkdir()
    for name in ("dirname", "basename", "mkdir", "sed", "rm", "mv"):
        executable = shutil.which(name)
        if executable is None:
            pytest.skip(f"{name} is unavailable")
        (fallback_bin / name).symlink_to(executable)

    root = tmp_path / "plugin"
    target = _plugin_config(root)
    env = os.environ | {
        "AGENT_HOME": str(tmp_path / "home"),
        "PATH": str(fallback_bin),
    }
    result = subprocess.run(
        [
            bash,
            str(script),
            "sample-harness@example-marketplace",
            str(root),
            str(target),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(Path(result.stdout.strip()).read_text("utf-8"))["target"] == str(
        target.resolve()
    )

    missing = subprocess.run(
        [
            bash,
            str(script),
            "sample-harness@example-marketplace",
            str(root),
            str(root / "gone.yaml"),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert missing.returncode == 2
    assert "existing regular file" in missing.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX shell execution requires POSIX paths"
)
@pytest.mark.parametrize("field", ["plugin", "root", "target"])
def test_posix_writer_rejects_control_characters(tmp_path, field):
    script = Path(__file__).parents[1] / "scripts" / "write-config-dropin.sh"
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    args = ["sample-harness@example-marketplace", str(root), str(target)]
    if field == "plugin":
        args[0] = "sample\x01-harness@example-marketplace"
    elif field == "root":
        args[1] = f"{root}\ncontrol"
    else:
        args[2] = f"{target}\ncontrol"

    result = subprocess.run(
        [bash, str(script), *args],
        capture_output=True,
        text=True,
        env=os.environ | {"AGENT_HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 2
    assert "must not contain control characters" in result.stderr


@pytest.mark.parametrize("field", ["plugin", "root", "target"])
def test_powershell_writer_rejects_control_characters(tmp_path, field):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    script = Path(__file__).parents[1] / "scripts" / "write-config-dropin.ps1"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    args = ["sample-harness@example-marketplace", str(root), str(target)]
    if field == "plugin":
        args[0] = "sample\x01-harness@example-marketplace"
    elif field == "root":
        args[1] = f"{root}\ncontrol"
    else:
        args[2] = f"{target}\ncontrol"

    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(script), *args],
        capture_output=True,
        text=True,
        env=os.environ | {"AGENT_HOME": str(tmp_path / "home")},
    )
    assert result.returncode == 2
    assert "must not contain control characters" in result.stderr


@pytest.mark.parametrize("control", ["\x7f", "\x85"])
def test_powershell_writer_rejects_del_and_c1_controls_without_publishing(
    tmp_path, control
):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("PowerShell is unavailable")
    script = Path(__file__).parents[1] / "scripts" / "write-config-dropin.ps1"
    root = tmp_path / "plugin"
    target = _plugin_config(root)
    home = tmp_path / "home"

    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-File",
            str(script),
            "sample-harness@example-marketplace",
            str(root),
            f"{target}{control}",
        ],
        capture_output=True,
        text=True,
        env=os.environ | {"AGENT_HOME": str(home)},
    )

    assert result.returncode == 2
    assert "must not contain control characters" in result.stderr
    assert not (home / ".agent-codespaces" / "config.d").exists()
