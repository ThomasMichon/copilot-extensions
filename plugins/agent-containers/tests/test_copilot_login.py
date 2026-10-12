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

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_result = types.SimpleNamespace(returncode=1)
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_result, restore_result],
    ) as run_mock:
        with pytest.raises(RuntimeError, match="login did not complete"):
            cmd_copilot_login(_args(account="explicit-user"))

    login_argv = run_mock.call_args_list[1].args[0]
    assert "explicit-user" not in login_argv  # account isn't passed to `copilot login`
    assert login_argv[:5] == ["docker", "exec", "-it", "-u", "vscode"]
    assert login_argv[-5:] == ["copilot", "login", "--device-code", "--host", "https://github.com"]


def test_fails_when_the_in_container_login_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_result = types.SimpleNamespace(returncode=1)
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_result, restore_result],
    ):
        with pytest.raises(RuntimeError, match="login did not complete"):
            cmd_copilot_login(_args())


def test_fails_when_the_container_config_json_cannot_be_read_back(monkeypatch):
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_ok = types.SimpleNamespace(returncode=0)
    read_failed = types.SimpleNamespace(returncode=1, stdout="")
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_ok, read_failed, restore_result],
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

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(returncode=0, stdout='{"authTokens": {}}')
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_ok, read_ok, restore_result],
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

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(
        returncode=0,
        stdout='{"authTokens": {"https://github.com:inference-user": {"token": "gho_extracted"}}}',
    )
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_ok, read_ok, restore_result],
    ) as run_mock:
        rc = cmd_copilot_login(_args())

    assert rc == 0
    assert imported == [("inference-user", "gho_extracted")]
    assert "gho_extracted" not in capsys.readouterr().out
    # Cleanup ran: with no prior snapshot, the container's freshly-written
    # token is removed rather than left behind.
    restore_argv = run_mock.call_args_list[-1].args[0]
    assert "rm -f" in restore_argv[-1]


def test_successful_login_restores_a_pre_existing_container_config(monkeypatch, capsys):
    """If the container already had its own config.json before the login
    (e.g. a prior session), that exact content is restored afterward --
    never silently replaced by the freshly logged-in document."""
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())
    monkeypatch.setattr(
        "agent_containers.resolver.copilot_cli_token_from_json_text",
        lambda account, raw: "gho_extracted",
    )
    monkeypatch.setattr(
        "agent_containers.resolver.import_copilot_cli_token",
        lambda account, token: None,
    )

    prior_config = '{"authTokens": {"https://github.com:git-user": {"token": "gho_old"}}}'
    snapshot_result = types.SimpleNamespace(returncode=0, stdout=prior_config)
    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(returncode=0, stdout='{"authTokens": {}}')
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_ok, read_ok, restore_result],
    ) as run_mock:
        rc = cmd_copilot_login(_args())

    assert rc == 0
    restore_call = run_mock.call_args_list[-1]
    assert restore_call.kwargs["input"] == prior_config
    assert "cat >" in restore_call.args[0][-1]


def test_cleanup_runs_even_when_login_fails(monkeypatch):
    """The restore/cleanup step must run on every exit path, not only the
    success path."""
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_result = types.SimpleNamespace(returncode=1)
    restore_result = types.SimpleNamespace(returncode=0)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_result, restore_result],
    ) as run_mock:
        with pytest.raises(RuntimeError):
            cmd_copilot_login(_args())

    assert run_mock.call_count == 3  # snapshot, login, restore -- cleanup still ran


def test_restore_warns_when_restore_write_back_exits_nonzero(monkeypatch, capsys):
    """A nonzero exit from the in-container restore write-back means the
    pre-login config was NOT actually restored -- this must surface as a
    warning, not be silently treated as a successful cleanup."""
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())
    monkeypatch.setattr(
        "agent_containers.resolver.copilot_cli_token_from_json_text",
        lambda account, raw: "gho_extracted",
    )
    monkeypatch.setattr(
        "agent_containers.resolver.import_copilot_cli_token",
        lambda account, token: None,
    )

    prior_config = '{"authTokens": {"https://github.com:git-user": {"token": "gho_old"}}}'
    snapshot_result = types.SimpleNamespace(returncode=0, stdout=prior_config)
    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(returncode=0, stdout='{"authTokens": {}}')
    restore_failed = types.SimpleNamespace(returncode=1)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_ok, read_ok, restore_failed],
    ):
        rc = cmd_copilot_login(_args())

    assert rc == 0  # the login itself still succeeded
    stderr = capsys.readouterr().err
    assert "WARNING" in stderr
    assert "could not restore" in stderr


def test_restore_warns_when_cleanup_rm_exits_nonzero(monkeypatch, capsys):
    """Same check on the no-prior-snapshot (``rm -f``) cleanup path."""
    monkeypatch.setattr(
        "agent_containers.resolver.resolve_live_exec_target",
        lambda name, config: _target(),
    )
    monkeypatch.setattr("agent_containers.config.load_config", lambda: object())
    monkeypatch.setattr(
        "agent_containers.resolver.copilot_cli_token_from_json_text",
        lambda account, raw: "gho_extracted",
    )
    monkeypatch.setattr(
        "agent_containers.resolver.import_copilot_cli_token",
        lambda account, token: None,
    )

    snapshot_result = types.SimpleNamespace(returncode=0, stdout="")
    login_ok = types.SimpleNamespace(returncode=0)
    read_ok = types.SimpleNamespace(
        returncode=0,
        stdout='{"authTokens": {"https://github.com:inference-user": {"token": "gho_extracted"}}}',
    )
    rm_failed = types.SimpleNamespace(returncode=1)
    with patch(
        "agent_containers.copilot_login.subprocess.run",
        side_effect=[snapshot_result, login_ok, read_ok, rm_failed],
    ):
        rc = cmd_copilot_login(_args())

    assert rc == 0
    stderr = capsys.readouterr().err
    assert "WARNING" in stderr
    assert "could not restore" in stderr
