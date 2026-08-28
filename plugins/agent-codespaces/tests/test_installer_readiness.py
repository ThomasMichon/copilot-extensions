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
    report = SimpleNamespace(findings=(), active_configs=())
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_dropin_registry", lambda: report)
    monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
    monkeypatch.setattr(cli, "load_adopted_repos", lambda: [])

    assert cli._cmd_installer_readiness() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"


def test_adopted_standard_repo_without_supplemental_config_is_ready(
    monkeypatch, capsys
):
    report = SimpleNamespace(findings=(), active_configs=())
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_dropin_registry", lambda: report)
    monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
    monkeypatch.setattr(
        cli,
        "load_adopted_repos",
        lambda: [SimpleNamespace(path=Path("standard-repo"))],
    )

    assert cli._cmd_installer_readiness() == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_malformed_supplemental_config_still_fails(monkeypatch, capsys):
    report = SimpleNamespace(findings=(), active_configs=())
    merged = CodespacesConfig(
        source_paths=[Path("configured-repo")],
        credentials=CredentialsConfig(
            sources={
                "github": CredentialSourceConfig(enabled=True),
            }
        ),
    )
    monkeypatch.setattr(cli, "_gh_auth_preflight", lambda: [])
    monkeypatch.setattr(cli, "scan_config_dropin_registry", lambda: report)
    monkeypatch.setattr(cli, "load_merged_config", lambda: merged)
    monkeypatch.setattr(cli, "load_adopted_repos", lambda: [])

    assert cli._cmd_installer_readiness() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert "no allowed_hosts" in result["detail"]
