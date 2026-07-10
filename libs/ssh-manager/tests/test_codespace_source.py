"""Tests for the shared CodespaceConfigSource (gh codespace ssh --config)."""

from __future__ import annotations

from ssh_manager import CodespaceConfigSource
from ssh_manager.codespace_source import CodespaceConfigSource as _CCS

_RAW = """Host cs.fluffy-parakeet.org_repo
    User vscode
    ProxyCommand gh cs ssh -c fluffy-parakeet --stdio -- -i /home/u/.ssh/cs
    UserKnownHostsFile=/dev/null
    StrictHostKeyChecking no
    LogLevel quiet
    ControlMaster auto
    IdentityFile /home/u/.ssh/codespaces.auto
"""


def test_parse_extracts_host_user_proxy_identity():
    parsed = _CCS._parse_ssh_config(_RAW)
    assert parsed["host_alias"] == "cs.fluffy-parakeet.org_repo"
    assert parsed["user"] == "vscode"
    assert parsed["proxy_command"].startswith("gh cs ssh")
    assert parsed["identity_file"].endswith("codespaces.auto")
    # ControlMaster is stripped; other options are retained
    assert "ControlMaster" not in parsed["extra_options"]
    assert parsed["extra_options"]["StrictHostKeyChecking"] == "no"
    assert parsed["extra_options"]["LogLevel"] == "quiet"


def test_write_config_file(tmp_path):
    src = _CCS("my-codespace", config_dir=tmp_path)
    path = src._write_config_file(_RAW)
    assert path.exists()
    assert path.read_text() == _RAW
    assert path.parent == tmp_path


def test_refresh_builds_ssh_config(tmp_path, monkeypatch):
    src = CodespaceConfigSource("cs-x", config_dir=tmp_path)
    monkeypatch.setattr(src, "_fetch_gh_config", lambda: _RAW)
    cfg = src.refresh()
    assert cfg.user == "vscode"
    assert cfg.config_file.endswith(".config")
    assert cfg.proxy_command.startswith("gh cs ssh")
    # cached
    assert src.get_ssh_config() is cfg


def test_parse_missing_host_raises():
    import pytest
    with pytest.raises(RuntimeError, match="Could not parse Host"):
        _CCS._parse_ssh_config("User vscode\n")
