"""Tests for ``agent-containers copilot-login`` (the manual device-code-in-
container Copilot CLI bootstrap fallback)."""

from __future__ import annotations

import argparse
import types
from unittest.mock import patch

import pytest

from agent_containers.config import RESTRICTED_PROFILE
from agent_containers.copilot_login import cmd_copilot_login


def _args(name="repo-1", account=None):
    return argparse.Namespace(name=name, account=account)


def _target(*, profile="trusted", copilot_cli_account="inference-user"):
    config = types.SimpleNamespace(
        copilot_cli_account_for=lambda fleet: copilot_cli_account,
    )
    fleet = types.SimpleNamespace()
    return types.SimpleNamespace(
        config=config,
        fleet=fleet,
        actual_profile=profile,
        container_id="repo-1",
        user="vscode",
    )


def test_rejects_a_restricted_container(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(profile=RESTRICTED_PROFILE),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    with pytest.raises(RuntimeError, match="restricted container"):
        cmd_copilot_login(_args())


def test_requires_an_account_when_none_is_configured(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(copilot_cli_account=None),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    with pytest.raises(RuntimeError, match="no --account given"):
        cmd_copilot_login(_args(account=None))


def test_explicit_account_flag_overrides_the_fleet_default(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(copilot_cli_account="fleet-default-user"),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    login_run = types.SimpleNamespace(returncode=1)
    with patch(
        "agent_containers.copilot_login.subprocess.run", return_value=login_run
    ) as run_mock:
        with pytest.raises(RuntimeError, match="login did not complete"):
            cmd_copilot_login(_args(account="explicit-user"))

    login_argv = run_mock.call_args_list[0].args[0]
    assert "explicit-user" not in login_argv  # account isn't passed to `copilot login`
    assert login_argv[:5] == ["docker", "exec", "-it", "-u", "vscode"]
    assert login_argv[-5:] == ["copilot", "login", "--device-code", "--host", "https://github.com"]


def test_fails_when_the_in_container_login_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    with patch(
        "agent_containers.copilot_login.subprocess.run",
        return_value=types.SimpleNamespace(returncode=1),
    ):
        with pytest.raises(RuntimeError, match="login did not complete"):
            cmd_copilot_login(_args())


def test_fails_when_the_container_config_json_cannot_be_read_back(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    login_ok = types.SimpleNamespace(returncode=0)
    read_failed = types.SimpleNamespace(returncode=1, stdout="")
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[login_ok, read_failed],
    ):
        with pytest.raises(RuntimeError, match="could not be read back"):
            cmd_copilot_login(_args())


def test_fails_when_no_token_for_the_account_is_found_after_login(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())
    monkeypatch.setattr(
        "agent_containers.resolver.copilot_cli_token_from_json_text",
        lambda account, raw: None,
    )

    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(returncode=0, stdout='{"authTokens": {}}')
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[login_ok, read_ok],
    ):
        with pytest.raises(RuntimeError, match="no token for"):
            cmd_copilot_login(_args())


def test_successful_login_imports_the_extracted_token(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())
    monkeypatch.setattr(
        "agent_containers.resolver.copilot_cli_token_from_json_text",
        lambda account, raw: "gho_extracted" if account == "inference-user" else None,
    )
    imported = []
    monkeypatch.setattr(
        "agent_containers.resolver.import_copilot_cli_token",
        lambda account, token: imported.append((account, token)),
    )

    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(
        returncode=0,
        stdout='{"authTokens": {"https://github.com:inference-user": {"token": "gho_extracted"}}}',
    )
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[login_ok, read_ok],
    ):
        rc = cmd_copilot_login(_args())

    assert rc == 0
    assert imported == [("inference-user", "gho_extracted")]
    assert "gho_extracted" not in capsys.readouterr().out
