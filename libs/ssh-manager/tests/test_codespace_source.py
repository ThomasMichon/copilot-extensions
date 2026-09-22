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


def test_fetch_gh_config_retries_transient_tunnel_reset(tmp_path, monkeypatch):
    # A dev-tunnel reset surfaces as rc!=0 with a "forcibly closed" marker; it
    # must be retried (not raised) so a single reset can't fail the connect.
    import ssh_manager.codespace_source as mod

    class _R:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls = []

    def fake_run(args, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _R(1, stderr="wsarecv: An existing connection was forcibly closed by the remote host.")
        return _R(0, stdout=_RAW)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    src = _CCS("cs-x", config_dir=tmp_path)
    raw = src._fetch_gh_config()
    assert raw == _RAW
    assert len(calls) == 2  # retried once past the reset


def test_fetch_gh_config_raises_on_genuine_error(tmp_path, monkeypatch):
    import ssh_manager.codespace_source as mod
    import pytest

    class _R:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls = []

    def fake_run(args, **kw):
        calls.append(1)
        return _R(1, stderr="unknown codespace: not-a-real-cs")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    src = _CCS("cs-x", config_dir=tmp_path)
    with pytest.raises(RuntimeError, match="gh codespace ssh --config failed"):
        src._fetch_gh_config()
    assert len(calls) == 1  # a genuine error is not retried

