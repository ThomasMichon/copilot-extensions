"""Tests for the trusted-container OpenSSH transport."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_containers import ssh_transport as transport


def test_bootstrap_commands_never_inherit_acp_stdin(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport.subprocess, "run", fake_run)
    transport._run(["docker", "inspect", "repo-1"])
    assert seen["stdin"] is subprocess.DEVNULL
    assert "input" not in seen


def test_prepare_ssh_config_uses_docker_only_as_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, "_SSH_DIR", tmp_path)
    monkeypatch.setattr(
        transport,
        "_ensure_host_key",
        lambda: (tmp_path / "id_ed25519", "ssh-ed25519 AAAA"),
    )
    monkeypatch.setattr(transport, "_install_authorized_key", lambda *args: None)
    monkeypatch.setattr(transport, "_container_id", lambda name: "a" * 64)

    config = transport.prepare_ssh_config("repo-1", "vscode")

    text = Path(config.config_file).read_text(encoding="utf-8")
    assert "ProxyCommand docker exec -i -u root repo-1 /usr/sbin/sshd -i -e" in text
    assert "StrictHostKeyChecking accept-new" in text
    assert config.host_alias.endswith("-" + ("a" * 12))
    assert config.user == "vscode"


@pytest.mark.parametrize("container,user", [
    ("repo-1;whoami", "vscode"),
    ("repo-1", "vscode;whoami"),
])
def test_prepare_ssh_config_rejects_shell_metacharacters(container, user):
    with pytest.raises(RuntimeError, match="Unsafe"):
        transport.prepare_ssh_config(container, user)


def test_build_remote_command_references_env_file_not_values():
    command = transport.build_remote_command(
        "copilot --acp --stdio",
        "/home/vscode/.agent-containers/launch/abc.env",
    )
    assert ". /home/vscode/.agent-containers/launch/abc.env" in command
    assert "rm -f /home/vscode/.agent-containers/launch/abc.env" in command
    assert "copilot --acp --stdio" in command


def test_authorized_key_is_sent_over_attached_stdin(monkeypatch):
    calls = []

    def fake_run(args, *, input_text=None, timeout=30.0):
        calls.append((args, input_text))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport, "_run", fake_run)
    transport._install_authorized_key("repo-1", "vscode", "ssh-ed25519 AAAA")

    install_args, key_input = calls[-1]
    assert install_args[:4] == ["docker", "exec", "-i", "-u"]
    assert key_input == "ssh-ed25519 AAAA\n"
    assert all("ssh-ed25519 AAAA" not in arg for arg in install_args)


def test_container_environment_preserves_path_without_session_identity(monkeypatch):
    monkeypatch.setattr(
        transport,
        "_run",
        lambda args: SimpleNamespace(
            returncode=0,
            stdout="PATH=/custom/bin\0HOME=/home/vscode\0TOKEN=value\0",
            stderr="",
        ),
    )
    assert transport.container_environment("repo-1", "vscode") == {
        "PATH": "/custom/bin",
        "TOKEN": "value",
    }


def test_write_remote_env_sends_secrets_over_stdin(monkeypatch):
    seen = {}

    def fake_run(args, *, input_text=None, timeout=30.0):
        seen["args"] = args
        seen["input"] = input_text
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(transport, "_run", fake_run)
    monkeypatch.setattr(transport, "_remote_home", lambda *args: "/home/vscode")
    monkeypatch.setattr(transport.uuid, "uuid4", lambda: SimpleNamespace(hex="abc"))

    path = transport.write_remote_env(
        "repo-1",
        "vscode",
        {"GH_TOKEN": "secret-value", "LC_GIT_CREDENTIAL_RELAY": "54321"},
    )

    assert path == "/home/vscode/.agent-containers/launch/abc.env"
    assert "secret-value" in seen["input"]
    assert all("secret-value" not in arg for arg in seen["args"])


def test_build_ssh_command_uses_shared_builder(monkeypatch):
    config = SimpleNamespace()
    monkeypatch.setattr(transport.shutil, "which", lambda name: "ssh")
    monkeypatch.setattr(
        transport,
        "build_remote_exec_args",
        lambda cfg, cmd: ["ssh", "target", cmd],
    )
    assert transport.build_ssh_command(config, "echo ok") == [
        "ssh", "target", "echo ok"
    ]
