from __future__ import annotations

from types import SimpleNamespace

from agent_ssh import host_restore


def _setup(tmp_path, monkeypatch):
    payload = tmp_path / "agent-ssh"
    script = payload / "transports" / "dtssh" / "scripts" / "install-host.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    monkeypatch.setattr(host_restore.platform, "system", lambda: "Windows")
    monkeypatch.setattr(host_restore.shutil, "which", lambda _name: "pwsh")
    monkeypatch.setattr(host_restore.time, "sleep", lambda _seconds: None)
    return script


def test_dtssh_dry_run_uses_status(tmp_path, monkeypatch):
    script = _setup(tmp_path, monkeypatch)
    captured = {}

    def run(command, **kwargs):
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout="WARNING: host not running",
            stderr="",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is True
    assert result["healthy"] is False
    assert result["would_change"] is True
    assert result["applied"] is False
    assert str(script) in captured["command"]
    assert "status" in captured["command"]
    assert "-SkipLogin" not in captured["command"]


def test_dtssh_apply_uses_idempotent_install_without_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    captured = {}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        captured["command"] = command
        if "status" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "dtssh host healthy\nwatchdog running\n"
                    "tunnel example: 1 host connection(s)"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="installed", stderr="")

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is True
    assert result["applied"] is True
    assert any("install" in command for command in calls)
    assert any("-SkipLogin" in command for command in calls)
    assert result["healthy"] is True


def test_dtssh_restore_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr(host_restore.platform, "system", lambda: "Linux")

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is False
    assert "only on Windows" in result["error"]


def test_dtssh_restore_blocks_without_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        host_restore.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="Not logged in",
            stderr="",
        ),
    )

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is False
    assert result["blocked"] == "authentication"


def test_payload_root_uses_runtime_marker(tmp_path, monkeypatch):
    home = tmp_path / "home"
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(
        '{"name": "agent-ssh"}',
        encoding="utf-8",
    )
    marker = home / ".agent-ssh" / "payload-dir"
    marker.parent.mkdir(parents=True)
    marker.write_text(str(payload), encoding="utf-8")
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(host_restore.Path, "home", lambda: home)

    assert host_restore._payload_root() == payload.resolve()


def test_zero_tunnel_connections_are_unhealthy():
    assert not host_restore._healthy_status(
        "host running\nwatchdog running\n"
        "tunnel example: 0 host connection(s)"
    )


def test_login_preflight_uses_dtssh_sibling_devtunnel(tmp_path, monkeypatch):
    local = tmp_path / "local"
    devtunnel = local / "dtssh" / "bin" / "devtunnel.exe"
    devtunnel.parent.mkdir(parents=True)
    devtunnel.write_text("", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(host_restore.shutil, "which", lambda _name: None)
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout='{"status": "Logged in"}',
            stderr="",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    logged_in, error = host_restore._login_status()

    assert logged_in is True
    assert error == ""
    assert captured["command"][0] == str(devtunnel)
