"""Tests for the CodespaceSource wrapper.

The gh-config fetch/parse now lives in ssh-manager (see
``ssh_manager.codespace_source`` + its ``test_codespace_source.py``); these tests
cover only the agent-codespaces wrapper: it is a ConfigSource, uses the
agent-codespaces SSH_CONFIG_DIR, and delegates fetch/parse to the shared source.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_codespaces.codespace_config import SSH_CONFIG_DIR, CodespaceSource

SAMPLE_GH_CONFIG = """\
Host cs.fluffy-parakeet-abc123.org-my--repo
    User codespace
    ProxyCommand gh cs ssh -c fluffy-parakeet-abc123 --stdio -- -i /tmp/key
    UserKnownHostsFile=/dev/null
    StrictHostKeyChecking no
    LogLevel quiet
    ControlMaster auto
    IdentityFile /tmp/key
"""


def test_implements_protocol():
    from ssh_manager import ConfigSource
    assert isinstance(CodespaceSource("test-cs"), ConfigSource)


def test_uses_agent_codespaces_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_codespaces.codespace_config.SSH_CONFIG_DIR", tmp_path
    )
    src = CodespaceSource("cs-x")
    assert src._config_dir == tmp_path


@patch("ssh_manager.codespace_source.subprocess.run")
def test_refresh_delegates_to_shared_parser(mock_run, tmp_path, monkeypatch):
    mock_run.return_value = MagicMock(returncode=0, stdout=SAMPLE_GH_CONFIG, stderr="")
    monkeypatch.setattr(
        "agent_codespaces.codespace_config.SSH_CONFIG_DIR", tmp_path
    )
    src = CodespaceSource("fluffy-parakeet-abc123")
    cfg = src.refresh()
    assert cfg.host_alias == "cs.fluffy-parakeet-abc123.org-my--repo"
    assert cfg.user == "codespace"
    assert "gh cs ssh" in (cfg.proxy_command or "")
    assert cfg.config_file is not None
    # config file lands under the agent-codespaces dir
    assert str(tmp_path) in cfg.config_file


@patch("ssh_manager.codespace_source.subprocess.run")
def test_get_ssh_config_caches(mock_run, tmp_path, monkeypatch):
    mock_run.return_value = MagicMock(returncode=0, stdout=SAMPLE_GH_CONFIG, stderr="")
    monkeypatch.setattr(
        "agent_codespaces.codespace_config.SSH_CONFIG_DIR", tmp_path
    )
    src = CodespaceSource("fluffy-parakeet-abc123")
    c1 = src.get_ssh_config()
    c2 = src.get_ssh_config()
    assert mock_run.call_count == 1
    assert c1 is c2


def test_default_config_dir_is_agent_codespaces():
    # SSH_CONFIG_DIR is the agent-codespaces runtime ssh dir (back-compat).
    assert SSH_CONFIG_DIR.name == "ssh"
    src = CodespaceSource("cs-y")
    assert src._config_dir == SSH_CONFIG_DIR