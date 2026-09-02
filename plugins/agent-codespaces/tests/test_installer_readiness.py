from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

from agent_codespaces import __main__ as cli
from agent_codespaces.config import (
    CodespacesConfig,
    CredentialsConfig,
    CredentialSourceConfig,
)
from agent_codespaces.installer_readiness import emit, evaluate


def _provider_reports(*, plugin_findings=(), active_configs=(), config_d_findings=()):
    return SimpleNamespace(
        active_plugins=SimpleNamespace(
            active_configs=active_configs,
            findings=plugin_findings,
        ),
        config_d=SimpleNamespace(findings=config_d_findings),
        active_configs=active_configs,
    )


def test_no_configuration_is_empty_without_requiring_live_instance(capsys):
    result = evaluate(
        auth_findings=[],
        registry_findings=[],
        config_issues=[],
        configured=False,
    )

    assert result["state"] == "configuration-empty"
    assert "live CodeSpace is not required" in result["detail"]
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_valid_configuration_is_ready_without_instance_probe():
    result = evaluate(
        auth_findings=[],
        registry_findings=[],
        config_issues=[],
        configured=True,
    )

    assert result["state"] == "ready"
    assert "live CodeSpace instance is not required" in result["detail"]


def test_superseded_pointer_is_a_nonblocking_readiness_advisory():
    result = evaluate(
        auth_findings=[],
        registry_findings=[],
        registry_advisories=["provider pointer: superseded"],
        config_issues=[],
        configured=True,
    )

    assert result["state"] == "ready"
    assert "provider pointer: superseded" in result["detail"]


def test_runtime_prerequisite_failure_is_not_configuration_empty(capsys):
    result = evaluate(
        auth_findings=["gh CLI not found"],
        registry_findings=[],
        config_issues=[],
        configured=False,
    )

    assert result["state"] == "failed"
    assert "agent-codespaces doctor" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_payload_command_uses_read_only_health_surfaces(monkeypatch, capsys):
    reports = _provider_reports()
    merged_reports = []
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(
        cli,
        "load_merged_config",
        lambda *, provider_reports: (
            merged_reports.append(provider_reports) or CodespacesConfig()
        ),
    )
    monkeypatch.setattr(cli, "load_adopted_repos", lambda: [])

    assert cli._cmd_installer_readiness() == 0
    assert merged_reports == [reports]
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"


def test_adopted_standard_repo_without_supplemental_config_is_ready(
    monkeypatch, capsys
):
    reports = _provider_reports()
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(
        cli, "load_merged_config", lambda **_: CodespacesConfig(),
    )
    monkeypatch.setattr(
        cli,
        "load_adopted_repos",
        lambda: [SimpleNamespace(path=Path("standard-repo"))],
    )

    assert cli._cmd_installer_readiness() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_malformed_supplemental_config_still_fails(monkeypatch, capsys):
    reports = _provider_reports()
    merged = CodespacesConfig(
        source_paths=[Path("configured-repo")],
        credentials=CredentialsConfig(
            sources={
                "github": CredentialSourceConfig(enabled=True),
            }
        ),
    )
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(cli, "load_merged_config", lambda **_: merged)
    monkeypatch.setattr(cli, "load_adopted_repos", lambda: [])

    assert cli._cmd_installer_readiness() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "no allowed_hosts" in result["detail"]


def test_active_plugin_declaration_finding_fails_readiness(monkeypatch, capsys):
    finding = SimpleNamespace(
        owner="sample-harness@example-marketplace",
        entry=Path("plugin") / "plugin.json",
        reason="missing-target",
        target=Path("plugin") / "references" / "config.yaml",
        remedy="Fix codespaceConfig in plugin.json.",
    )
    reports = _provider_reports(plugin_findings=(finding,))
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(
        cli, "load_merged_config", lambda **_: CodespacesConfig(),
    )
    monkeypatch.setattr(cli, "load_adopted_repos", lambda: [])

    assert cli._cmd_installer_readiness() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "sample-harness@example-marketplace" in result["detail"]
    assert "missing-target" in result["detail"]


def test_stale_pointer_for_active_declaration_does_not_block_readiness(
    monkeypatch, capsys
):
    owner = "sample-harness@example-marketplace"
    active = SimpleNamespace(owner=owner)
    stale = SimpleNamespace(
        owner=owner,
        entry=Path("config.d") / "sample.json",
        reason="missing-target",
        target=Path("old-plugin") / "config.yaml",
        remedy="Remove the stale compatibility pointer.",
    )
    reports = _provider_reports(
        active_configs=(active,),
        config_d_findings=(stale,),
    )
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_providers", lambda: reports)
    monkeypatch.setattr(
        cli,
        "load_merged_config",
        lambda **_: CodespacesConfig(source_paths=[Path("plugin-config")]),
    )
    monkeypatch.setattr(cli, "load_adopted_repos", lambda: [])

    assert cli._cmd_installer_readiness() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready"
    assert owner in result["detail"]
    assert "missing-target" in result["detail"]
